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

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("RECON_DATA_DIR", str(BACKEND_DIR / "data")))
DATABASE_URL = os.environ.get(
    "DATABASE_URL", f"sqlite:///{DATA_DIR / 'app.db'}"
)

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
        _engine = create_engine(DATABASE_URL, **kwargs)
        register_sqlite_attach(_engine)
    return _engine


def SessionLocal():
    """Session factory (lazy so importing db never touches the DB)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db():
    """alembic upgrade head + idempotent seeding. Called on app startup;
    safe to run repeatedly and on a blank database (SQLite or Postgres)."""
    from alembic import command
    from alembic.config import Config

    if DATABASE_URL.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
    # never let alembic's fileConfig() touch the app's logging setup
    # (duplicate handlers + disabled loggers otherwise; see alembic/env.py)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    from .seeds import seed_defaults
    with SessionLocal() as session:
        seed_defaults(session)
        session.commit()
