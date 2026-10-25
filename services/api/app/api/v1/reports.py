"""Review report drafts, edits, validation, exports and claim tracing. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.db.models import Project, User
from app.db.session import get_db
from app.reports import service as svc

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])


@router.get("")
def list_reports(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_versions(db, project)


@router.post("", status_code=201)
def generate_report(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.view(db, svc.generate(db, project, user, body))


@router.get("/{draft_id}")
def get_report(
    draft_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.view(db, svc.get(db, project, draft_id))


@router.post("/{draft_id}/edits", status_code=201)
def edit_report(
    draft_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.view(db, svc.edit(db, project, user, draft_id, body))


@router.get("/{draft_id}/trace")
def trace_claim(
    draft_id: uuid.UUID,
    claim_id: str | None = Query(None, max_length=64),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if claim_id is None:
        return svc.claims(db, project, draft_id)
    return svc.trace(db, project, draft_id, claim_id)


@router.get("/{draft_id}/exports")
def list_exports(
    draft_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return svc.list_exports(db, project, draft_id)


@router.post("/{draft_id}/exports")
def export_report(
    draft_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    blob, filename, media, rec = svc.export(db, project, user, draft_id, str(body.get("format") or ""))
    return Response(
        content=blob,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Export-Id": str(rec.id),
            "X-Report-Export-Sha256": rec.sha256,
            "Cache-Control": "no-store",
        },
    )
