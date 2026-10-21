from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import Unauthorized
from app.core.net import client_ip
from app.core.rate_limit import InMemoryRateLimiter, RateLimiter, RedisRateLimiter
from app.db.models import AuthSession, Project, User
from app.db.session import get_db
from app.jobs.celery_app import send_task
from app.services import auth_service, project_service


@lru_cache
def _limiter() -> RateLimiter:
    s = get_settings()
    try:
        import redis
        return RedisRateLimiter(redis.Redis.from_url(s.redis_url, socket_timeout=1, socket_connect_timeout=1))
    except Exception:  # noqa: BLE001
        return InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter()


def get_dispatcher() -> Callable[[str, str], None]:
    return send_task


def client_key(request: Request, settings: Settings = Depends(get_settings)) -> str:
    peer = request.client.host if request.client else None
    return client_ip(request.headers.get("x-forwarded-for"), peer, settings.trusted_proxy_hops)


def current_session(request: Request, db: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings)) -> tuple[User, AuthSession]:
    resolved = auth_service.resolve_session(db, request.cookies.get(settings.session_cookie_name))
    if resolved is None:
        raise Unauthorized("Authentication required.")
    return resolved


def current_user(resolved: tuple[User, AuthSession] = Depends(current_session)) -> User:
    return resolved[0]


def get_owned_project(project_id: uuid.UUID, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> Project:
    """THE authorization choke point for every /projects/{project_id}/... route.
    Raises 404 (not 403) for foreign projects so existence is not leaked."""
    return project_service.get(db, user, project_id)
