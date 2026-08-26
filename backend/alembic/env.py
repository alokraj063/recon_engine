import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db.base import DATA_DIR, DATABASE_URL, register_sqlite_attach  # noqa: E402
from db.models import Base  # noqa: E402

if DATABASE_URL.startswith("sqlite"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    # Programmatic runs (init_db()) set configure_logger=False so alembic
    # never touches the app's logging config at all — fileConfig() both
    # attaches alembic.ini's own root console handler (duplicating every
    # line the app logs) and, with its default disable_existing_loggers=
    # True, permanently disables every logger not listed in alembic.ini
    # (Logger objects are cached singletons — once disabled, disabled for
    # the rest of the process). CLI `alembic` runs from a shell still get
    # alembic.ini's logging, with disable_existing_loggers=False as
    # belt-and-braces.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# One URL drives everything; the CLI and init_db() both land here.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,   # SQLite's limited ALTER support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # alembic builds its own engine, separate from db.base.get_engine()'s
    # singleton, so the sibling-file ATTACH has to be registered here too
    register_sqlite_attach(connectable)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_schemas=True,   # autogenerate must see bronze/silver/gold too
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
