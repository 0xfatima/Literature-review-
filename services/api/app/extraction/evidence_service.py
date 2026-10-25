"""The evidence table as a first-class feature: rows of included papers x approved fields, each cell traceable to its
page and excerpt; per-claim history (revisions, the source page, the outputs generated from it); CSV export that
records which revision of each claim it used; and the list of outputs made stale by later edits or schema versions."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import (
    DocumentPage,
    EvidenceItem,
    EvidenceItemRevision,
    EvidenceOutput,
    EvidenceOutputItem,
    ExtractionSchema,
    ModelRun,
    PaperDocument,
    PaperRecord,
    Project,
    User,
)
from app.extraction import schema_service as ss
from app.extraction.extractor import schema_diff
from app.services import audit_service


def _authors(p: PaperRecord) -> str:
    names = [str(a.get("name") or a.get("family") or "") for a in (p.authors or []) if isinstance(a, dict)]
    names = [n for n in names if n]
    return (names[0] + (" et al." if len(names) > 1 else "")) if names else ""


def _paper_ids(db: Session, project: Project, schema: ExtractionSchema) -> list[uuid.UUID]:
    """Included papers plus any paper that already has evidence under this schema (inclusion may have changed)."""
    ids = set(ss.included_paper_ids(db, project))
    ids |= set(db.scalars(select(EvidenceItem.paper_id).where(EvidenceItem.extraction_schema_id == schema.id)))
    return list(ids)


def _cells(db: Session, schema: ExtractionSchema, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    rows = db.execute(
        select(EvidenceItem, DocumentPage)
        .outerjoin(DocumentPage, DocumentPage.id == EvidenceItem.document_page_id)
        .where(EvidenceItem.extraction_schema_id == schema.id, EvidenceItem.paper_id.in_(paper_ids))
    ).all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for item, page in rows:
        out.setdefault(item.paper_id, {})[item.field_key] = ss.evidence_view(item, page)
    return out


def table(db: Session, project: Project, schema: ExtractionSchema, offset: int, limit: int) -> dict[str, Any]:
    ids = _paper_ids(db, project, schema)
    included = set(ss.included_paper_ids(db, project))
    papers = list(
        db.scalars(
            select(PaperRecord)
            .where(PaperRecord.project_id == project.id, PaperRecord.id.in_(ids))
            .order_by(PaperRecord.title, PaperRecord.id)
            .offset(offset)
            .limit(limit)
        )
    )
    cells = _cells(db, schema, [p.id for p in papers])
    from app.services.extraction_service import full_text  # the document extraction would read

    docs = {p.id: full_text(db, p.id) for p in papers}
    summary = {
        f"{status}": n
        for status, n in db.execute(
            select(EvidenceItem.status, func.count())
            .where(EvidenceItem.extraction_schema_id == schema.id)
            .group_by(EvidenceItem.status)
        ).all()
    }
    needs_review = (
        db.scalar(
            select(func.count()).where(
                EvidenceItem.extraction_schema_id == schema.id,
                EvidenceItem.origin.in_(("ai", "system")),
                EvidenceItem.review_status == "unreviewed",
                EvidenceItem.status != "pending",
            )
        )
        or 0
    )
    return {
        "schema_id": schema.id,
        "schema_version": schema.version,
        "schema_status": schema.status,
        "fields": schema.field_definitions,
        "total": len(ids),
        "offset": offset,
        "limit": limit,
        "status_counts": summary,
        "needs_review": needs_review,
        "rows": [
            {
                "paper": {
                    "id": p.id,
                    "title": p.title,
                    "year": p.year,
                    "doi": p.doi,
                    "first_author": _authors(p),
                    "full_text_status": p.full_text_status,
                    "included": p.id in included,
                    "document_id": (doc.id if (doc := docs[p.id]) is not None else None),
                },
                "cells": cells.get(p.id, {}),
            }
            for p in papers
        ],
    }


def history(db: Session, project: Project, schema_id: uuid.UUID, paper_id: uuid.UUID, field_key: str) -> dict[str, Any]:
    ss.get_schema(db, project, schema_id)
    paper = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project.id))
    if paper is None:
        raise NotFound("Paper not found.")
    item = db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.extraction_schema_id == schema_id,
            EvidenceItem.paper_id == paper_id,
            EvidenceItem.field_key == field_key,
        )
    )
    if item is None:
        return {"item": None, "revisions": [], "outputs": [], "source_page": None, "model_run": None}
    page_doc = (
        db.execute(
            select(DocumentPage, PaperDocument)
            .join(PaperDocument, PaperDocument.id == DocumentPage.document_id)
            .where(DocumentPage.id == item.document_page_id)
        ).first()
        if item.document_page_id
        else None
    )
    page = page_doc[0] if page_doc else None
    revisions = list(
        db.scalars(
            select(EvidenceItemRevision)
            .where(EvidenceItemRevision.evidence_item_id == item.id)
            .order_by(EvidenceItemRevision.revision)
        )
    )
    outputs = db.execute(
        select(EvidenceOutput, EvidenceOutputItem.revision)
        .join(EvidenceOutputItem, EvidenceOutputItem.evidence_output_id == EvidenceOutput.id)
        .where(EvidenceOutputItem.evidence_item_id == item.id)
        .order_by(EvidenceOutput.created_at.desc())
    ).all()
    mr = db.get(ModelRun, item.model_run_id) if item.model_run_id else None
    return {
        "item": ss.evidence_view(item, page),
        "revisions": [
            {
                "revision": r.revision,
                "action": r.action,
                "before": r.before,
                "after": r.after,
                "actor_id": r.actor_id,
                "model_run_id": r.model_run_id,
                "reason": r.reason,
                "at": r.at,
            }
            for r in revisions
        ],
        "outputs": [
            {
                "id": o.id,
                "kind": o.kind,
                "created_at": o.created_at,
                "revision": rev,
                "stale": rev < item.row_version,
            }
            for o, rev in outputs
        ],
        "source_page": (
            {
                "document_id": page_doc[1].id,
                "filename": page_doc[1].original_filename,
                "page_no": page_doc[0].page_no,
                "text_version": page_doc[0].text_version,
                "is_current_text": page_doc[0].text_version == page_doc[1].text_version,
                "text": page_doc[0].text,
            }
            if page_doc
            else None
        ),
        "model_run": (
            {
                "id": mr.id,
                "provider": mr.provider,
                "model": mr.model,
                "prompt_version": mr.prompt_version,
                "created_at": mr.created_at,
            }
            if mr
            else None
        ),
    }


def _safe(v: Any) -> str:
    """CSV cell; neutralises spreadsheet formula injection."""
    if v is None:
        return ""
    if isinstance(v, list):
        s = "; ".join(str(x) for x in v)
    elif isinstance(v, dict):
        s = "; ".join(str(x) for x in v.values())
    else:
        s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(db: Session, project: Project, schema: ExtractionSchema, user: User) -> tuple[str, EvidenceOutput]:
    if schema.status != "approved":
        raise Conflict("Only an approved schema's evidence table can be exported.", code="schema_not_approved")
    ids = _paper_ids(db, project, schema)
    papers = list(
        db.scalars(
            select(PaperRecord)
            .where(PaperRecord.project_id == project.id, PaperRecord.id.in_(ids))
            .order_by(PaperRecord.title, PaperRecord.id)
        )
    )
    cells = _cells(db, schema, ids)
    fields = list(schema.field_definitions or [])
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["paper_id", "title", "first_author", "year", "doi"]
    for fd in fields:
        lab = fd.get("label") or fd["field_key"]
        header += [lab, f"{lab} [status]", f"{lab} [page]", f"{lab} [excerpt]", f"{lab} [review]"]
    w.writerow(header)
    used: list[tuple[uuid.UUID, int]] = []
    for p in papers:
        row = [str(p.id), _safe(p.title), _safe(_authors(p)), p.year or "", _safe(p.doi)]
        for fd in fields:
            c = cells.get(p.id, {}).get(fd["field_key"])
            if c is None:
                row += ["", "pending", "", "", ""]
                continue
            row += [
                _safe(c["value"]),
                c["status"],
                c["page_no"] or "",
                _safe(c["excerpt"]),
                f"{c['origin']}/{c['review_status']}",
            ]
            if c["status"] != "pending":
                used.append((c["id"], c["row_version"]))
        w.writerow(row)
    out = EvidenceOutput(
        id=uuid.uuid4(),
        project_id=project.id,
        extraction_schema_id=schema.id,
        kind="csv_export",
        created_by=user.id,
        item_count=len(used),
    )
    db.add(out)
    db.flush()
    for item_id, rev in used:
        db.add(EvidenceOutputItem(evidence_output_id=out.id, evidence_item_id=item_id, revision=rev))
    audit_service.record(
        db,
        action="evidence.exported",
        entity_type="evidence_output",
        entity_id=out.id,
        actor_id=user.id,
        project_id=project.id,
        after={"schema_version": schema.version, "items": len(used), "papers": len(papers)},
    )
    db.commit()
    return buf.getvalue(), out


def affected(db: Session, project: Project, schema: ExtractionSchema) -> dict[str, Any]:
    """Downstream results to regenerate: outputs built from claims that changed since, outputs built on a superseded
    schema version, and fields of this version that the previous version did not have or defined differently."""
    stale_rows = db.execute(
        select(EvidenceOutput, func.count(EvidenceOutputItem.id))
        .join(EvidenceOutputItem, EvidenceOutputItem.evidence_output_id == EvidenceOutput.id)
        .join(EvidenceItem, EvidenceItem.id == EvidenceOutputItem.evidence_item_id)
        .where(
            EvidenceOutput.project_id == project.id,
            EvidenceOutput.extraction_schema_id == schema.id,
            EvidenceItem.row_version > EvidenceOutputItem.revision,
        )
        .group_by(EvidenceOutput.id)
        .order_by(EvidenceOutput.created_at.desc())
    ).all()
    superseded: dict[str, Any] | None = None
    if schema.supersedes_id:
        prev = db.get(ExtractionSchema, schema.supersedes_id)
        if prev is not None and prev.project_id == project.id:
            diff = schema_diff(list(prev.field_definitions or []), list(schema.field_definitions or []))
            prev_outputs = db.scalar(select(func.count()).where(EvidenceOutput.extraction_schema_id == prev.id)) or 0
            prev_items = db.scalar(select(func.count()).where(EvidenceItem.extraction_schema_id == prev.id)) or 0
            superseded = {
                "schema_id": prev.id,
                "version": prev.version,
                "diff": diff,
                "evidence_items": prev_items,
                "outputs": prev_outputs,
                "rerun_fields": diff["added"] + diff["changed"],
            }
    return {
        "schema_id": schema.id,
        "stale_outputs": [
            {"id": o.id, "kind": o.kind, "created_at": o.created_at, "changed_items": n} for o, n in stale_rows
        ],
        "superseded_schema": superseded,
    }
