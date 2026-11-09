"""Unit tests for structured JSON logging + correlation-id plumbing."""
from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from packages.observability.logging import (
    JsonFormatter,
    configure_logging,
    get_correlation_id,
    set_correlation_id,
)


# --- correlation id (thread-local) -----------------------------------------

def test_correlation_id_round_trip():
    set_correlation_id("abc123")
    assert get_correlation_id() == "abc123"
    set_correlation_id(None)
    assert get_correlation_id() is None


def test_correlation_id_is_thread_local():
    captured: dict[str, str | None] = {}

    def worker() -> None:
        captured["worker"] = get_correlation_id()
        set_correlation_id("worker-cid")
        captured["worker_after"] = get_correlation_id()

    set_correlation_id("main-cid")
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    # Worker thread had no cid at start; setting there does not leak back.
    assert captured["worker"] is None
    assert captured["worker_after"] == "worker-cid"
    assert get_correlation_id() == "main-cid"
    set_correlation_id(None)


# --- JsonFormatter ---------------------------------------------------------

def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_formatter_emits_required_fields():
    set_correlation_id("cid-1")
    rec = logging.LogRecord(
        name="svc", level=logging.INFO, pathname="x.py", lineno=1,
        msg="hello", args=None, exc_info=None,
    )
    payload = _format(rec)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "svc"
    assert payload["cid"] == "cid-1"
    assert payload["msg"] == "hello"
    assert "ts" in payload
    set_correlation_id(None)


def test_formatter_includes_extra_fields():
    rec = logging.LogRecord(
        name="svc", level=logging.INFO, pathname="x.py", lineno=1,
        msg="hi", args=None, exc_info=None,
    )
    rec.user = "alex"
    rec.store_id = "S001"
    payload = _format(rec)
    assert payload["user"] == "alex"
    assert payload["store_id"] == "S001"


def test_formatter_stringifies_unserializable_extras():
    class NotJson:
        def __repr__(self) -> str:
            return "<NotJson>"

    rec = logging.LogRecord(
        name="svc", level=logging.INFO, pathname="x.py", lineno=1,
        msg="hi", args=None, exc_info=None,
    )
    rec.obj = NotJson()
    payload = _format(rec)
    assert payload["obj"] == "<NotJson>"


def test_formatter_captures_exception_type_and_message():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        rec = logging.LogRecord(
            name="svc", level=logging.ERROR, pathname="x.py", lineno=1,
            msg="failed", args=None, exc_info=sys.exc_info(),
        )
    payload = _format(rec)
    assert payload["exc_type"] == "RuntimeError"
    assert payload["exc_msg"] == "boom"
    # Raw stack trace must not appear by default.
    assert "Traceback" not in json.dumps(payload)


def test_formatter_output_is_single_line_json():
    rec = logging.LogRecord(
        name="svc", level=logging.INFO, pathname="x.py", lineno=1,
        msg="hello", args=None, exc_info=None,
    )
    raw = JsonFormatter().format(rec)
    assert "\n" not in raw
    # Must round-trip as JSON.
    json.loads(raw)


# --- configure_logging -----------------------------------------------------

def test_configure_logging_replaces_handlers(monkeypatch):
    # Baseline: dirty root with a bogus handler.
    root = logging.getLogger()
    root.handlers = [logging.StreamHandler(io.StringIO()),
                     logging.StreamHandler(io.StringIO())]
    configure_logging(level="WARNING")
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING


def test_configure_logging_reads_env_level(monkeypatch):
    monkeypatch.setenv("STOREPULSE_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG
