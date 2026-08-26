"""
Shared logging setup for recon/, db/, and app/ — a standalone top-level
module (not part of any of those three packages) so importing it never
creates a recon -> db dependency. Two outputs, one root logger:
  - human text to stdout (local dev tailing)
  - JSON lines to a rotating file under data/logs/ (gitignored, same
    RECON_DATA_DIR-style env override pattern as db/base.py)

configure_logging() is called by real entry points only (app/main.py at
import time, recon/cli.py's main()) — never at db/ import time, never by
tests. That is the whole "don't spam log files under pytest" strategy:
if nothing calls configure_logging(), no handlers are ever attached, so
nothing is ever written to disk. WARNING+ records still reach stderr via
Python's built-in logging.lastResort handler either way (harmless, and
already what pytest captures per-test today).

configure_logging() always (re)installs its two handlers rather than
no-op'ing after the first call — verified empirically that this matters:
init_db() runs alembic migrations, and alembic/env.py calls
logging.config.fileConfig() on every migration run, which REPLACES
whatever handlers are already on the root logger with alembic.ini's own
(a plain stderr StreamHandler). So app/main.py's lifespan calls
configure_logging() a second time, after init_db(), to reclaim the root
logger — otherwise every request after the first startup silently stops
being logged, which is exactly what a live-reload session caught.

Correlation ids (request_id/customer_id/run_id) ride plain ContextVars.
Verified empirically that starlette's run_in_threadpool (via anyio's
copy_context()/context.run()) propagates the calling task's context into
the worker thread, and that this holds through @app.middleware("http")
too — so a value set in middleware or right after resolving a customer
is visible in every subsequent log line for that request, including ones
emitted from synchronous DB code running in the threadpool. No need to
thread ids through function signatures.
"""

import json
import logging
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("RECON_LOG_DIR", str(BACKEND_DIR / "data" / "logs")))
LOG_FILE = LOG_DIR / "app.log"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
customer_id_var: ContextVar[str] = ContextVar("customer_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")
_CTX_VARS = {"request_id": request_id_var, "customer_id": customer_id_var,
             "run_id": run_id_var}


class ContextFilter(logging.Filter):
    """Fills correlation attrs from the ContextVars — but only when the
    record doesn't already carry them: a caller that knows its ids (e.g.
    db/audit.py's record_event) passes them via `extra`, and those must
    win over the ambient context, which can lag (a run_id generated
    inside the very function emitting the record, for instance)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, var in _CTX_VARS.items():
            if not hasattr(record, key):
                setattr(record, key, var.get())
        return True


@contextmanager
def bind_context(**kwargs):
    """Scoped correlation binding with reset on exit. Each HTTP request is
    already its own asyncio Task with an independently copied Context, so
    this isn't load-bearing for cross-request isolation — it's just a
    tidy way to bind ids for a scoped block (e.g. a background job)."""
    tokens = {}
    try:
        for key, value in kwargs.items():
            if value is not None:
                tokens[key] = _CTX_VARS[key].set(str(value))
        yield
    finally:
        for key, token in tokens.items():
            _CTX_VARS[key].reset(token)


class ConsoleFormatter(logging.Formatter):
    """Human-readable line with the structured details appended inline,
    so the console is self-describing without opening the JSON file."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        details = getattr(record, "details", None)
        if details:
            line += " | " + json.dumps(details, default=str)
        return line


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "customer_id": getattr(record, "customer_id", "-"),
            "run_id": getattr(record, "run_id", "-"),
        }
        for key in ("event_type", "entity_type", "entity_id", "details"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: Optional[str] = None) -> None:
    """Call at process start (see module docstring), and again after
    anything that might have called logging.config.fileConfig() in the
    meantime (init_db(), via alembic). Always removes and reinstalls its
    own two handlers — cheap, and the only way to reliably win back the
    root logger from a library that reconfigures it out from under us."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_recon_logging", False):
            root.removeHandler(h)
            h.close()

    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    root.setLevel(level)
    ctx_filter = ContextFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ConsoleFormatter(
        "%(asctime)s %(levelname)-7s %(name)-22s "
        "req=%(request_id)s cust=%(customer_id)s run=%(run_id)s :: %(message)s"))
    console.addFilter(ctx_filter)
    console._recon_logging = True
    root.addHandler(console)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(ctx_filter)
    file_handler._recon_logging = True
    root.addHandler(file_handler)

    # alembic registers ~7 "setup plugin ..." INFO lines on every startup;
    # keep alembic.runtime.migration's useful "Running upgrade" at INFO
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
