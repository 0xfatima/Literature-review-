from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import error_handlers, health
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.context import reset_correlation_id, sanitize_correlation_id, set_correlation_id
from app.core.errors import error_body
from app.core.logging import configure_logging
from app.core.security import csrf_violation

log = logging.getLogger("app.request")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="Systematic Review Scientist API",
        version="0.1.0",
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    error_handlers.install(app)

    if settings.allowed_origins:  # exact allow-list; never a wildcard (enforced by settings checks)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-SRS-CSRF", "Idempotency-Key", "X-Correlation-ID"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        cid = sanitize_correlation_id(request.headers.get("x-correlation-id"))
        token = set_correlation_id(cid)
        start = time.perf_counter()
        try:
            reason = csrf_violation(
                request.method, {k.lower(): v for k, v in request.headers.items()}, settings.allowed_origins
            )
            response: Response
            if reason and request.url.path.startswith("/api/"):
                response = JSONResponse(
                    error_body("csrf_rejected", "Request rejected.", [{"issue": reason}]), status_code=403
                )
            else:
                response = await call_next(request)
            response.headers["X-Correlation-ID"] = cid
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,  # no query string
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            return response
        finally:
            reset_correlation_id(token)

    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
