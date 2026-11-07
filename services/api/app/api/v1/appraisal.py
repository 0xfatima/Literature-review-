"""Critical appraisal routes. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter, get_user_settings
from app.appraisal import frameworks as fwm
from app.appraisal import service as svc
from app.core.config import Settings
from app.core.errors import ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import Project, User
from app.db.session import get_db

router = APIRouter(prefix="/projects/{project_id}/appraisal", tags=["appraisal"])


@router.get("")
def appraisal_overview(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.overview(db, project)


@router.get("/frameworks")
def list_frameworks(project: Project = Depends(get_owned_project)) -> list[dict[str, Any]]:
    return [f.view() for f in fwm.FRAMEWORKS.values()]


@router.get("/papers/{paper_id}")
def paper_appraisals(
    paper_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.detail(db, project, paper_id)


@router.put("/papers/{paper_id}")
def save_appraisal(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.view(svc.save(db, project, user, paper_id, body))


@router.post("/papers/{paper_id}/approve")
def approve_appraisal(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.view(svc.approve(db, project, user, paper_id, body))


@router.get("/papers/{paper_id}/history")
def appraisal_history(
    paper_id: uuid.UUID,
    framework_key: str | None = Query(None, max_length=40),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.history(db, project, paper_id, framework_key)


@router.get("/suggestions")
def suggestion_status(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
) -> dict[str, Any]:
    return svc.suggestion_status(db, settings, user, project)


@router.post("/suggestions", status_code=202)
def start_suggestions(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Callable[[str, str], None] = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    paper_ids = body.get("paper_ids")
    frameworks = body.get("frameworks")
    if paper_ids is not None and (not isinstance(paper_ids, list) or len(paper_ids) > 500):
        raise ValidationFailed("paper_ids must be a list of at most 500 ids.", details=[{"field": "paper_ids"}])
    if frameworks is not None and not isinstance(frameworks, dict):
        raise ValidationFailed("frameworks must map paper ids to framework keys.", details=[{"field": "frameworks"}])
    job = svc.start_suggestions(
        db,
        settings,
        dispatch,
        limiter,
        user,
        project,
        paper_ids=[str(p) for p in paper_ids] if paper_ids else None,
        frameworks={str(k): str(v) for k, v in frameworks.items()} if frameworks else None,
    )
    return {"job_id": job.id, "status": job.status}
