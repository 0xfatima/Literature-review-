"""Application error types and the standard error envelope. Stdlib only.

Envelope (all non-2xx responses):
  {"error": {"code": str, "message": str, "details": list|None, "correlation_id": str|None}}
"""

from __future__ import annotations

from typing import Any

from app.core.context import get_correlation_id


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code
        self.details = details
        self.headers = headers or {}


class ValidationFailed(AppError):
    status_code, code = 422, "validation_error"


class Unauthorized(AppError):
    status_code, code = 401, "unauthorized"


class Forbidden(AppError):
    status_code, code = 403, "forbidden"


class NotFound(AppError):
    status_code, code = 404, "not_found"


class Conflict(AppError):
    status_code, code = 409, "conflict"


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"

    def __init__(self, retry_after_s: int, message: str = "Too many attempts. Try again later."):
        super().__init__(message, headers={"Retry-After": str(max(1, int(retry_after_s)))})


def error_body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "correlation_id": get_correlation_id()}}
