"""
One-off schema migration + seeding, for deployments where the app itself
starts with RUN_MIGRATIONS_ON_STARTUP=false:

    python -m db.migrate        (run from backend/)

On ECS this is the same image as the app with the command overridden, run
once per release before the service rolls. Exits non-zero on failure so the
task shows as failed.
"""

import sys

from logging_setup import configure_logging, get_logger

from .base import DATABASE_URL, init_db


def _redacted(url: str) -> str:
    from sqlalchemy.engine import make_url
    return make_url(url).render_as_string(hide_password=True)


def main() -> int:
    configure_logging()
    logger = get_logger("db.migrate")
    target = _redacted(DATABASE_URL)
    logger.info("db.migrate_started", extra={
        "event_type": "db.migrate_started", "details": {"database": target}})
    try:
        init_db()
    except Exception:
        logger.exception("db.migrate_failed", extra={
            "event_type": "db.migrate_failed", "details": {"database": target}})
        return 1
    logger.info("db.migrate_succeeded", extra={
        "event_type": "db.migrate_succeeded", "details": {"database": target}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
