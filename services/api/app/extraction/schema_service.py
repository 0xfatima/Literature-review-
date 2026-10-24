"""Extraction schema lifecycle: create, suggest, approve, version, record evidence.

An extraction schema is a versioned list of field definitions tied to a project.
Approved schemas are immutable; new versions create new rows.
Evidence items record what was extracted (or why extraction was not possible) for each field of each included paper.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import (
    EVIDENCE_STATUSES,
    DocumentPage,
    EvidenceItem,
    ExtractionSchema,
    PaperRecord,
    Project,
)
from app.services import audit_service

MAX_FIELDS = 80
MAX_FIELD_KEY = 60
MAX_LABEL = 200
FIELD_TYPES = ("text", "number", "choice", "multi_choice", "boolean", "date")


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_fields(field_definitions: list[dict[str, Any]]) -> None:
    """Validate a list of field definitions against the schema contract."""
    if not isinstance(field_definitions, list):
        raise ValidationFailed("field_definitions must be a list.")
    if len(field_definitions) > MAX_FIELDS:
        raise ValidationFailed(f"At most {MAX_FIELDS} fields are allowed.")
    keys: set[str] = set()
    for i, fd in enumerate(field_definitions):
        key = str(fd.get("field_key") or "")
        label = str(fd.get("label") or "")
        ftype = str(fd.get("type") or "text")
        if not key or not key.replace("_", "").isalnum() or len(key) > MAX_FIELD_KEY:
            raise ValidationFailed(
                f"Field {i}: field_key must be alphanumeric/underscore, max {MAX_FIELD_KEY} chars.",
                details=[{"field": f"field_definitions[{i}].field_key", "issue": "invalid"}],
            )
        if key in keys:
            raise ValidationFailed(f"Duplicate field_key: '{key}'.")
        keys.add(key)
        if not label or len(label) > MAX_LABEL:
            raise ValidationFailed(f"Field '{key}': label is required, max {MAX_LABEL} chars.")
        if ftype not in FIELD_TYPES:
            raise ValidationFailed(f"Field '{key}': unknown type '{ftype}'. Allowed: {', '.join(FIELD_TYPES)}.")
        if ftype in ("choice", "multi_choice") and not fd.get("options"):
            raise ValidationFailed(f"Field '{key}' (type {ftype}): options list is required.")


# ---------- schema CRUD ----------


def current_schema(db: Session, project: Project) -> ExtractionSchema | None:
    return db.scalar(
        select(ExtractionSchema)
        .where(ExtractionSchema.project_id == project.id, ExtractionSchema.status == "approved")
        .order_by(ExtractionSchema.version.desc())
        .limit(1)
    )


def draft_schema(db: Session, project: Project) -> ExtractionSchema | None:
    return db.scalar(
        select(ExtractionSchema)
        .where(ExtractionSchema.project_id == project.id, ExtractionSchema.status == "draft")
        .order_by(ExtractionSchema.version.desc())
        .limit(1)
    )


def get_schema(db: Session, project: Project, schema_id: uuid.UUID) -> ExtractionSchema:
    s = db.scalar(
        select(ExtractionSchema).where(
            ExtractionSchema.id == schema_id,
            ExtractionSchema.project_id == project.id,
        )
    )
    if s is None:
        raise NotFound("Extraction schema not found.")
    return s


def list_schemas(db: Session, project: Project) -> list[ExtractionSchema]:
    return list(
        db.scalars(
            select(ExtractionSchema)
            .where(ExtractionSchema.project_id == project.id)
            .order_by(ExtractionSchema.version.desc())
        )
    )


def create_draft(
    db: Session,
    project: Project,
    user_id: uuid.UUID,
    *,
    field_definitions: list[dict[str, Any]],
    question_map: dict[str, list[str]] | None = None,
    notes: str | None = None,
    change_reason: str | None = None,
) -> ExtractionSchema:
    _validate_fields(field_definitions)
    existing = draft_schema(db, project)
    if existing is not None:
        raise Conflict(
            "A draft schema already exists. Approve or delete it before creating a new one.",
            code="draft_exists",
            details=[{"schema_id": str(existing.id)}],
        )
    last = db.scalar(
        select(ExtractionSchema.version)
        .where(ExtractionSchema.project_id == project.id)
        .order_by(ExtractionSchema.version.desc())
        .limit(1)
    )
    version = (last or 0) + 1
    _cur = current_schema(db, project)
    _cur_id: uuid.UUID | None = _cur.id if _cur else None
    schema = ExtractionSchema(
        id=uuid.uuid4(),
        project_id=project.id,
        version=version,
        status="draft",
        field_definitions=field_definitions,
        question_map=question_map or {},
        notes=notes,
        change_reason=change_reason,
        created_by=user_id,
        supersedes_id=_cur_id,
    )
    db.add(schema)
    audit_service.record(
        db,
        action="extraction_schema.created",
        entity_type="extraction_schema",
        entity_id=schema.id,
        actor_id=user_id,
        project_id=project.id,
        after={"version": version, "field_count": len(field_definitions)},
    )
    db.commit()
    db.refresh(schema)
    return schema


def update_draft(
    db: Session,
    project: Project,
    schema_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    field_definitions: list[dict[str, Any]] | None = None,
    question_map: dict[str, list[str]] | None = None,
    notes: str | None = None,
    expected_version: int | None = None,
) -> ExtractionSchema:
    s = get_schema(db, project, schema_id)
    if s.status == "approved":
        raise Conflict("Approved schemas are immutable. Create a new version.", code="schema_approved")
    if expected_version is not None and s.row_version != expected_version:
        raise Conflict("Schema was changed elsewhere. Reload and try again.", code="version_conflict")
    if field_definitions is not None:
        _validate_fields(field_definitions)
        s.field_definitions = field_definitions
    if question_map is not None:
        s.question_map = question_map
    if notes is not None:
        s.notes = notes
    s.updated_at = _now()
    s.row_version += 1
    audit_service.record(
        db,
        action="extraction_schema.updated",
        entity_type="extraction_schema",
        entity_id=s.id,
        actor_id=user_id,
        project_id=project.id,
        after={"row_version": s.row_version},
    )
    db.commit()
    db.refresh(s)
    return s


def approve_schema(
    db: Session,
    project: Project,
    schema_id: uuid.UUID,
    user_id: uuid.UUID,
    expected_version: int | None = None,
) -> ExtractionSchema:
    s = get_schema(db, project, schema_id)
    if s.status == "approved":
        return s
    if not s.field_definitions:
        raise ValidationFailed("Cannot approve a schema with no fields.")
    if expected_version is not None and s.row_version != expected_version:
        raise Conflict("Schema was changed elsewhere. Reload and try again.", code="version_conflict")
    s.status = "approved"
    s.approved_by = user_id
    s.approved_at = _now()
    s.updated_at = _now()
    s.row_version += 1
    audit_service.record(
        db,
        action="extraction_schema.approved",
        entity_type="extraction_schema",
        entity_id=s.id,
        actor_id=user_id,
        project_id=project.id,
        after={"version": s.version},
    )
    db.commit()
    db.refresh(s)
    return s


def schema_view(s: ExtractionSchema) -> dict[str, Any]:
    return {
        "id": s.id,
        "version": s.version,
        "status": s.status,
        "field_definitions": s.field_definitions,
        "question_map": s.question_map,
        "notes": s.notes,
        "approved_by": s.approved_by,
        "approved_at": s.approved_at,
        "change_reason": s.change_reason,
        "supersedes_id": s.supersedes_id,
        "created_by": s.created_by,
        "row_version": s.row_version,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "field_count": len(s.field_definitions),
    }


# ---------- evidence items ----------


def upsert_evidence(
    db: Session,
    project: Project,
    *,
    paper_id: uuid.UUID,
    schema_id: uuid.UUID,
    field_key: str,
    status: str,
    value: Any | None = None,
    excerpt: str | None = None,
    document_page_id: uuid.UUID | None = None,
    reviewer_id: uuid.UUID | None = None,
    model_run_id: uuid.UUID | None = None,
    note: str | None = None,
    expected_row_version: int | None = None,
) -> EvidenceItem:
    """Create or update one evidence item. Checks excerpt occurs verbatim in cited page."""
    if status not in EVIDENCE_STATUSES:
        raise ValidationFailed(f"Unknown status '{status}'.")
    if status == "found" and value is None:
        raise ValidationFailed("A value is required when status is 'found'.")
    if status == "unavailable" and document_page_id is not None:
        raise ValidationFailed("Cannot cite a page when full text is unavailable.")

    # verify excerpt
    if excerpt and document_page_id:
        page = db.scalar(select(DocumentPage).where(DocumentPage.id == document_page_id))
        if page is None:
            raise NotFound("Document page not found.")
        if excerpt not in (page.text or ""):
            raise ValidationFailed(
                "The excerpt does not occur verbatim in the cited page. Copy it exactly from the page text.",
                details=[{"field": "excerpt", "issue": "not_found_in_page"}],
            )

    schema = db.scalar(
        select(ExtractionSchema).where(
            ExtractionSchema.id == schema_id,
            ExtractionSchema.project_id == project.id,
        )
    )
    if schema is None:
        raise NotFound("Extraction schema not found.")
    paper = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project.id))
    if paper is None:
        raise NotFound("Paper not found.")
    field_keys = {fd["field_key"] for fd in (schema.field_definitions or [])}
    if field_key not in field_keys:
        raise ValidationFailed(f"Field key '{field_key}' is not defined in this schema.")

    existing = db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.paper_id == paper_id,
            EvidenceItem.extraction_schema_id == schema_id,
            EvidenceItem.field_key == field_key,
        )
    )
    now = _now()
    if existing:
        if expected_row_version is not None and existing.row_version != expected_row_version:
            raise Conflict("Evidence item was changed elsewhere.", code="version_conflict")
        existing.status = status
        existing.value = value
        existing.excerpt = excerpt
        existing.document_page_id = document_page_id
        existing.reviewer_id = reviewer_id
        existing.model_run_id = model_run_id
        existing.note = note
        existing.updated_at = now
        existing.row_version += 1
        item = existing
    else:
        item = EvidenceItem(
            id=uuid.uuid4(),
            project_id=project.id,
            paper_id=paper_id,
            extraction_schema_id=schema_id,
            field_key=field_key,
            status=status,
            value=value,
            excerpt=excerpt,
            document_page_id=document_page_id,
            reviewer_id=reviewer_id,
            model_run_id=model_run_id,
            note=note,
        )
        db.add(item)
    db.commit()
    db.refresh(item)
    return item


def evidence_view(e: EvidenceItem) -> dict[str, Any]:
    return {
        "id": e.id,
        "paper_id": e.paper_id,
        "field_key": e.field_key,
        "status": e.status,
        "value": e.value,
        "excerpt": e.excerpt,
        "document_page_id": e.document_page_id,
        "reviewer_id": e.reviewer_id,
        "model_run_id": e.model_run_id,
        "note": e.note,
        "row_version": e.row_version,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }


def coverage_by_question(db: Session, project: Project, schema: ExtractionSchema) -> dict[str, Any]:
    """Per-question coverage: for each question, how many included papers have found vs missing evidence
    for the fields mapped to that question.

    Distinguishes:
      found         — evidence was extracted
      not_located   — full text was read but the information was not found
      unavailable   — no full text (never confuse with not_located)
      pending       — not yet extracted
    """
    from sqlalchemy import func

    from app.db.models import ScreeningState

    # included papers (full_text included state OR ta_included with no full_text stage)
    included_papers = list(
        db.scalars(
            select(ScreeningState.paper_id).where(
                ScreeningState.project_id == project.id,
                ScreeningState.stage == "full_text",
                ScreeningState.decision == "include",
            )
        )
    )
    if not included_papers:
        # fall back to ta-included for projects not yet at full-text stage
        included_papers = list(
            db.scalars(
                select(ScreeningState.paper_id).where(
                    ScreeningState.project_id == project.id,
                    ScreeningState.stage == "title_abstract",
                    ScreeningState.decision == "include",
                )
            )
        )

    q_map: dict[str, list[str]] = schema.question_map or {}  # {field_key: [question_id, ...]}
    all_fields = {fd["field_key"]: fd for fd in (schema.field_definitions or [])}

    # aggregate evidence statuses per field
    rows = db.execute(
        select(
            EvidenceItem.field_key,
            EvidenceItem.status,
            func.count().label("n"),
        )
        .where(
            EvidenceItem.extraction_schema_id == schema.id,
            EvidenceItem.paper_id.in_(included_papers),
        )
        .group_by(EvidenceItem.field_key, EvidenceItem.status)
    ).all()

    field_counts: dict[str, dict[str, int]] = {}
    for field_key, status, n in rows:
        fc = field_counts.setdefault(field_key, {})
        fc[status] = n

    # invert question_map: question_id → [field_key]
    q_to_fields: dict[str, list[str]] = {}
    for field_key, q_ids in q_map.items():
        for qid in q_ids or []:
            q_to_fields.setdefault(qid, []).append(field_key)

    def _field_coverage(field_key: str) -> dict[str, Any]:
        fc = field_counts.get(field_key, {})
        n_included = len(included_papers)
        n_found = fc.get("found", 0)
        n_not_located = fc.get("not_located", 0)
        n_unavailable = fc.get("unavailable", 0)
        n_pending = n_included - n_found - n_not_located - n_unavailable - fc.get("not_applicable", 0)
        return {
            "field_key": field_key,
            "label": all_fields.get(field_key, {}).get("label", field_key),
            "included_papers": n_included,
            "found": n_found,
            "not_located": n_not_located,
            "unavailable": n_unavailable,
            "pending": max(n_pending, 0),
        }

    questions_out: dict[str, Any] = {}
    for qid, fkeys in q_to_fields.items():
        questions_out[qid] = {
            "question_id": qid,
            "fields": [_field_coverage(fk) for fk in fkeys],
        }
    # fields not mapped to any question
    unmapped = [fk for fk in all_fields if fk not in q_map]
    return {
        "schema_id": schema.id,
        "schema_version": schema.version,
        "included_papers": len(included_papers),
        "by_question": questions_out,
        "unmapped_fields": [_field_coverage(fk) for fk in unmapped],
    }
