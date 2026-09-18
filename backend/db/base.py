"""
Engine / session factory and schema initialisation.

One DATABASE_URL drives everything: unset it and you get a local SQLite
setup under backend/data/; point it at RDS Postgres and the same code and
migrations run there. init_db() brings any blank database to the current
schema via alembic and seeds the default customer.

Real per-layer schema separation (see db/models.py): every model
declares schema="bronze"|"silver"|"gold" (or omits it for the default/
app schema). Postgres does this natively via CREATE SCHEMA. SQLite has
no schema concept, so register_sqlite_attach() fakes it with ATTACH
DATABASE — data/app.db (the main connection) gets data/bronze.db,
data/silver.db, data/gold.db joined on as aliases on every new
connection, so "gold.bills" addresses the right physical file either
way. This is entirely invisible to Postgres: the listener only ever
registers for the sqlite dialect.
"""

import os
from pathlib import Path

import env_file  # noqa: F401  (loads backend/.env before any setting is read)
from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("RECON_DATA_DIR", str(BACKEND_DIR / "data")))

# Postgres goes through psycopg 3. SQLAlchemy maps a bare postgresql:// to
# psycopg2, which is not installed, so both spellings are pinned here.
_PG_PREFIXES = ("postgresql://", "postgres://")
_PG_DRIVER = "postgresql+psycopg://"


def _database_url() -> str:
    """Resolution order:
      1. DATABASE_URL, verbatim (bare postgresql:// pinned to psycopg 3)
      2. DB_HOST + DB_USER/DB_PASSWORD/DB_NAME/DB_PORT/DB_SSLMODE — the
         shape ECS injects from a Secrets Manager secret. Built with
         URL.create so a password containing @ : / % stays intact.
      3. local SQLite under DATA_DIR
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        for prefix in _PG_PREFIXES:
            if url.startswith(prefix):
                return _PG_DRIVER + url[len(prefix):]
        return url
    host = os.environ.get("DB_HOST")
    if host:
        sslmode = os.environ.get("DB_SSLMODE")
        return URL.create(
            "postgresql+psycopg",
            username=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASSWORD"),
            host=host,
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "recon"),
            query={"sslmode": sslmode} if sslmode else {},
        ).render_as_string(hide_password=False)
    return f"sqlite:///{DATA_DIR / 'app.db'}"


DATABASE_URL = _database_url()

# Arbitrary constant key for pg_advisory_lock: serialises init_db() across
# processes (several ECS tasks starting at once) on the same database.
_MIGRATION_LOCK_KEY = 72_310_945

# Sibling per-layer SQLite files, always co-located with DATA_DIR — the
# same root db.storage already uses for bronze blobs / run artifacts.
# Independent of whatever DATABASE_URL's main-file path/name is; meaning-
# less (never referenced) once DATABASE_URL points at Postgres.
LAYER_DB_FILES = {
    "bronze": DATA_DIR / "bronze.db",
    "silver": DATA_DIR / "silver.db",
    "gold": DATA_DIR / "gold.db",
}


def _attach_layer_databases(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    for alias, path in LAYER_DB_FILES.items():
        cursor.execute("ATTACH DATABASE ? AS " + alias, (str(path),))
    cursor.close()


def register_sqlite_attach(engine):
    """No-op for Postgres: schema="bronze" etc. maps to a real CREATE
    SCHEMA there, ATTACH is meaningless and must never fire against RDS."""
    if engine.dialect.name == "sqlite":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        event.listen(engine, "connect", _attach_layer_databases)
    return engine


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        kwargs = {}
        if DATABASE_URL.startswith("sqlite"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            # runs execute in a threadpool; sessions are per-request
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            # RDS failovers and idle-connection reaping leave dead pooled
            # connections behind; test each on checkout, recycle hourly
            kwargs["pool_pre_ping"] = True
            kwargs["pool_recycle"] = 3600
        _engine = create_engine(DATABASE_URL, **kwargs)
        register_sqlite_attach(_engine)
    return _engine


def SessionLocal():
    """Session factory (lazy so importing db never touches the DB)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def run_migrations_on_startup() -> bool:
    """RUN_MIGRATIONS_ON_STARTUP (default true). Deployed containers set it
    false and run `python -m db.migrate` once per release instead."""
    return os.environ.get("RUN_MIGRATIONS_ON_STARTUP", "true").strip().lower() \
        not in ("0", "false", "no", "off")


def init_db():
    """alembic upgrade head + idempotent seeding. Called on app startup
    (unless RUN_MIGRATIONS_ON_STARTUP=false) and by `python -m db.migrate`;
    safe to run repeatedly and on a blank database (SQLite or Postgres).
    On Postgres the whole thing runs under an advisory lock, so processes
    starting together take turns instead of racing the same migration."""
    if DATABASE_URL.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _upgrade_and_seed()
        return

    with get_engine().connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"),
                          {"k": _MIGRATION_LOCK_KEY})
        # session-level lock outlives the transaction; don't sit idle in
        # one for the length of the migration
        lock_conn.commit()
        try:
            _upgrade_and_seed()
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),
                              {"k": _MIGRATION_LOCK_KEY})
            lock_conn.commit()


def _upgrade_and_seed():
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    # alembic's config is a ConfigParser: a literal % (URL-encoded password
    # characters) must be doubled or interpolation raises
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    # never let alembic's fileConfig() touch the app's logging setup
    # (duplicate handlers + disabled loggers otherwise; see alembic/env.py)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    from logging_setup import get_logger
    from .seeds import seed_admin_user, seed_defaults
    with SessionLocal() as session:
        seed_defaults(session)
        # no-op unless ADMIN_EMAIL/ADMIN_PASSWORD are set and the users
        # table is empty; see db/seeds.py for why it never re-passwords
        seed_admin_user(session, get_logger(__name__))
        session.commit()
