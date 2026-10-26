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


# Which system process is acting when no user is (Phase 14 audit attribution): "worker:<task>", "cli:<command>".
_actor_process: contextvars.ContextVar[str | None] = contextvars.ContextVar("actor_process", default=None)
_PROCESS = re.compile(r"[^A-Za-z0-9._:-]")


def set_actor_process(value: str | None) -> contextvars.Token[str | None]:
    return _actor_process.set(_PROCESS.sub("_", value)[:64] if value else None)


def reset_actor_process(token: contextvars.Token[str | None]) -> None:
    _actor_process.reset(token)


def get_actor_process() -> str | None:
    return _actor_process.get()
