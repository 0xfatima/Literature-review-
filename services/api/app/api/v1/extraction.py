"""Extraction schema and evidence items. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.errors import ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import DocumentPage, EvidenceItem, Project, User
from app.db.session import get_db
from app.extraction import evidence_service
from app.extraction import schema_service as svc
from app.services import consent_service, extraction_service

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
    items = list(db.scalars(q.order_by(EvidenceItem.created_at, EvidenceItem.id).offset(offset).limit(limit)))
    page_ids = [e.document_page_id for e in items if e.document_page_id]
    pages = {p.id: p for p in db.scalars(select(DocumentPage).where(DocumentPage.id.in_(page_ids)))} if page_ids else {}
    return {
        "items": [svc.evidence_view(e, pages.get(e.document_page_id) if e.document_page_id else None) for e in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _uuid_or_none(v: Any, name: str) -> uuid.UUID | None:
    if v in (None, ""):
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise ValidationFailed(f"{name} must be a UUID.", details=[{"field": name}]) from None


def _int_or_none(v: Any, name: str) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValidationFailed(f"{name} must be a positive integer.", details=[{"field": name}])
    return v


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
        document_page_id=_uuid_or_none(body.get("document_page_id"), "document_page_id"),
        document_id=_uuid_or_none(body.get("document_id"), "document_id"),
        page_no=_int_or_none(body.get("page_no"), "page_no"),
        reviewer_id=user.id,
        note=body.get("note"),
        reason=body.get("reason"),
        expected_row_version=body.get("expected_row_version"),
    )
    page = db.get(DocumentPage, e.document_page_id) if e.document_page_id else None
    return svc.evidence_view(e, page)


# ---------- Phase 9: evidence extraction, review, table, lineage and exports ----------


@router.post("/extraction/schemas/{schema_id}/evidence/{paper_id}/{field_key}/approve")
def approve_evidence(
    schema_id: uuid.UUID,
    paper_id: uuid.UUID,
    field_key: str,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    e = svc.approve_evidence(
        db,
        project,
        schema_id=schema_id,
        paper_id=paper_id,
        field_key=field_key,
        reviewer_id=user.id,
        expected_row_version=body.get("expected_row_version"),
    )
    page = db.get(DocumentPage, e.document_page_id) if e.document_page_id else None
    return svc.evidence_view(e, page)


@router.get("/extraction/schemas/{schema_id}/evidence/{paper_id}/{field_key}/history")
def evidence_history(
    schema_id: uuid.UUID,
    paper_id: uuid.UUID,
    field_key: str,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return evidence_service.history(db, project, schema_id, paper_id, field_key)


@router.get("/extraction/schemas/{schema_id}/table")
def evidence_table(
    schema_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return evidence_service.table(db, project, svc.get_schema(db, project, schema_id), offset, limit)


@router.post("/extraction/schemas/{schema_id}/exports")
def export_table(
    schema_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    schema = svc.get_schema(db, project, schema_id)
    text, out = evidence_service.export_csv(db, project, schema, user)
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-table-v{schema.version}.csv"',
            "X-Evidence-Output-Id": str(out.id),
            "Cache-Control": "no-store",
        },
    )


@router.get("/extraction/schemas/{schema_id}/affected")
def affected_outputs(
    schema_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return evidence_service.affected(db, project, svc.get_schema(db, project, schema_id))


@router.get("/extraction/schemas/{schema_id}/runs")
def extraction_status(
    schema_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return extraction_service.status(db, settings, user, project, svc.get_schema(db, project, schema_id))


@router.post("/extraction/schemas/{schema_id}/runs", status_code=202)
def start_extraction(
    schema_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Callable[[str, str], None] = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    paper_ids = body.get("paper_ids")
    if paper_ids is not None and (not isinstance(paper_ids, list) or len(paper_ids) > 500):
        raise ValidationFailed("paper_ids must be a list of at most 500 ids.", details=[{"field": "paper_ids"}])
    job = extraction_service.start(
        db,
        settings,
        dispatch,
        limiter,
        user,
        project,
        schema_id,
        paper_ids=[str(p) for p in paper_ids] if paper_ids else None,
        force=bool(body.get("force")),
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/llm-consent")
def consent_status(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return consent_service.status(db, settings, project)


@router.post("/llm-consent")
def consent_grant(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return consent_service.grant(db, settings, user, project, str(body.get("notice_version") or ""))


@router.delete("/llm-consent")
def consent_revoke(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return consent_service.revoke(db, settings, user, project)
