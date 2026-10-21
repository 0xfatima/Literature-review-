from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, error_body

log = logging.getLogger(__name__)
_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
               405: "method_not_allowed", 413: "payload_too_large", 429: "rate_limited"}


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Deliberately drop pydantic's `input`/`ctx`: they echo submitted values (e.g. passwords).
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "issue": e["type"],
                    "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(error_body("validation_error", "Request validation failed.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")  # stack goes to (redacted) logs, never to the client
        return JSONResponse(error_body("internal_error", "Something went wrong."), status_code=500)
