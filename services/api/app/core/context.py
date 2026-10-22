"""Request/job-scoped context (correlation ID). Stdlib only."""

from __future__ import annotations

import contextvars
import re
import uuid

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def sanitize_correlation_id(candidate: str | None) -> str:
    """Accept a caller-supplied ID only if it is short and boring (log-injection safe)."""
    if candidate and _VALID.match(candidate):
        return candidate
    return new_correlation_id()


def set_correlation_id(value: str) -> contextvars.Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    return _correlation_id.get()
