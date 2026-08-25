from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_KEYS = {"authorization", "api_secret", "api_key", "x-auth-key"}


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    query = urlencode([(key, "[REDACTED]") for key, _value in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


class _JsonFormatter(logging.Formatter):
    _standard = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._standard or key.startswith("_"):
                continue
            if key.lower() in _SENSITIVE_KEYS:
                payload[key] = "[REDACTED]"
            elif key.lower() == "url" and isinstance(value, str):
                payload[key] = _redact_url(value)
            else:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)


def configure_logging(path: Path, *, max_bytes: int = 1024 * 1024, backups: int = 3) -> logging.Logger:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger = logging.getLogger(f"anberpod.{path.resolve()}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    return logger
