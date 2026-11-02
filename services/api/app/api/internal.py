"""Operator-only endpoints (Phase 15). Not under /api, so the edge proxy never forwards them; they also require
METRICS_TOKEN. With no token configured the route answers 404, exactly like a path that does not exist."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import NotFound
from app.core.metrics import REQUESTS
from app.db.session import get_db
from app.services import metrics_service

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/metrics")
def metrics(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    expected = settings.metrics_token or ""
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise NotFound("Not found.")
    lines = list(REQUESTS.render()) + metrics_service.render(db, settings.metrics_window_s)
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
