"""
Optional local configuration from backend/.env (template: backend/.env.example).

Imported for its side effect at the top of every module that reads settings
from the environment at import time (db/base.py, logging_setup.py), so every
entry point — uvicorn, `python -m db.migrate`, alembic, scripts, the CLI —
sees the same values without each one remembering to load the file.

Rules:
  - real environment variables always win (override=False), so a shell
    export or an ECS task definition is never silently replaced by a file
  - deployed containers have no .env at all (.dockerignore keeps it out);
    ECS injects settings and Secrets Manager values as plain env vars
  - skipped under pytest: the DB-backed tests create and delete rows in
    whatever database is configured, so a developer's .env pointing at a
    shared database must never become the test target. Tests keep using
    explicit env vars only (RECON_DATA_DIR, DATABASE_URL), exactly as before.
"""

import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


def load_env_file() -> bool:
    """Load backend/.env if present. Returns whether a file was loaded."""
    if "pytest" in sys.modules or not ENV_FILE.is_file():
        return False
    from dotenv import load_dotenv
    return load_dotenv(ENV_FILE, override=False)


load_env_file()
