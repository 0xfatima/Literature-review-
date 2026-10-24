"""Extraction schema and evidence items. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.db.models import EvidenceItem, Project, User
from app.db.session import get_db
from app.extraction import schema_service as svc

router = APIRouter(prefix="/projects/{project_id}", tags=["extraction"])


@router.get("/extraction/schemas")
def list_schemas(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [svc.schema_view(s) for s in svc.list_schemas(db, project)]


@router.post("/extraction/schemas", status_code=201)
def create_schema(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = svc.create_draft(
        db,
        project,
        user.id,
        field_definitions=body.get("field_definitions") or [],
        question_map=body.get("question_map"),
        notes=body.get("notes"),
        change_reason=body.get("change_reason"),
    )
    return svc.schema_view(s)


@router.get("/extraction/schemas/{schema_id}")
def get_schema(
    schema_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.schema_view(svc.get_schema(db, project, schema_id))


@router.patch("/extraction/schemas/{schema_id}")
def update_schema(
    schema_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = svc.update_draft(
        db,
        project,
        schema_id,
        user.id,
        field_definitions=body.get("field_definitions"),
        question_map=body.get("question_map"),
        notes=body.get("notes"),
        expected_version=body.get("expected_row_version"),
    )
    return svc.schema_view(s)


@router.post("/extraction/schemas/{schema_id}/approve")
def approve_schema(
    schema_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = svc.approve_schema(
        db,
        project,
        schema_id,
        user.id,
        expected_version=body.get("expected_row_version"),
    )
    return svc.schema_view(s)


@router.get("/extraction/schemas/{schema_id}/coverage")
def schema_coverage(
    schema_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = svc.get_schema(db, project, schema_id)
    return svc.coverage_by_question(db, project, s)


@router.get("/extraction/schemas/{schema_id}/evidence")
def list_evidence(
    schema_id: uuid.UUID,
    paper_id: uuid.UUID | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    svc.get_schema(db, project, schema_id)  # 404 if not found
    q = select(EvidenceItem).where(
        EvidenceItem.extraction_schema_id == schema_id,
        EvidenceItem.project_id == project.id,
    )
    if paper_id:
        q = q.where(EvidenceItem.paper_id == paper_id)
    if status:
        q = q.where(EvidenceItem.status == status)
    from sqlalchemy import func

    total = db.scalar(q.with_only_columns(func.count()).order_by(None)) or 0
    items = list(db.scalars(q.offset(offset).limit(limit)))
    return {"items": [svc.evidence_view(e) for e in items], "total": total, "offset": offset, "limit": limit}


@router.put("/extraction/schemas/{schema_id}/evidence/{paper_id}/{field_key}")
def set_evidence(
    schema_id: uuid.UUID,
    paper_id: uuid.UUID,
    field_key: str,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    e = svc.upsert_evidence(
        db,
        project,
        paper_id=paper_id,
        schema_id=schema_id,
        field_key=field_key,
        status=str(body.get("status") or "pending"),
        value=body.get("value"),
        excerpt=body.get("excerpt"),
        document_page_id=uuid.UUID(str(body["document_page_id"])) if body.get("document_page_id") else None,
        reviewer_id=user.id,
        note=body.get("note"),
        expected_row_version=body.get("expected_row_version"),
    )
    return svc.evidence_view(e)
