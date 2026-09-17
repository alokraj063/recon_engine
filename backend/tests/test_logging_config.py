"""
Container logging: LOG_TO_FILE=false attaches no file handler (and creates
no log directory); LOG_FORMAT=json puts the structured JSON lines on stdout.
Handlers are removed again after each test, so nothing leaks into the rest
of the suite (the "no log files during pytest" strategy).
"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging_setup  # noqa: E402


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name in ("LOG_TO_FILE", "LOG_FORMAT", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logging_setup, "LOG_DIR", log_dir)
    monkeypatch.setattr(logging_setup, "LOG_FILE", log_dir / "app.log")
    root = logging.getLogger()
    level = root.level
    yield log_dir
    for h in list(root.handlers):
        if getattr(h, "_recon_logging", False):
            root.removeHandler(h)
            h.close()
    root.setLevel(level)


def _ours():
    return [h for h in logging.getLogger().handlers
            if getattr(h, "_recon_logging", False)]


def test_default_keeps_console_text_plus_file(configured):
    logging_setup.configure_logging()
    handlers = _ours()
    assert len(handlers) == 2
    assert any(isinstance(h, RotatingFileHandler) for h in handlers)
    console = next(h for h in handlers if not isinstance(h, RotatingFileHandler))
    assert isinstance(console.formatter, logging_setup.ConsoleFormatter)
    assert configured.is_dir()


@pytest.mark.parametrize("value", ["false", "0", "no", "OFF"])
def test_log_to_file_false_means_stdout_only(configured, monkeypatch, value):
    monkeypatch.setenv("LOG_TO_FILE", value)
    logging_setup.configure_logging()
    handlers = _ours()
    assert len(handlers) == 1
    assert not isinstance(handlers[0], RotatingFileHandler)
    assert not configured.exists()          # no directory created either


def test_json_console_carries_correlation_fields(configured, monkeypatch, capsys):
    monkeypatch.setenv("LOG_TO_FILE", "false")
    monkeypatch.setenv("LOG_FORMAT", "json")
    logging_setup.configure_logging()
    # pytest's capture swaps sys.stdout; point our handler at the capture
    handler = _ours()[0]
    handler.setStream(sys.stdout)

    with logging_setup.bind_context(request_id="req-1", customer_id="wabtec"):
        logging.getLogger("app.test").info("http.request", extra={
            "event_type": "http.request", "details": {"status_code": 200}})

    line = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()][-1]
    payload = json.loads(line)
    assert payload["message"] == "http.request"
    assert payload["request_id"] == "req-1"
    assert payload["customer_id"] == "wabtec"
    assert payload["event_type"] == "http.request"
    assert payload["details"] == {"status_code": 200}


def test_reconfigure_does_not_duplicate_handlers(configured, monkeypatch):
    monkeypatch.setenv("LOG_TO_FILE", "false")
    logging_setup.configure_logging()
    logging_setup.configure_logging()
    assert len(_ours()) == 1
