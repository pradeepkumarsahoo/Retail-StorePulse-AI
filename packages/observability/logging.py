"""Structured JSON logging with correlation IDs.

Every log line is a single JSON object: ts, level, logger, cid, msg, plus any
`extra` fields a caller attaches. No stack traces or raw prompts by default.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone


_cid = threading.local()


def set_correlation_id(cid: str | None) -> None:
    _cid.value = cid


def get_correlation_id() -> str | None:
    return getattr(_cid, "value", None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "cid": get_correlation_id(),
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key in {"args", "msg", "levelname", "levelno", "pathname", "filename", "module",
                       "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created",
                       "msecs", "relativeCreated", "thread", "threadName", "processName",
                       "process", "name", "asctime", "message", "taskName"}:
                continue
            try:
                json.dumps(val)
                payload[key] = val
            except (TypeError, ValueError):
                payload[key] = repr(val)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc_msg"] = str(record.exc_info[1]) if record.exc_info[1] else None
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str | None = None) -> None:
    lvl = (level or os.environ.get("STOREPULSE_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # Replace handlers so we never double-emit
    root.handlers = [handler]
    root.setLevel(lvl)
    # Quiet uvicorn access double-emit
    logging.getLogger("uvicorn.access").propagate = False
