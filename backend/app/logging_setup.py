"""Structured JSON logging with request IDs. Never log headers, bodies or secrets."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Extra attributes copied into the JSON line when present on a LogRecord.
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "data_mode", "stale_level",
                 "source", "age_minutes", "reason", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", stream=None) -> logging.Logger:
    logger = logging.getLogger("relay")
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
