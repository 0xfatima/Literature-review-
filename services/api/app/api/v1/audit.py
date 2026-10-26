"""Audit log (Phase 14): read-only. There is deliberately no route that edits or deletes an audit event; corrections
are new events written by the feature that made them. Project routes depend on get_owned_project (404 for others)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.audit.catalog import CATEGORY_NAMES
from app.core.config import Settings, get_settings
from app.db.models import Project, User
from app.db.session import get_db
from app.services import audit_service

router = APIRouter(tags=["audit"])


@router.get("/audit/categories")
def categories(_: User = Depends(current_user)) -> list[str]:
    return [c for c in CATEGORY_NAMES if c != "other"]


@router.get("/projects/{project_id}/audit-events")
def project_events(
    category: str | None = None,
    entity_type: str | None = None,
    actor: str | None = None,
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return audit_service.list_events(
        db,
        viewer_id=user.id,
        project_id=project.id,
        category=category,
        entity_type=entity_type,
        actor=actor,
        cursor=cursor,
        limit=limit,
    )


@router.get("/projects/{project_id}/audit-events/export.csv")
def export_project_events(
    category: str | None = None,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    body = audit_service.export_csv(
        db, viewer_id=user.id, project_id=project.id, max_rows=settings.audit_export_max_rows, category=category
    )
    return StreamingResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{project.id}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/auth/me/audit-events")
def account_events(
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return audit_service.list_events(db, viewer_id=user.id, project_id=None, cursor=cursor, limit=limit)
