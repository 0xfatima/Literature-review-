"""Report drafts: generation from stored data, immutable versions (every edit creates a version), live validation,
citation links, exports (DOCX, Markdown, supplementary ZIP with CSVs + PRISMA JSON + manifest, PRISMA JSON) recorded
with their hash and the review state, and tracing of any claim to its references and evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import (
    ClaimEvidenceLink,
    DocumentPage,
    DraftVersion,
    EvidenceItem,
    PaperRecord,
    Project,
    ReportExport,
    User,
)
from app.reports import build as rb
from app.reports import render
from app.reports.validate import cite_keys, validate
from app.services import audit_service, protocol_service

MAX_OPS = 200
MAX_TEXT = 10000
FORMATS = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "supplementary_zip": ("application/zip", "zip"),
    "prisma_json": ("application/json", "json"),
}
EDITABLE = ("paragraph", "statement")


def _now() -> datetime:
    return datetime.now(UTC)


def _latest(db: Session, project: Project) -> DraftVersion | None:
    return db.scalar(
        select(DraftVersion).where(DraftVersion.project_id == project.id).order_by(DraftVersion.version.desc()).limit(1)
    )


def get(db: Session, project: Project, draft_id: uuid.UUID) -> DraftVersion:
    v = db.scalar(select(DraftVersion).where(DraftVersion.id == draft_id, DraftVersion.project_id == project.id))
    if v is None:
        raise NotFound("Report version not found.")
    return v


def list_versions(db: Session, project: Project) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(DraftVersion).where(DraftVersion.project_id == project.id).order_by(DraftVersion.version.desc())
    )
    return [
        {
            "id": v.id,
            "version": v.version,
            "status": v.status,
            "title": v.title,
            "change_summary": v.change_summary,
            "summary": (v.validation or {}).get("summary"),
            "created_at": v.created_at,
            "created_by": v.created_by,
        }
        for v in rows
    ]


def _validate(db: Session, v: dict[str, Any], schema_id: uuid.UUID | None) -> dict[str, Any]:
    refs = v["references"]
    return validate(
        v["sections"],
        refs,
        (v.get("review_state") or {}).get("prisma") or {},
        rb.evidence_index(db, schema_id),
        rb.paper_index(db, [r["paper_id"] for r in refs]),
    )


def _links(db: Session, draft: DraftVersion) -> None:
    key_to_paper = {r["key"]: r["paper_id"] for r in draft.references}
    paper_to_key = {p: k for k, p in key_to_paper.items()}
    existing = rb.paper_index(db, list(key_to_paper.values()))
    item_ids = [uuid.UUID(e["item_id"]) for s in draft.sections for b in s["blocks"] for e in b.get("evidence") or []]
    pages = (
        {
            str(i): p
            for i, p in db.execute(
                select(EvidenceItem.id, DocumentPage.page_no)
                .outerjoin(DocumentPage, DocumentPage.id == EvidenceItem.document_page_id)
                .where(EvidenceItem.id.in_(item_ids))
            ).all()
        }
        if item_ids
        else {}
    )
    for s in draft.sections:
        for b in s["blocks"]:
            seen: set[tuple[str, str | None]] = set()
            for e in b.get("evidence") or []:
                p = str(e["paper_id"])
                if not existing.get(p, {}).get("exists"):
                    continue
                seen.add((p, e["item_id"]))
                page = pages.get(str(e["item_id"]))
                db.add(
                    ClaimEvidenceLink(
                        draft_version_id=draft.id,
                        claim_id=b["id"],
                        paper_id=uuid.UUID(p),
                        evidence_item_id=uuid.UUID(e["item_id"]) if str(e["item_id"]) in pages else None,
                        evidence_revision=e.get("revision"),
                        citation_key=paper_to_key.get(p, ""),
                        citation_locator=f"p. {page}" if page else None,
                    )
                )
            keys = cite_keys(b.get("text") or "")
            if b.get("type") == "table":  # study columns hold "[key]"
                keys = [
                    c[1:-1]
                    for row in b["table"].get("rows", [])
                    for c in row
                    if isinstance(c, str) and c.startswith("[") and c.endswith("]")
                ]
            for k in dict.fromkeys(keys):
                kp = key_to_paper.get(k)
                if kp and existing.get(kp, {}).get("exists") and not any(x[0] == kp for x in seen):
                    db.add(
                        ClaimEvidenceLink(
                            draft_version_id=draft.id, claim_id=b["id"], paper_id=uuid.UUID(kp), citation_key=k
                        )
                    )


def _store(
    db: Session,
    project: Project,
    user: User,
    data: dict[str, Any],
    *,
    change_summary: str | None,
    supersedes: DraftVersion | None,
) -> DraftVersion:
    latest = _latest(db, project)
    for old in db.scalars(
        select(DraftVersion).where(DraftVersion.project_id == project.id, DraftVersion.status == "current")
    ):
        old.status = "superseded"
    v = DraftVersion(
        id=uuid.uuid4(),
        project_id=project.id,
        version=(latest.version if latest else 0) + 1,
        status="current",
        title=data["title"],
        sections=data["sections"],
        references=data["references"],
        review_state=data["review_state"],
        protocol_version_id=data.get("protocol_version_id"),
        extraction_schema_id=data.get("extraction_schema_id"),
        synthesis_version_id=data.get("synthesis_version_id"),
        supersedes_id=supersedes.id if supersedes else None,
        change_summary=change_summary,
        created_by=user.id,
    )
    v.validation = _validate(db, data, v.extraction_schema_id)
    db.add(v)
    db.flush()
    _links(db, v)
    audit_service.record(
        db,
        action="report.version_created",
        entity_type="draft_version",
        entity_id=v.id,
        actor_id=user.id,
        project_id=project.id,
        after={"version": v.version, "summary": v.validation["summary"], "change": change_summary},
    )
    db.commit()
    db.refresh(v)
    return v


def generate(db: Session, project: Project, user: User, body: dict[str, Any]) -> DraftVersion:
    protocol_service.require_writable(project)
    try:
        data = rb.build(db, project)
    except ValueError:
        raise Conflict(
            "Approve the review protocol before generating a report.", code="protocol_not_approved"
        ) from None
    note = (str(body.get("note") or "").strip() or None) if body.get("note") else None
    return _store(
        db,
        project,
        user,
        data,
        change_summary=note or "Generated from the current review data",
        supersedes=_latest(db, project),
    )


def view(db: Session, v: DraftVersion) -> dict[str, Any]:
    project = db.get(Project, v.project_id)
    current = rb.review_state(db, project) if project else {}
    live = _validate(
        db, {"sections": v.sections, "references": v.references, "review_state": v.review_state}, v.extraction_schema_id
    )
    return {
        "id": v.id,
        "version": v.version,
        "status": v.status,
        "title": v.title,
        "sections": v.sections,
        "references": v.references,
        "review_state": v.review_state,
        "data_changed_since_generation": current.get("fingerprint") != (v.review_state or {}).get("fingerprint"),
        "validation": live,
        "validation_at_creation": (v.validation or {}).get("summary"),
        "change_summary": v.change_summary,
        "supersedes_id": v.supersedes_id,
        "synthesis_version_id": v.synthesis_version_id,
        "protocol_version_id": v.protocol_version_id,
        "extraction_schema_id": v.extraction_schema_id,
        "created_at": v.created_at,
        "created_by": v.created_by,
    }


# ---------- edits ----------


def _find(sections: list[dict[str, Any]], block_id: Any) -> tuple[dict[str, Any], int]:
    for s in sections:
        for i, b in enumerate(s["blocks"]):
            if b.get("id") == block_id:
                return s, i
    raise ValidationFailed(f"Block '{str(block_id)[:40]}' not found.", details=[{"field": "block_id"}])


def _text(op: dict[str, Any]) -> str:
    t = str(op.get("text") or "")
    if not t.strip() or len(t) > MAX_TEXT:
        raise ValidationFailed(f"Text is required (max {MAX_TEXT} characters).", details=[{"field": "text"}])
    return t


def edit(db: Session, project: Project, user: User, draft_id: uuid.UUID, body: dict[str, Any]) -> DraftVersion:
    base = get(db, project, draft_id)
    protocol_service.require_writable(project)
    latest = _latest(db, project)
    if latest is not None and body.get("expected_latest_version") not in (None, latest.version):
        raise Conflict(
            "The report changed since you opened it. Reload and reapply your edits.", code="version_conflict"
        )
    ops = body.get("ops")
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS:
        raise ValidationFailed(f"ops must be a list of 1-{MAX_OPS} edits.", details=[{"field": "ops"}])
    sections = copy.deepcopy(base.sections)
    refs = copy.deepcopy(base.references)
    title = base.title
    by_key = {s["key"]: s for s in sections}
    touched: set[str] = set()
    for op in ops:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "set_title":
            title = _text(op)[:500]
        elif kind == "set_text":
            sec, i = _find(sections, op.get("block_id"))
            b = sec["blocks"][i]
            if b.get("type") not in EDITABLE:
                raise ValidationFailed("Only paragraphs and statements can be edited; tables come from stored data.")
            sec["blocks"][i] = {**b, "text": _text(op), "origin": "edited"}
            touched.add(sec["key"])
        elif kind == "add_paragraph":
            target = by_key.get(str(op.get("section")))
            if target is None or target["key"] == "references":
                raise ValidationFailed("Unknown or read-only section.", details=[{"field": "section"}])
            sec = target
            block = {"id": rb._bid(), "type": "paragraph", "text": _text(op), "source": "author", "origin": "edited"}
            after = op.get("after_block_id")
            idx = next((i + 1 for i, b in enumerate(sec["blocks"]) if b.get("id") == after), len(sec["blocks"]))
            sec["blocks"].insert(idx, block)
            touched.add(sec["key"])
        elif kind == "remove_block":
            sec, i = _find(sections, op.get("block_id"))
            if sec["blocks"][i].get("type") not in EDITABLE:
                raise ValidationFailed("Tables and the reference list come from stored data and cannot be removed.")
            sec["blocks"].pop(i)
            touched.add(sec["key"])
        elif kind == "add_reference":
            try:
                pid = uuid.UUID(str(op.get("paper_id")))
            except ValueError:
                raise ValidationFailed("paper_id must be a UUID.") from None
            paper = db.scalar(select(PaperRecord).where(PaperRecord.id == pid, PaperRecord.project_id == project.id))
            if paper is None:
                raise NotFound("Paper record not found in this project.")
            if not any(r["paper_id"] == str(pid) for r in refs):
                existing = {r["key"] for r in refs}
                key = rb.build_references([paper])[0]["key"]
                n = 0
                while key in existing:
                    n += 1
                    key = f"{rb.build_references([paper])[0]['key']}x{n}"
                refs.append({**rb.build_references([paper])[0], "key": key})
        elif kind == "verify_section":
            vsec = by_key.get(str(op.get("section")))
            if vsec is None:
                raise ValidationFailed("Unknown section.", details=[{"field": "section"}])
            sec = vsec
            if op.get("verified"):
                sec["verified_by"], sec["verified_at"] = str(user.id), _now().isoformat()
            else:
                sec["verified_by"], sec["verified_at"] = None, None
        else:
            raise ValidationFailed(
                "op must be one of set_title, set_text, add_paragraph, remove_block, add_reference, verify_section."
            )
    for k in touched:  # a content change invalidates that section's human verification
        if not any(o.get("op") == "verify_section" and o.get("section") == k and o.get("verified") for o in ops):
            by_key[k]["verified_by"], by_key[k]["verified_at"] = None, None
    summary = (str(body.get("change_summary") or "").strip() or None) if body.get("change_summary") else None
    data = {
        "title": title,
        "sections": sections,
        "references": refs,
        "review_state": base.review_state,
        "protocol_version_id": base.protocol_version_id,
        "extraction_schema_id": base.extraction_schema_id,
        "synthesis_version_id": base.synthesis_version_id,
    }
    return _store(
        db, project, user, data, change_summary=summary or f"{len(ops)} edit(s) to v{base.version}", supersedes=base
    )


# ---------- exports ----------


def _render_input(db: Session, v: DraftVersion) -> dict[str, Any]:
    return {
        "title": v.title,
        "version": v.version,
        "sections": v.sections,
        "references": v.references,
        "validation": view(db, v)["validation"],
    }


def export(
    db: Session, project: Project, user: User, draft_id: uuid.UUID, fmt: str
) -> tuple[bytes, str, str, ReportExport]:
    v = get(db, project, draft_id)
    if fmt not in FORMATS:
        raise ValidationFailed(f"format must be one of {', '.join(FORMATS)}.", details=[{"field": "format"}])
    media, ext = FORMATS[fmt]
    data = _render_input(db, v)
    current = rb.review_state(db, project)
    changed = current.get("fingerprint") != (v.review_state or {}).get("fingerprint")
    export_id = uuid.uuid4()
    manifest = {
        "export_id": str(export_id),
        "format": fmt,
        "draft_version": v.version,
        "draft_version_id": str(v.id),
        "exported_at": _now().isoformat(),
        "review_state_at_draft": v.review_state,
        "data_changed_since_draft": changed,
        "validation": data["validation"]["summary"],
        "notice": render.notice(data),
    }
    prisma = {"counts": (v.review_state or {}).get("prisma"), "manifest": manifest}
    if fmt == "docx":
        blob = render.render_docx(data)
    elif fmt == "markdown":
        blob = render.render_markdown(data).encode("utf-8")
    elif fmt == "prisma_json":
        blob = json.dumps(prisma, indent=2, sort_keys=True, default=str).encode("utf-8")
    else:
        files: dict[str, str | bytes] = {
            "README.txt": render.notice(data) + "\n",
            "manifest.json": json.dumps(manifest, indent=2, sort_keys=True, default=str),
            "report.md": render.render_markdown(data),
            "report.docx": render.render_docx(data),
            "prisma_flow.json": json.dumps(prisma, indent=2, sort_keys=True, default=str),
            "references.csv": render.references_csv(v.references),
        }
        for s in v.sections:
            for b in s["blocks"]:
                if b.get("type") == "table":
                    files[f"tables/{b['table']['key']}.csv"] = render.table_csv(b["table"])
        blob = render.zip_bytes(files)
    filename = f"review-draft-v{v.version}.{ext}" if fmt != "prisma_json" else f"prisma-flow-v{v.version}.json"
    rec = ReportExport(
        id=export_id,
        project_id=project.id,
        draft_version_id=v.id,
        format=fmt,
        filename=filename,
        sha256=hashlib.sha256(blob).hexdigest(),
        byte_size=len(blob),
        review_state=v.review_state,
        validation_summary=data["validation"]["summary"],
        data_changed_since_draft=changed,
        created_by=user.id,
    )
    db.add(rec)
    audit_service.record(
        db,
        action="report.exported",
        entity_type="report_export",
        entity_id=rec.id,
        actor_id=user.id,
        project_id=project.id,
        after={"format": fmt, "version": v.version, "sha256": rec.sha256, "data_changed": changed},
    )
    db.commit()
    return blob, filename, media, rec


def list_exports(db: Session, project: Project, draft_id: uuid.UUID) -> list[dict[str, Any]]:
    get(db, project, draft_id)
    return [
        {
            "id": e.id,
            "format": e.format,
            "filename": e.filename,
            "sha256": e.sha256,
            "byte_size": e.byte_size,
            "validation_summary": e.validation_summary,
            "data_changed_since_draft": e.data_changed_since_draft,
            "created_at": e.created_at,
        }
        for e in db.scalars(
            select(ReportExport)
            .where(ReportExport.draft_version_id == draft_id)
            .order_by(ReportExport.created_at.desc())
        )
    ]


# ---------- tracing ----------


def claims(db: Session, project: Project, draft_id: uuid.UUID) -> dict[str, Any]:
    """Every claim of a version with its number of linked references and evidence items."""
    v = get(db, project, draft_id)
    counts: dict[str, dict[str, int]] = {}
    for lk in db.scalars(select(ClaimEvidenceLink).where(ClaimEvidenceLink.draft_version_id == v.id)):
        c = counts.setdefault(lk.claim_id, {"references": 0, "evidence": 0})
        c["references"] += 1
        c["evidence"] += 1 if lk.evidence_item_id else 0
    return {
        "claims": [
            {
                "claim_id": b["id"],
                "section": s["key"],
                "type": b.get("type"),
                "source": b.get("source"),
                **counts.get(b["id"], {"references": 0, "evidence": 0}),
            }
            for s in v.sections
            for b in s["blocks"]
        ]
    }


def trace(db: Session, project: Project, draft_id: uuid.UUID, claim_id: str) -> dict[str, Any]:
    v = get(db, project, draft_id)
    block = next((b for s in v.sections for b in s["blocks"] if b.get("id") == claim_id), None)
    if block is None:
        raise NotFound("Claim not found in this version.")
    links = list(
        db.scalars(
            select(ClaimEvidenceLink).where(
                ClaimEvidenceLink.draft_version_id == v.id, ClaimEvidenceLink.claim_id == claim_id
            )
        )
    )
    papers = (
        {p.id: p for p in db.scalars(select(PaperRecord).where(PaperRecord.id.in_([lk.paper_id for lk in links])))}
        if links
        else {}
    )
    items = (
        {
            i.id: (i, pg)
            for i, pg in db.execute(
                select(EvidenceItem, DocumentPage)
                .outerjoin(DocumentPage, DocumentPage.id == EvidenceItem.document_page_id)
                .where(EvidenceItem.id.in_([lk.evidence_item_id for lk in links if lk.evidence_item_id]))
            ).all()
        }
        if any(lk.evidence_item_id for lk in links)
        else {}
    )
    out_refs: dict[str, dict[str, Any]] = {}
    evidence = []
    for lk in links:
        p = papers.get(lk.paper_id)
        if p is not None and str(p.id) not in out_refs:
            out_refs[str(p.id)] = {
                "key": lk.citation_key,
                "paper_id": p.id,
                "title": p.title,
                "year": p.year,
                "venue": p.venue,
                "doi": p.doi,
                "pmid": p.pmid,
                "providers": p.providers,
                "is_retracted": p.is_retracted,
                "formatted": rb.format_reference(p),
            }
        if lk.evidence_item_id and lk.evidence_item_id in items:
            it, pg = items[lk.evidence_item_id]
            evidence.append(
                {
                    "evidence_item_id": it.id,
                    "paper_id": it.paper_id,
                    "field_key": it.field_key,
                    "status": it.status,
                    "value": it.value,
                    "excerpt": it.excerpt,
                    "page_no": pg.page_no if pg else None,
                    "document_id": pg.document_id if pg else None,
                    "review_status": it.review_status,
                    "cited_revision": lk.evidence_revision,
                    "current_revision": it.row_version,
                    "changed_since_cited": lk.evidence_revision != it.row_version,
                }
            )
    src = str(block.get("source") or "")
    explanation = {
        "data:prisma": "Counts computed by the application from the current record status events (PRISMA 2020).",
        "data:protocol": "Taken from the approved protocol version recorded for this draft.",
        "data:search_queries": "Recorded search queries: provider, exact query text, date and result counts.",
        "data:evidence": "Reviewed extraction values; each links to its page and excerpt.",
        "data:appraisal": "Approved appraisal judgements only.",
        "data:screening": "Screening decisions with their recorded reasons.",
        "placeholder": "Text the authors still have to write.",
        "author": "Written by an author in this application.",
    }
    return {
        "claim_id": claim_id,
        "block": block,
        "source_explanation": next(
            (t for k, t in explanation.items() if src.startswith(k)),
            "From the accepted synthesis version." if src.startswith("synthesis:") else None,
        ),
        "facts": (v.review_state or {}).get("prisma") if src.startswith("data:prisma") else None,
        "references": list(out_refs.values()),
        "evidence": evidence,
    }
