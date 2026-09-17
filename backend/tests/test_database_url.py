"""
DATABASE_URL resolution for deployment: an explicit URL wins, then the
DB_* parts ECS injects from Secrets Manager, then local SQLite. Pure
function over the environment — no database is touched.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from db import base  # noqa: E402

_VARS = ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER",
         "DB_PASSWORD", "DB_SSLMODE", "RUN_MIGRATIONS_ON_STARTUP")


@pytest.fixture
def env(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_to_local_sqlite(env):
    assert base._database_url() == f"sqlite:///{base.DATA_DIR / 'app.db'}"


@pytest.mark.parametrize("given", [
    "postgresql://u:p@db:5432/recon",
    "postgres://u:p@db:5432/recon",
])
def test_bare_postgres_url_pinned_to_psycopg3(env, given):
    env.setenv("DATABASE_URL", given)
    assert base._database_url() == "postgresql+psycopg://u:p@db:5432/recon"


def test_explicit_driver_and_sqlite_urls_untouched(env):
    env.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/recon")
    assert base._database_url() == "postgresql+psycopg://u:p@db/recon"
    env.setenv("DATABASE_URL", "sqlite:///elsewhere.db")
    assert base._database_url() == "sqlite:///elsewhere.db"


def test_database_url_beats_db_parts(env):
    env.setenv("DATABASE_URL", "sqlite:///explicit.db")
    env.setenv("DB_HOST", "ignored")
    assert base._database_url() == "sqlite:///explicit.db"


def test_db_parts_survive_a_hostile_password(env):
    password = "p@ss:w/rd%20#?"
    env.setenv("DB_HOST", "wabtec-recon-db.abc.ap-south-1.rds.amazonaws.com")
    env.setenv("DB_USER", "wabtecadmin")
    env.setenv("DB_PASSWORD", password)
    env.setenv("DB_NAME", "recon")
    env.setenv("DB_SSLMODE", "require")

    url = make_url(base._database_url())
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "wabtec-recon-db.abc.ap-south-1.rds.amazonaws.com"
    assert url.port == 5432
    assert url.username == "wabtecadmin"
    assert url.password == password
    assert url.database == "recon"
    assert url.query == {"sslmode": "require"}


def test_db_parts_defaults(env):
    env.setenv("DB_HOST", "localhost")
    url = make_url(base._database_url())
    assert (url.port, url.database, dict(url.query)) == (5432, "recon", {})


@pytest.mark.parametrize("value, expected", [
    (None, True), ("true", True), ("1", True), ("TRUE", True),
    ("false", False), ("0", False), ("no", False), (" Off ", False),
])
def test_run_migrations_on_startup_flag(env, value, expected):
    if value is not None:
        env.setenv("RUN_MIGRATIONS_ON_STARTUP", value)
    assert base.run_migrations_on_startup() is expected
