from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api import error_handlers, health, internal
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.context import reset_correlation_id, sanitize_correlation_id, set_correlation_id
from app.core.errors import error_body
from app.core.http_security import body_too_large, security_headers, write_limit_key
from app.core.logging import configure_logging
from app.core.metrics import REQUESTS
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

    def _effective_settings() -> Settings:
        # Honour dependency overrides (tests) so middleware and routes always agree on configuration.
        effective: Settings = app.dependency_overrides.get(get_settings, get_settings)()
        return effective

    def _guard(request: Request, s: Settings) -> Response | None:
        """Cheap request-level guards that run before any route: body ceiling, then CSRF."""
        path = request.url.path
        if body_too_large(
            request.method,
            path,
            request.headers.get("content-length"),
            max_body=s.max_request_body_bytes,
            upload_max=s.upload_max_bytes,
        ):
            return JSONResponse(error_body("payload_too_large", "Request body is too large."), status_code=413)
        reason = csrf_violation(request.method, {k.lower(): v for k, v in request.headers.items()}, s.allowed_origins)
        if reason and path.startswith("/api/"):
            return JSONResponse(error_body("csrf_rejected", "Request rejected.", [{"issue": reason}]), status_code=403)
        return None

    async def _write_limit(request: Request, s: Settings) -> Response | None:
        key = write_limit_key(request.method, request.url.path, request.cookies.get(s.session_cookie_name))
        if not key or s.rate_limit_writes_per_min <= 0:
            return None
        from app.api.deps import get_rate_limiter

        limiter = app.dependency_overrides.get(get_rate_limiter, get_rate_limiter)()
        # the Redis limiter is synchronous: keep it off the event loop
        allowed, retry = await run_in_threadpool(limiter.hit, key, s.rate_limit_writes_per_min, 60)
        if allowed:
            return None
        return JSONResponse(
            error_body("rate_limited", "Too many changes in a short time. Slow down and try again."),
            status_code=429,
            headers={"Retry-After": str(max(1, retry))},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        cid = sanitize_correlation_id(request.headers.get("x-correlation-id"))
        token = set_correlation_id(cid)
        start = time.perf_counter()
        try:
            s = _effective_settings()
            response: Response
            blocked = _guard(request, s) or await _write_limit(request, s)
            response = blocked if blocked is not None else await call_next(request)
            for name, value in security_headers(
                path=request.url.path, hsts_max_age_s=s.hsts_max_age_s if s.cookie_secure else None
            ).items():
                response.headers.setdefault(name, value)
            response.headers["X-Correlation-ID"] = cid
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            template = getattr(route, "path", None)
            if template is None:
                template = "unmatched"
            elif request.url.path.startswith("/api/v1") and not template.startswith("/api/v1"):
                template = "/api/v1" + template
            REQUESTS.observe(request.method, template, response.status_code, elapsed)
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,  # no query string
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            return response
        finally:
            reset_correlation_id(token)

    app.include_router(health.router)
    app.include_router(internal.router)
    app.include_router(api_router)
    return app


app = create_app()
