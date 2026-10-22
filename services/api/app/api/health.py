"""Liveness / readiness / worker health. Probes never call an LLM or external provider."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def liveness() -> dict[str, str]:
    """Process is up. No dependency checks (so an outage elsewhere does not restart-loop the API)."""
    return {"status": "ok"}


def _check_db() -> str:
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def _check_redis(settings: Settings) -> str:
    try:
        import redis

        redis.Redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1).ping()
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


@router.get("/readyz")
def readiness(settings: Settings = Depends(get_settings)) -> JSONResponse:
    checks = {"database": _check_db(), "redis": _check_redis(settings)}
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503)


@router.get("/healthz/worker")
def worker_health(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Asks live workers to answer a Celery control ping. Kept out of /readyz so a worker outage does not
    take the API out of rotation (the API can still serve reads and queue jobs)."""
    try:
        from app.jobs.celery_app import celery_app

        replies = celery_app.control.ping(timeout=settings.worker_health_timeout_s) or []
        workers = sorted(k for r in replies for k in r)
    except Exception:  # noqa: BLE001
        workers = []
    ok = len(workers) > 0
    return JSONResponse({"status": "ok" if ok else "no_workers", "workers": workers}, status_code=200 if ok else 503)
