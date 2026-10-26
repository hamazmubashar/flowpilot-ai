"""Structured, privacy-safe application logs: one JSON object per line on stderr.

Records carry only operational fields (request id, method, route *template*, status, duration,
error type, code locations). Query strings, headers, request/response bodies, exception
messages, API keys and reviewer tokens are never logged; lead data stays in the database and
its audit log. Standard library only.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import IO

LOGGER = "flowpilot"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# Extra attributes copied into the JSON line when a record carries them.
FIELDS = (
    "event",
    "request_id",
    "client",
    "method",
    "route",
    "status",
    "duration_ms",
    "version",
    "host",
    "port",
    "llm_mode",
    "leads",
    "demo",
)
_STACK_FRAMES = 12


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in FIELDS:
            if hasattr(record, name):
                data[name] = getattr(record, name)
        if record.exc_info and record.exc_info[0] is not None:
            # The exception *type* and code locations only: messages can contain data.
            data["error_type"] = record.exc_info[0].__name__
            frames = traceback.extract_tb(record.exc_info[2])[-_STACK_FRAMES:]
            data["stack"] = [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}:{f.name}" for f in frames]
        return json.dumps(data, sort_keys=True, default=str)


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> logging.Logger:
    """Send `flowpilot.*` records to `stream` (stderr) as JSON lines. Safe to call again."""
    logger = logging.getLogger(LOGGER)
    for handler in [h for h in logger.handlers if getattr(h, "_flowpilot", False)]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._flowpilot = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
