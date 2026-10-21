"""Structured JSON logging with secret redaction. Stdlib only.

Policy (spec §8, §10): never log passwords, tokens, API keys, cookies, or document/chat bodies.
Log IDs, counts, timings, and sanitized errors only.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.core.context import get_correlation_id

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(pass(word)?|secret|token|authorization|cookie|api[_-]?key|credential|session|csrf)", re.IGNORECASE
)
# key=value / key: value patterns inside free-text messages
_INLINE_KV = re.compile(
    r"(?i)\b(pass(?:word)?|secret|token|api[_-]?key|authorization|cookie|session)\b(\s*[=:]\s*)([^\s,;&\"']+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_CREDS = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)")

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact_text(text: str) -> str:
    text = _URL_CREDS.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _INLINE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def redact(value: Any, _key: str | None = None) -> Any:
    """Recursively redact by key name and by inline patterns in strings."""
    if _key is not None and _SENSITIVE_KEY.search(_key):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
            "correlation_id": get_correlation_id(),
        }
        for k, v in record.__dict__.items():
            if k not in _STANDARD_ATTRS and not k.startswith("_"):
                payload[k] = redact(v, k)
        if record.exc_info:
            # exception text can embed secrets (DSNs etc.); redact it too
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):  # we emit our own request log line
        logging.getLogger(noisy).disabled = True
