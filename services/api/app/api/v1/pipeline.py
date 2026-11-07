"""Controlled-autonomy pipeline routes. Every route depends on get_owned_project."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_user_settings
from app.core.config import Settings
from app.db.models import Project, User
from app.db.session import get_db
from app.pipeline import service as svc

router = APIRouter(prefix="/projects/{project_id}/pipeline", tags=["pipeline"])
Dispatch = Callable[[str, str], None]


@router.get("")
def pipeline_overview(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
) -> dict[str, Any]:
    return svc.overview(db, settings, project)


@router.post("", status_code=202)
def start_pipeline(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return svc.run_view(db, svc.start(db, settings, dispatch, user, project, body))


@router.post("/answer", status_code=202)
def answer(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return svc.run_view(db, svc.answer(db, settings, dispatch, user, project, body))


@router.post("/pause")
def pause(
    project: Project = Depends(get_owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.run_view(db, svc.pause(db, user, project))


@router.post("/resume", status_code=202)
def resume(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return svc.run_view(db, svc.resume(db, settings, dispatch, user, project))


@router.post("/recover", status_code=202)
def recover(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return svc.run_view(db, svc.recover(db, settings, dispatch, user, project))


@router.post("/cancel")
def cancel(
    project: Project = Depends(get_owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.run_view(db, svc.cancel(db, user, project))


@router.post("/rerun", status_code=202)
def rerun(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return svc.run_view(db, svc.rerun(db, settings, dispatch, user, project, body))


@router.get("/impact")
def impact(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.impact_view(db, project)


@router.post("/impact/confirm")
def confirm(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
) -> dict[str, Any]:
    return svc.confirm_decisions(db, settings, user, project, body)


@router.post("/invalidations/dismiss")
def dismiss(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.dismiss_invalidation(db, user, project, body)
