"""Evidence synthesis: comparison tables from reviewed extraction data, versioned narrative syntheses whose statements
cite evidence (with the revision used) and approved appraisals, statement-level review, acceptance, and the
background job that drafts a synthesis (deterministic statements, plus optional AI statements behind the consent
gates). Certainty is always computed by app.synthesis.signals and can only be lowered by a person or a model."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.appraisal import service as appraisal_service
from app.core.config import Settings
from app.core.errors import Conflict, NotFound, RateLimited, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import (
    DocumentPage,
    EvidenceItem,
    EvidenceOutput,
    EvidenceOutputItem,
    ExtractionSchema,
    Job,
    PaperRecord,
    Project,
    SynthesisVersion,
    User,
)
from app.extraction import schema_service
from app.jobs import fsm
from app.providers.llm import LLMError, LLMGateway, build_gateway, describe
from app.repositories import jobs as job_repo
from app.services import audit_service, consent_service, job_service, llm_jobs, protocol_service
from app.services.job_service import Dispatcher
from app.synthesis import ai
from app.synthesis import signals as sig

JOB_TYPE = "synthesis.draft"
PURPOSE = "synthesis_draft"
MAX_PROMPT_CHARS = 60000
REVIEW_ACTIONS = ("accept", "reject", "edit", "add", "refresh")
log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _schema(db: Session, project: Project, schema_id: uuid.UUID | None) -> ExtractionSchema | None:
    if schema_id is not None:
        return schema_service.get_schema(db, project, schema_id)
    return schema_service.current_schema(db, project)


def _design_key(fields: list[dict[str, Any]]) -> str | None:
    return next(
        (
            f["field_key"]
            for f in fields
            if "design" in f["field_key"].lower() or "design" in str(f.get("label")).lower()
        ),
        None,
    )


def load_studies(db: Session, project: Project, schema: ExtractionSchema) -> list[sig.Study]:
    ids = schema_service.included_paper_ids(db, project)
    papers = list(
        db.scalars(select(PaperRecord).where(PaperRecord.id.in_(ids)).order_by(PaperRecord.title, PaperRecord.id))
    )
    rows = db.execute(
        select(EvidenceItem, DocumentPage.page_no)
        .outerjoin(DocumentPage, DocumentPage.id == EvidenceItem.document_page_id)
        .where(EvidenceItem.extraction_schema_id == schema.id, EvidenceItem.paper_id.in_(ids))
    ).all()
    cells: dict[uuid.UUID, dict[str, sig.Cell]] = {}
    for item, page_no in rows:
        cells.setdefault(item.paper_id, {})[item.field_key] = sig.Cell(
            str(item.id),
            item.field_key,
            item.row_version,
            item.status,
            item.value,
            item.excerpt,
            page_no,
            item.review_status,
        )
    apps = appraisal_service.approved_by_paper(db, ids)
    out = []
    for p in papers:
        a = apps.get(p.id)
        out.append(
            sig.Study(
                str(p.id),
                p.title,
                p.year,
                cells.get(p.id, {}),
                {
                    "record_id": str(a.id),
                    "revision": a.row_version,
                    "framework_key": a.framework_key,
                    "overall": a.overall_judgement,
                }
                if a
                else None,
            )
        )
    return out


def _question_labels(db: Session, project: Project) -> dict[str, str]:
    pv = protocol_service.approved(db, project)
    return {q["stable_key"]: q["text"] for q in protocol_service.to_content(pv)["questions"]} if pv else {}


def comparison(db: Session, project: Project, schema_id: uuid.UUID | None, group_by: str | None) -> dict[str, Any]:
    schema = _schema(db, project, schema_id)
    if schema is None or schema.status != "approved":
        return {"schema": None, "groups": [], "studies": [], "meta_analysis": sig.META_ANALYSIS, "group_options": []}
    fields = list(schema.field_definitions or [])
    keys = {f["field_key"] for f in fields}
    qlabels = _question_labels(db, project)
    if group_by and not group_by.startswith("question:") and group_by not in keys:
        raise ValidationFailed(
            "group_by must be a field of the schema or question:<id>.", details=[{"field": "group_by"}]
        )
    studies = load_studies(db, project, schema)
    groups = sig.group_studies(studies, fields, group_by, schema.question_map or {})
    by_id = {s.paper_id: s for s in studies}
    fdefs = {f["field_key"]: f for f in fields}
    out_groups = []
    for g in groups:
        members = [by_id[p] for p in g["paper_ids"] if p in by_id]
        label = g["label"]
        if g["key"].startswith("question:"):
            label = qlabels.get(g["key"].split(":", 1)[1], "Research question")
        out_groups.append(
            {
                "key": g["key"],
                "label": label,
                "paper_ids": g["paper_ids"],
                "fields": [sig.field_signal(fdefs[k], members) for k in g["field_keys"]],
            }
        )
    unreviewed = sum(1 for s in studies for c in s.cells.values() if c.status != "pending" and not sig.reviewed(c))
    question_ids = sorted({q for qs in (schema.question_map or {}).values() for q in (qs or [])})
    return {
        "schema": {"id": schema.id, "version": schema.version, "fields": fields},
        "group_by": group_by,
        "groups": out_groups,
        "studies": [
            {
                "paper_id": s.paper_id,
                "title": s.title,
                "year": s.year,
                "appraisal": s.appraisal,
                "cells": {
                    k: {
                        "item_id": c.item_id,
                        "status": c.status,
                        "value": c.value,
                        "excerpt": c.excerpt,
                        "page_no": c.page_no,
                        "review_status": c.review_status,
                        "reviewed": sig.reviewed(c),
                    }
                    for k, c in s.cells.items()
                },
            }
            for s in studies
        ],
        "method_differences": sig.method_differences(studies, _design_key(fields)),
        "limitations": sig.limitations(studies),
        "unreviewed_values": unreviewed,
        "meta_analysis": sig.META_ANALYSIS,
        "group_options": [{"value": f["field_key"], "label": f.get("label") or f["field_key"]} for f in fields]
        + [{"value": f"question:{q}", "label": qlabels.get(q, f"Question {q[:8]}")} for q in question_ids],
    }


# ---------- statements ----------


def _enrich(raw: dict[str, Any], studies: list[sig.Study], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve citations to the current revision of REVIEWED evidence, attach approved appraisals, compute certainty."""
    cells = {c.item_id: (s, c) for s in studies for c in s.cells.values()}
    text_fields = frozenset(f["field_key"] for f in fields if (f.get("type") or "text") == "text")
    warnings: list[dict[str, str]] = []
    cited: list[tuple[sig.Study, sig.Cell]] = []
    for iid in dict.fromkeys(str(i) for i in raw.get("item_ids") or []):
        pair = cells.get(iid)
        if pair is None or not sig.reviewed(pair[1]):
            warnings.append(
                {
                    "code": "citation_not_reviewed",
                    "message": "A cited value is not part of the reviewed evidence and was removed.",
                }
            )
            continue
        cited.append(pair)
    kind = raw.get("kind") if raw.get("kind") in sig.KINDS else "finding"
    text = str(raw.get("text") or "").strip()[: ai.MAX_TEXT]
    computed, reasons = sig.compute_certainty(text, cited, str(kind), text_fields)
    claimed = raw.get("claimed_certainty") if raw.get("claimed_certainty") in sig.CERTAINTY else None
    final = sig.cap(claimed, computed)
    if claimed and final != claimed:
        warnings.append(
            {
                "code": "certainty_capped",
                "message": f"Certainty lowered from '{claimed}' to '{final}' by the evidence rules.",
            }
        )
    appraisals = {}
    for s, _ in cited:
        if s.appraisal:
            appraisals[s.paper_id] = {"paper_id": s.paper_id, **s.appraisal}
    return {
        "id": raw.get("id") or str(uuid.uuid4()),
        "kind": kind,
        "text": text,
        "group": raw.get("group"),
        "citations": [
            {"evidence_item_id": c.item_id, "revision": c.revision, "paper_id": s.paper_id, "field_key": c.field_key}
            for s, c in cited
        ],
        "appraisals": list(appraisals.values()),
        "claimed_certainty": claimed,
        "certainty": final,
        "certainty_reasons": reasons,
        "origin": raw.get("origin") or "manual",
        "review": raw.get("review") or "unreviewed",
        "reviewed_by": raw.get("reviewed_by"),
        "reviewed_at": raw.get("reviewed_at"),
        "warnings": warnings,
    }


def _stale_map(db: Session, statements: list[dict[str, Any]]) -> dict[str, list[str]]:
    """statement id -> reasons it no longer matches the current evidence/appraisals."""
    from app.db.models import AppraisalRecord

    iids = {uuid.UUID(c["evidence_item_id"]) for s in statements for c in s.get("citations", [])}
    rids = {uuid.UUID(a["record_id"]) for s in statements for a in s.get("appraisals", [])}
    items = {str(i.id): i for i in db.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(iids)))} if iids else {}
    recs = (
        {str(r.id): r for r in db.scalars(select(AppraisalRecord).where(AppraisalRecord.id.in_(rids)))} if rids else {}
    )
    out: dict[str, list[str]] = {}
    for s in statements:
        reasons = []
        for c in s.get("citations", []):
            it = items.get(c["evidence_item_id"])
            if it is None or it.row_version != c["revision"] or it.review_status not in sig.REVIEWED:
                reasons.append(f"Evidence for {c['field_key']} changed since it was cited.")
        for a in s.get("appraisals", []):
            r = recs.get(a["record_id"])
            if r is None or r.row_version != a["revision"] or r.status != "approved":
                reasons.append("A cited study's appraisal changed since it was cited.")
        if reasons:
            out[s["id"]] = reasons
    return out


def version_view(db: Session, v: SynthesisVersion) -> dict[str, Any]:
    stale = _stale_map(db, list(v.statements or []))
    statements = [{**s, "stale": s["id"] in stale, "stale_reasons": stale.get(s["id"], [])} for s in v.statements or []]
    counts: dict[str, int] = {}
    for s in statements:
        counts[s["review"]] = counts.get(s["review"], 0) + 1
    return {
        "id": v.id,
        "version": v.version,
        "status": v.status,
        "extraction_schema_id": v.extraction_schema_id,
        "scope": v.scope,
        "statements": statements,
        "review_counts": counts,
        "meta_analysis": v.meta_analysis,
        "created_by": v.created_by,
        "accepted_by": v.accepted_by,
        "accepted_at": v.accepted_at,
        "accept_note": v.accept_note,
        "supersedes_id": v.supersedes_id,
        "evidence_output_id": v.evidence_output_id,
        "model_run_id": v.model_run_id,
        "row_version": v.row_version,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }


def list_versions(db: Session, project: Project) -> list[SynthesisVersion]:
    return list(
        db.scalars(
            select(SynthesisVersion)
            .where(SynthesisVersion.project_id == project.id)
            .order_by(SynthesisVersion.version.desc())
        )
    )


def get_version(db: Session, project: Project, synthesis_id: uuid.UUID, *, lock: bool = False) -> SynthesisVersion:
    q = select(SynthesisVersion).where(SynthesisVersion.id == synthesis_id, SynthesisVersion.project_id == project.id)
    v = db.scalar(q.with_for_update() if lock else q)
    if v is None:
        raise NotFound("Synthesis version not found.")
    return v


def _draft(db: Session, project: Project) -> SynthesisVersion | None:
    return db.scalar(
        select(SynthesisVersion).where(SynthesisVersion.project_id == project.id, SynthesisVersion.status == "draft")
    )


def _require_approved_schema(db: Session, project: Project, schema_id: uuid.UUID | None) -> ExtractionSchema:
    schema = _schema(db, project, schema_id)
    if schema is None or schema.status != "approved":
        raise Conflict("Approve an extraction schema before synthesising.", code="schema_not_approved")
    return schema


def _new_version(
    db: Session,
    project: Project,
    schema: ExtractionSchema,
    *,
    statements: list[dict[str, Any]],
    scope: dict[str, Any],
    created_by: uuid.UUID | None,
    supersedes_id: uuid.UUID | None = None,
    model_run_id: uuid.UUID | None = None,
) -> SynthesisVersion:
    if _draft(db, project) is not None:
        raise Conflict("A draft synthesis already exists. Accept or continue it first.", code="draft_exists")
    last = db.scalar(
        select(SynthesisVersion.version)
        .where(SynthesisVersion.project_id == project.id)
        .order_by(SynthesisVersion.version.desc())
        .limit(1)
    )
    v = SynthesisVersion(
        id=uuid.uuid4(),
        project_id=project.id,
        extraction_schema_id=schema.id,
        version=(last or 0) + 1,
        status="draft",
        scope=scope,
        statements=statements,
        meta_analysis=dict(sig.META_ANALYSIS),
        created_by=created_by,
        supersedes_id=supersedes_id,
        model_run_id=model_run_id,
        row_version=1,
    )
    db.add(v)
    db.flush()
    audit_service.record(
        db,
        action="synthesis.created",
        entity_type="synthesis_version",
        entity_id=v.id,
        actor_id=created_by,
        project_id=project.id,
        after={"version": v.version, "statements": len(statements)},
    )
    return v


def create_empty(db: Session, project: Project, user: User, body: dict[str, Any]) -> SynthesisVersion:
    protocol_service.require_writable(project)
    sid = body.get("schema_id")
    schema = _require_approved_schema(db, project, uuid.UUID(str(sid)) if sid else None)
    v = _new_version(db, project, schema, statements=[], scope={"group_by": body.get("group_by")}, created_by=user.id)
    db.commit()
    db.refresh(v)
    return v


def review(
    db: Session, project: Project, user: User, synthesis_id: uuid.UUID, body: dict[str, Any]
) -> SynthesisVersion:
    v = get_version(db, project, synthesis_id, lock=True)
    protocol_service.require_writable(project)
    if v.status != "draft":
        raise Conflict("Accepted syntheses are immutable; revise to create a new draft.", code="not_draft")
    exp = body.get("expected_row_version")
    if exp is not None and exp != v.row_version:
        raise Conflict("This synthesis was changed elsewhere. Reload and try again.", code="version_conflict")
    action = str(body.get("action") or "")
    if action not in REVIEW_ACTIONS:
        raise ValidationFailed(f"action must be one of {', '.join(REVIEW_ACTIONS)}.", details=[{"field": "action"}])
    schema = db.get(ExtractionSchema, v.extraction_schema_id)
    if schema is None:
        raise NotFound("Extraction schema not found.")
    fields = list(schema.field_definitions or [])
    statements = [dict(s) for s in v.statements or []]
    stamp = {"reviewed_by": str(user.id), "reviewed_at": _now().isoformat()}
    if action == "add" or action == "refresh":
        studies = load_studies(db, project, schema)
    if action == "add":
        if not str(body.get("text") or "").strip():
            raise ValidationFailed("A statement needs text.", details=[{"field": "text"}])
        s = _enrich(
            {
                "kind": body.get("kind"),
                "text": body.get("text"),
                "item_ids": body.get("citations") or [],
                "claimed_certainty": body.get("claimed_certainty"),
                "origin": "manual",
                "review": "accepted",
                **stamp,
            },
            studies,
            fields,
        )
        if not s["citations"]:
            raise ValidationFailed("Cite at least one reviewed evidence value.", details=[{"field": "citations"}])
        statements.append(s)
    elif action == "refresh":
        target = body.get("statement_id")
        for i, s in enumerate(statements):
            if target in (None, s["id"]):
                new = _enrich({**s, "item_ids": [c["evidence_item_id"] for c in s["citations"]]}, studies, fields)
                changed = new["citations"] != s["citations"] or new["certainty"] != s["certainty"]
                statements[i] = {**new, "review": "unreviewed" if changed else s["review"]}
    else:
        idx = next((i for i, s in enumerate(statements) if s["id"] == body.get("statement_id")), None)
        if idx is None:
            raise NotFound("Statement not found.")
        s = statements[idx]
        if action == "accept":
            statements[idx] = {**s, "review": "accepted", **stamp}
        elif action == "reject":
            statements[idx] = {**s, "review": "rejected", **stamp}
        else:  # edit
            studies = load_studies(db, project, schema)
            new = _enrich(
                {
                    **s,
                    "kind": body.get("kind", s["kind"]),
                    "text": body.get("text", s["text"]),
                    "item_ids": body.get("citations", [c["evidence_item_id"] for c in s["citations"]]),
                    "claimed_certainty": body.get("claimed_certainty", s.get("claimed_certainty")),
                    "review": "edited",
                    **stamp,
                },
                studies,
                fields,
            )
            if not new["text"] or not new["citations"]:
                raise ValidationFailed("An edited statement needs text and at least one reviewed citation.")
            statements[idx] = new
    v.statements = statements
    v.row_version += 1
    v.updated_at = _now()
    audit_service.record(
        db,
        action=f"synthesis.statement_{action}",
        entity_type="synthesis_version",
        entity_id=v.id,
        actor_id=user.id,
        project_id=project.id,
        after={"statement_id": body.get("statement_id"), "row_version": v.row_version},
    )
    db.commit()
    db.refresh(v)
    return v


def accept(
    db: Session, project: Project, user: User, synthesis_id: uuid.UUID, body: dict[str, Any]
) -> SynthesisVersion:
    v = get_version(db, project, synthesis_id, lock=True)
    protocol_service.require_writable(project)
    if v.status != "draft":
        raise Conflict("Only a draft synthesis can be accepted.", code="not_draft")
    exp = body.get("expected_row_version")
    if exp is not None and exp != v.row_version:
        raise Conflict("This synthesis was changed elsewhere. Reload and try again.", code="version_conflict")
    statements = list(v.statements or [])
    if any(s["review"] == "unreviewed" for s in statements):
        raise Conflict(
            "Review every statement (accept, edit or reject) before accepting.", code="unreviewed_statements"
        )
    kept = [s for s in statements if s["review"] in ("accepted", "edited")]
    if not kept:
        raise Conflict("Accept at least one statement.", code="no_accepted_statements")
    stale = _stale_map(db, kept)
    if stale:
        raise Conflict(
            "Some accepted statements cite evidence or appraisals that changed since; refresh and review them.",
            code="stale_citations",
            details=[{"statement_id": k} for k in stale],
        )
    for old in db.scalars(
        select(SynthesisVersion).where(SynthesisVersion.project_id == project.id, SynthesisVersion.status == "accepted")
    ):
        old.status = "superseded"
        old.updated_at = _now()
    used = {(c["evidence_item_id"], c["revision"]) for s in kept for c in s["citations"]}
    out = EvidenceOutput(
        id=uuid.uuid4(),
        project_id=project.id,
        extraction_schema_id=v.extraction_schema_id,
        kind="synthesis",
        created_by=user.id,
        item_count=len(used),
    )
    db.add(out)
    db.flush()
    for iid, rev in sorted(used):
        db.add(EvidenceOutputItem(evidence_output_id=out.id, evidence_item_id=uuid.UUID(iid), revision=rev))
    v.status, v.accepted_by, v.accepted_at = "accepted", user.id, _now()
    v.accept_note = str(body.get("note") or "").strip() or None
    v.evidence_output_id = out.id
    v.row_version += 1
    v.updated_at = _now()
    audit_service.record(
        db,
        action="synthesis.accepted",
        entity_type="synthesis_version",
        entity_id=v.id,
        actor_id=user.id,
        project_id=project.id,
        after={"version": v.version, "statements": len(kept), "evidence_items": len(used)},
    )
    db.commit()
    db.refresh(v)
    return v


def revise(db: Session, project: Project, user: User, synthesis_id: uuid.UUID) -> SynthesisVersion:
    """New draft from a version: rejected statements dropped; citations re-resolved; changed statements unreviewed."""
    src = get_version(db, project, synthesis_id)
    protocol_service.require_writable(project)
    schema = db.get(ExtractionSchema, src.extraction_schema_id)
    if schema is None or schema.status != "approved":
        raise Conflict("The synthesis' extraction schema is no longer available.", code="schema_not_approved")
    studies = load_studies(db, project, schema)
    fields = list(schema.field_definitions or [])
    statements = []
    for s in src.statements or []:
        if s["review"] == "rejected":
            continue
        new = _enrich({**s, "item_ids": [c["evidence_item_id"] for c in s["citations"]]}, studies, fields)
        changed = new["citations"] != s["citations"] or new["certainty"] != s["certainty"]
        statements.append({**new, "review": "unreviewed" if changed else s["review"]})
    v = _new_version(
        db,
        project,
        schema,
        statements=statements,
        scope=dict(src.scope or {}),
        created_by=user.id,
        supersedes_id=src.id,
    )
    db.commit()
    db.refresh(v)
    return v


# ---------- draft job ----------


def _jobs(db: Session, user: User, project: Project, active_only: bool) -> list[Job]:
    return [
        j
        for j in job_service.list_(db, user, project_id=project.id, active_only=active_only, limit=50)
        if j.type == JOB_TYPE
    ]


def draft_status(db: Session, settings: Settings, user: User, project: Project) -> dict[str, Any]:
    recent = _jobs(db, user, project, active_only=False)
    return {
        "llm": describe(settings),
        "consent": consent_service.status(db, settings, project),
        "prompt_version": ai.PROMPT_VERSION,
        "active_job_id": next((j.id for j in recent if j.status in fsm.ACTIVE_STATUSES), None),
        "last_result": next((j.result for j in recent if j.status in fsm.TERMINAL_STATUSES and j.result), None),
        "last_error": next((j.error_summary for j in recent if j.status == fsm.FAILED), None),
        "meta_analysis": sig.META_ANALYSIS,
    }


def start_draft(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    body: dict[str, Any],
) -> Job:
    protocol_service.require_writable(project)
    sid = body.get("schema_id")
    schema = _require_approved_schema(db, project, uuid.UUID(str(sid)) if sid else None)
    group_by = body.get("group_by") or None
    keys = {f["field_key"] for f in schema.field_definitions or []}
    if group_by and not str(group_by).startswith("question:") and group_by not in keys:
        raise ValidationFailed(
            "group_by must be a field of the schema or question:<id>.", details=[{"field": "group_by"}]
        )
    if not schema_service.included_paper_ids(db, project):
        raise Conflict("No papers are included yet.", code="no_included_papers")
    if _draft(db, project) is not None:
        raise Conflict("A draft synthesis already exists. Accept or continue it first.", code="draft_exists")
    if _jobs(db, user, project, active_only=True):
        raise Conflict("A synthesis draft is already being generated.", code="job_already_active")
    allowed, retry = limiter.hit(f"synthesis:{user.id}", settings.rate_limit_extraction_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many synthesis drafts started in the last hour. Try again later.")
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=project.id,
        payload={
            "schema_id": str(schema.id),
            "group_by": group_by,
            "use_ai": bool(body.get("use_ai")),
            "nonce": str(uuid.uuid4()),
        },
        request_key=None,
    )
    return job


def _signal_lines(groups: list[dict[str, Any]], studies: list[sig.Study], fields: list[dict[str, Any]]) -> list[str]:
    by_id = {s.paper_id: s for s in studies}
    fdefs = {f["field_key"]: f for f in fields}
    lines = []
    for g in groups:
        members = [by_id[p] for p in g["paper_ids"] if p in by_id]
        for k in g["field_keys"]:
            s = sig.field_signal(fdefs[k], members)
            gaps = ", ".join(f"{n} {len(v)}" for n, v in s["gaps"].items() if v)
            lines.append(
                f"[{g['label']}] {s['label']}: {s['pattern']} ({s['studies_with_value']} with value"
                f"{'; gaps: ' + gaps if gaps else ''})"
            )
    return lines


def execute_draft(db: Session, job: Job, settings: Settings, *, gateway: LLMGateway | None = None) -> str:
    payload = job.input or {}
    project = db.get(Project, job.project_id) if job.project_id else None
    schema = db.get(ExtractionSchema, uuid.UUID(str(payload["schema_id"]))) if payload.get("schema_id") else None
    if project is None or schema is None or schema.project_id != project.id or schema.status != "approved":
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="The extraction schema is no longer available.")
        db.commit()
        return "failed"
    fields = list(schema.field_definitions or [])
    group_by = payload.get("group_by")
    studies = load_studies(db, project, schema)
    groups = sig.group_studies(studies, fields, group_by, schema.question_map or {})
    raw = sig.system_statements(fields, groups, studies, _design_key(fields))
    result: dict[str, Any] = {"system_statements": len(raw), "ai_statements": 0, "stop_reason": None}
    model_run_id: uuid.UUID | None = None
    if payload.get("use_ai"):
        gw = gateway or build_gateway(settings)
        consent, block = llm_jobs.gate(db, project, gw)
        if block is not None:
            result["stop_reason"] = "not_configured" if gw.provider is None else "refused"
            result["blocked_reason"] = block
        else:
            keys, lines = ai.evidence_keys(studies, fields)
            prompt = ai.build_prompt(lines, _signal_lines(groups, studies, fields))
            if len(prompt) > MAX_PROMPT_CHARS:
                prompt = prompt[:MAX_PROMPT_CHARS] + "\n[Evidence list truncated: cite only keys shown above.]"
                result["prompt_truncated"] = True
            job_repo.set_progress(db, job.id, 40, "Drafting statements")
            db.commit()
            try:
                res = gw.generate_json(
                    data_class="private_document",
                    system=ai.SYSTEM,
                    prompt=prompt,
                    schema=ai.SCHEMA,
                    temperature=0.0,
                    max_output_tokens=8192,
                    consent=consent,
                )
                ai_raw, problems = ai.validate(res.data or {}, keys)
                mr = llm_jobs.model_run(
                    db,
                    job,
                    project.id,
                    gw,
                    purpose=PURPOSE,
                    prompt_version=ai.PROMPT_VERSION,
                    n=len(keys),
                    model=res.model,
                    status="succeeded",
                    input_tokens=res.input_tokens,
                    output_tokens=res.output_tokens,
                    duration_ms=res.duration_ms,
                    attempts=res.attempts,
                    error_summary=("; ".join(dict.fromkeys(problems))[:500] or None) if problems else None,
                )
                model_run_id = mr.id
                raw += ai_raw
                result["ai_statements"] = len(ai_raw)
                result["ai_dropped"] = len(problems)
            except LLMError as e:
                llm_jobs.model_run(
                    db,
                    job,
                    project.id,
                    gw,
                    purpose=PURPOSE,
                    prompt_version=ai.PROMPT_VERSION,
                    n=len(keys),
                    status="failed",
                    error_kind=e.kind,
                    error_summary=e.message[:500],
                )
                result["stop_reason"] = e.kind
    statements = [_enrich(r, studies, fields) for r in raw]
    statements = [s for s in statements if s["citations"]]
    try:
        v = _new_version(
            db,
            project,
            schema,
            statements=statements,
            scope={"group_by": group_by},
            created_by=job.owner_id,
            model_run_id=model_run_id,
        )
    except Conflict:
        db.rollback()
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="A draft synthesis was created meanwhile.")
        db.commit()
        return "failed"
    result["synthesis_id"] = str(v.id)
    result["statements"] = len(statements)
    job_repo.finish(db, job.id, fsm.RUNNING, fsm.SUCCEEDED, result=result)
    db.commit()
    return "ok"
