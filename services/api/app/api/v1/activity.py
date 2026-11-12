"""What is running in a project right now (one step at a time), and pausing / resuming it. Every route depends on
get_owned_project."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_user_settings
from app.core.config import Settings
from app.db.models import Project, User
from app.db.session import get_db
from app.schemas.jobs import JobOut
from app.services import activity_service

router = APIRouter(prefix="/projects/{project_id}/activity", tags=["activity"])
Dispatch = Callable[[str, str], None]


def _out(v: dict[str, Any]) -> dict[str, Any]:
    return {
        **v,
        "running": JobOut.model_validate(v["running"]).model_dump() if v["running"] else None,
        "paused": JobOut.model_validate(v["paused"]).model_dump() if v["paused"] else None,
    }


@router.get("")
def current_activity(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _out(activity_service.view(db, project))


@router.post("/pause")
def pause_step(
    project: Project = Depends(get_owned_project), user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return _out(activity_service.pause(db, user, project))


@router.post("/resume")
def resume_step(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_user_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> dict[str, Any]:
    return _out(activity_service.resume(db, settings, dispatch, user, project))
