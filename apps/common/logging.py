"""Logging helpers: request-ID injection and a JSON formatter for production."""

import datetime as dt
import json
import logging

from apps.common.middleware import get_request_id

# Attributes LogRecord always carries; anything else was passed via `extra=`.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"asctime", "message", "request_id", "taskName"}
)


class RequestIDFilter(logging.Filter):
    """Makes `%(request_id)s` available to every formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line — what log aggregators expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", get_request_id()),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything the caller passed as extra={...}
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value

        return json.dumps(payload, default=str)
