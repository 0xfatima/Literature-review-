"""Comparison tables and versioned narrative synthesis. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import Project, User
from app.db.session import get_db
from app.synthesis import service as svc

router = APIRouter(prefix="/projects/{project_id}/synthesis", tags=["synthesis"])


@router.get("/comparison")
def comparison(
    schema_id: uuid.UUID | None = None,
    group_by: str | None = Query(None, max_length=120),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.comparison(db, project, schema_id, group_by)


@router.get("/versions")
def list_versions(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [svc.version_view(db, v) for v in svc.list_versions(db, project)]


@router.post("/versions", status_code=201)
def create_version(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.version_view(db, svc.create_empty(db, project, user, body))


@router.get("/versions/{synthesis_id}")
def get_version(
    synthesis_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.version_view(db, svc.get_version(db, project, synthesis_id))


@router.post("/versions/{synthesis_id}/review")
def review_statement(
    synthesis_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.version_view(db, svc.review(db, project, user, synthesis_id, body))


@router.post("/versions/{synthesis_id}/accept")
def accept_version(
    synthesis_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.version_view(db, svc.accept(db, project, user, synthesis_id, body))


@router.post("/versions/{synthesis_id}/revise", status_code=201)
def revise_version(
    synthesis_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.version_view(db, svc.revise(db, project, user, synthesis_id))


@router.get("/drafts")
def draft_status(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return svc.draft_status(db, settings, user, project)


@router.post("/drafts", status_code=202)
def start_draft(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Callable[[str, str], None] = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    job = svc.start_draft(db, settings, dispatch, limiter, user, project, body)
    return {"job_id": job.id, "status": job.status}
