"""Evidence extraction job: API side (status, start) and worker side (execute).

Worker design (same reliability rules as the relevance job):
  * Papers: the included papers (or the subset requested), at most EXTRACTION_MAX_PAPERS_PER_JOB per job.
  * A paper with no linked, parsed full text gets `unavailable` for every field (origin=system, no AI call).
  * Otherwise the document's current page text is split into page-aligned chunks; each chunk is one AI request
    (data class `private_document`, so gates G1-G3 must pass) and one `model_runs` row, also on failure.
  * Answers are validated deterministically (app.extraction.extractor) and written per paper in one commit, each
    with a revision. Values a human approved or edited are never overwritten; a rerun (force) only replaces AI or
    system values that are still unreviewed.
  * Cancellation between papers/chunks; a time budget stops cleanly; rate-limit / refused / rejected stops the AI part.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, RateLimited, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import (
    DOCUMENT_LINKED_MATCH,
    DocumentPage,
    EvidenceItem,
    ExtractionSchema,
    Job,
    ModelRun,
    PaperDocument,
    PaperRecord,
    Project,
    User,
)
from app.extraction import extractor as ex
from app.extraction import schema_service
from app.jobs import fsm
from app.providers.llm import ConsentToken, LLMError, LLMGateway, build_gateway, describe
from app.repositories import jobs as job_repo
from app.services import audit_service, consent_service, job_service, protocol_service
from app.services.job_service import Dispatcher

JOB_TYPE = "extraction.run"
PURPOSE = "evidence_extraction"
STOP_KINDS = frozenset({"rate_limited", "rejected", "not_configured", "refused"})
MAX_CONSECUTIVE_FAILURES = 3
log = logging.getLogger(__name__)

STOP_MESSAGES = {
    "rate_limited": "The AI provider's rate limit or quota was reached. Start extraction again later to continue.",
    "rejected": "The AI provider rejected the request (check LLM_API_KEY and LLM_MODEL).",
    "refused": "Document text may not be sent to the AI provider (see the consent panel). Papers without full text "
    "were still marked.",
    "not_configured": "No AI model is configured. Papers without full text were still marked; extract the rest "
    "manually.",
    "repeated_failures": "Several AI requests failed in a row; stopped. Completed papers are kept.",
    "time_budget": "Reached this job's time budget. Start extraction again to continue where it stopped.",
    "cancelled": "Cancelled. Papers completed so far are kept.",
    "paper_limit": "Reached the per-job paper limit. Start extraction again to continue with the remaining papers.",
}


# ---------- API side ----------


def _jobs(db: Session, user: User, project: Project, schema_id: uuid.UUID, active_only: bool) -> list[Job]:
    return [
        j
        for j in job_service.list_(db, user, project_id=project.id, active_only=active_only, limit=50)
        if j.type == JOB_TYPE and (j.input or {}).get("schema_id") == str(schema_id)
    ]


def status(db: Session, settings: Settings, user: User, project: Project, schema: ExtractionSchema) -> dict[str, Any]:
    recent = _jobs(db, user, project, schema.id, active_only=False)
    runs = list(
        db.scalars(
            select(ModelRun)
            .where(ModelRun.project_id == project.id, ModelRun.purpose == PURPOSE)
            .order_by(ModelRun.created_at.desc())
            .limit(10)
        )
    )
    counts = {
        f"{o}:{r}:{s}": n
        for o, r, s, n in db.execute(
            select(EvidenceItem.origin, EvidenceItem.review_status, EvidenceItem.status, func.count())
            .where(EvidenceItem.extraction_schema_id == schema.id)
            .group_by(EvidenceItem.origin, EvidenceItem.review_status, EvidenceItem.status)
        ).all()
    }
    return {
        "schema_id": schema.id,
        "schema_status": schema.status,
        "prompt_version": ex.PROMPT_VERSION,
        "llm": describe(settings),
        "consent": consent_service.status(db, settings, project),
        "included_papers": len(schema_service.included_paper_ids(db, project)),
        "active_job_id": next((j.id for j in recent if j.status in fsm.ACTIVE_STATUSES), None),
        "last_result": next((j.result for j in recent if j.status in fsm.TERMINAL_STATUSES and j.result), None),
        "last_error": next((j.error_summary for j in recent if j.status == fsm.FAILED), None),
        "item_counts": counts,
        "model_runs": [
            {
                "id": r.id,
                "provider": r.provider,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "data_class": r.data_class,
                "status": r.status,
                "error_kind": r.error_kind,
                "error_summary": r.error_summary,
                "item_count": r.item_count,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at,
            }
            for r in runs
        ],
    }


def start(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    schema_id: uuid.UUID,
    *,
    paper_ids: list[str] | None,
    force: bool,
    request_key: str | None = None,
) -> Job:
    schema = schema_service.get_schema(db, project, schema_id)
    protocol_service.require_writable(project)
    if schema.status != "approved":
        raise Conflict("Approve the extraction schema before extracting evidence.", code="schema_not_approved")
    included = {str(p) for p in schema_service.included_paper_ids(db, project)}
    if not included:
        raise Conflict("No papers are included yet; screen papers before extracting.", code="no_included_papers")
    chosen: list[str] = []
    if paper_ids:
        for p in paper_ids:
            if str(p) not in included:
                raise ValidationFailed("Only included papers can be extracted.", details=[{"paper_id": str(p)}])
            chosen.append(str(p))
    if _jobs(db, user, project, schema.id, active_only=True):
        raise Conflict("An extraction is already running for this schema.", code="job_already_active")
    allowed, retry = limiter.hit(f"extraction:{user.id}", settings.rate_limit_extraction_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many extractions started in the last hour. Try again later.")
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=project.id,
        payload={
            "schema_id": str(schema.id),
            "paper_ids": chosen,
            "force": force,
            "nonce": request_key or str(uuid.uuid4()),
        },
        request_key=request_key,
    )
    return job


# ---------- worker side ----------


def full_text(db: Session, paper_id: uuid.UUID) -> PaperDocument | None:
    """Newest parsed document that is linked to the paper (unconfirmed matches never count)."""
    return db.scalar(
        select(PaperDocument)
        .where(
            PaperDocument.paper_id == paper_id,
            PaperDocument.removed_at.is_(None),
            PaperDocument.parse_status == "parsed",
            PaperDocument.match_status.in_(DOCUMENT_LINKED_MATCH),
            PaperDocument.text_version > 0,
        )
        .order_by(PaperDocument.processed_at.desc().nulls_last(), PaperDocument.created_at.desc())
        .limit(1)
    )


def _todo(fields: list[dict[str, Any]], items: dict[str, EvidenceItem], force: bool) -> list[dict[str, Any]]:
    out = []
    for fd in fields:
        it = items.get(fd["field_key"])
        if it is None or (it.status == "pending" and it.review_status not in schema_service.REVIEWED):
            out.append(fd)
        elif force and it.origin in ("ai", "system") and it.review_status == "unreviewed":
            out.append(fd)
    return out


def _model_run(db: Session, job: Job, project_id: uuid.UUID, gw: LLMGateway, n: int, **kw: Any) -> ModelRun:
    provider = gw.provider
    mr = ModelRun(
        project_id=project_id,
        job_id=job.id,
        purpose=PURPOSE,
        provider=provider.name if provider else "none",
        model=(kw.pop("model", None) or (provider.model if provider else "none"))[:120],
        prompt_version=ex.PROMPT_VERSION,
        data_class="private_document",
        settings={"temperature": 0.0},
        item_count=n,
        **kw,
    )
    db.add(mr)
    db.flush()
    return mr


def _write(
    db: Session,
    project: Project,
    schema: ExtractionSchema,
    paper_id: uuid.UUID,
    key: str,
    values: dict[str, Any],
    action: str,
    model_run_id: uuid.UUID | None,
) -> None:
    schema_service.write_item(
        db,
        project,
        paper_id=paper_id,
        schema_id=schema.id,
        field_key=key,
        values={"reviewer_id": None, "review_status": "unreviewed", **values},
        action=action,
        actor_id=None,
        model_run_id=model_run_id,
    )


def _mark_unavailable(
    db: Session, project: Project, schema: ExtractionSchema, paper_id: uuid.UUID, todo: list[dict[str, Any]], note: str
) -> None:
    for fd in todo:
        _write(
            db,
            project,
            schema,
            paper_id,
            fd["field_key"],
            {
                "status": "unavailable",
                "value": None,
                "excerpt": None,
                "document_page_id": None,
                "note": note,
                "origin": "system",
                "extraction_version": ex.PROMPT_VERSION,
                "pages_inspected": [],
                "validation": [],
            },
            "system_unavailable",
            None,
        )


def _extract_paper(
    db: Session,
    job: Job,
    settings: Settings,
    gw: LLMGateway,
    consent: ConsentToken | None,
    project: Project,
    schema: ExtractionSchema,
    paper: PaperRecord,
    doc: PaperDocument,
    todo: list[dict[str, Any]],
    deadline: float,
    failures: list[int],
) -> tuple[dict[str, int], str | None]:
    """Extract one paper. Valid answers from completed requests are written even if the job must stop part-way
    (`not_located` still needs an answer from every chunk). Returns (counts, stop_reason)."""
    rows = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == doc.id, DocumentPage.text_version == doc.text_version)
        .order_by(DocumentPage.page_no)
    )
    pages = [ex.PageText(str(p.id), p.page_no, p.text or "") for p in rows]
    chunks = ex.chunk_pages(pages, max(2000, settings.extraction_chunk_chars))
    if not chunks:
        _mark_unavailable(
            db, project, schema, paper.id, todo, "The uploaded document has no readable text (scanned without OCR?)."
        )
        return {"unavailable": len(todo)}, None
    per_chunk: list[dict[str, ex.Outcome]] = []
    run_ids: dict[int, uuid.UUID] = {}
    inspected: list[int] = []
    stop: str | None = None
    for i, chunk in enumerate(chunks):
        db.refresh(job)
        if job.cancel_requested:
            stop = "cancelled"
            break
        if time.monotonic() > deadline:
            stop = "time_budget"
            break
        try:
            res = gw.generate_json(
                data_class="private_document",
                system=ex.SYSTEM,
                prompt=ex.build_prompt(todo, paper.title, chunk, i + 1, len(chunks)),
                schema=ex.SCHEMA,
                temperature=0.0,
                max_output_tokens=min(8192, 300 * len(todo) + 400),
                consent=consent,
            )
        except LLMError as e:
            _model_run(
                db, job, project.id, gw, len(todo), status="failed", error_kind=e.kind, error_summary=e.message[:500]
            )
            failures[0] += 1
            log.warning("llm extraction request failed", extra={"kind": e.kind})
            if e.kind in STOP_KINDS:
                stop = e.kind
                break
            if failures[0] >= MAX_CONSECUTIVE_FAILURES:
                stop = "repeated_failures"
                break
            continue
        failures[0] = 0
        outcomes, problems = ex.validate_answer(todo, chunk, res.data or {}, i)
        mr = _model_run(
            db,
            job,
            project.id,
            gw,
            len(todo),
            model=res.model,
            status="succeeded",
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            duration_ms=res.duration_ms,
            attempts=res.attempts,
            error_summary=("; ".join(problems)[:500] or None) if problems else None,
        )
        run_ids[i] = mr.id
        inspected += [p.page_no for p in chunk]
        per_chunk.append(outcomes)
    counts = {"found": 0, "not_located": 0, "ambiguous": 0, "discarded": 0, "no_answer": 0}
    merged = ex.merge(per_chunk, [fd["field_key"] for fd in todo], len(chunks))
    for fd in todo:
        key = fd["field_key"]
        o = merged.get(key)
        if o is None:
            counts["no_answer"] += 1
            continue
        common = {
            "origin": "ai",
            "extraction_version": ex.PROMPT_VERSION,
            "pages_inspected": sorted(set(inspected)),
            "validation": o.warnings,
        }
        if o.status == "found":
            vals = {"status": "found", "value": o.value, "excerpt": o.excerpt, "note": o.note}
        elif o.status == "ambiguous":
            vals = {"status": "ambiguous", "value": None, "excerpt": o.excerpt, "note": o.note}
        elif o.status == "not_located":
            vals = {
                "status": "not_located",
                "value": None,
                "excerpt": None,
                "note": o.note or "Not stated in the processed text of the pages inspected.",
            }
        else:
            vals = {
                "status": "pending",
                "value": None,
                "excerpt": None,
                "note": "The AI proposal failed validation and was discarded; extract this field manually.",
            }
        page_id = uuid.UUID(o.page_id) if o.page_id and o.status in ("found", "ambiguous") else None
        _write(
            db,
            project,
            schema,
            paper.id,
            key,
            {**common, **vals, "document_page_id": page_id},
            "ai_extracted",
            run_ids.get(o.chunk_index),
        )
        counts["discarded" if o.status == "rejected" else o.status] += 1
    return counts, stop


def execute(db: Session, job: Job, settings: Settings, *, gateway: LLMGateway | None = None) -> str:
    """Run one extraction job (claimed by the caller). Finishes the job."""
    payload = job.input or {}
    project = db.get(Project, job.project_id) if job.project_id else None
    schema = db.get(ExtractionSchema, uuid.UUID(str(payload.get("schema_id")))) if payload.get("schema_id") else None
    if project is None or schema is None or schema.project_id != project.id or schema.status != "approved":
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="The extraction schema is no longer available.")
        db.commit()
        return "failed"
    deadline = time.monotonic() + settings.extraction_job_budget_s
    fields = list(schema.field_definitions or [])
    included = schema_service.included_paper_ids(db, project)
    wanted = [uuid.UUID(p) for p in payload.get("paper_ids") or []] or included
    included_set = set(included)
    wanted = [p for p in wanted if p in included_set]
    force = bool(payload.get("force"))
    gw = gateway or build_gateway(settings)
    if gw.budget_guard is None:
        from app.services.llm_jobs import token_guard

        gw.budget_guard = token_guard(db, project.id, settings.llm_daily_token_budget)
    consent = consent_service.token_for(db, project, gw.provider.name, gw.provider.model) if gw.provider else None
    block = gw.private_document_block(consent)
    totals: dict[str, int] = {
        "papers_processed": 0,
        "papers_skipped": 0,
        "papers_blocked": 0,
        "unavailable": 0,
        "found": 0,
        "not_located": 0,
        "ambiguous": 0,
        "discarded": 0,
        "no_answer": 0,
    }
    stop: str | None = None
    failures = [0]
    budget = max(1, settings.extraction_max_papers_per_job)
    done = 0
    for n, pid in enumerate(wanted, start=1):
        if done >= budget:
            stop = stop or "paper_limit"
            break
        db.refresh(job)
        if job.cancel_requested:
            stop = "cancelled"
            break
        if time.monotonic() > deadline:
            stop = "time_budget"
            break
        paper = db.get(PaperRecord, pid)
        if paper is None:
            continue
        items = {
            it.field_key: it
            for it in db.scalars(
                select(EvidenceItem).where(EvidenceItem.extraction_schema_id == schema.id, EvidenceItem.paper_id == pid)
            )
        }
        todo = _todo(fields, items, force)
        if not todo:
            totals["papers_skipped"] += 1
            continue
        doc = full_text(db, pid)
        if doc is None:
            _mark_unavailable(
                db,
                project,
                schema,
                pid,
                todo,
                "No full text is linked to this paper. Upload a copy you are authorised to use, then rerun.",
            )
            totals["unavailable"] += len(todo)
            totals["papers_processed"] += 1
        elif block is not None:
            totals["papers_blocked"] += 1
            stop = stop or ("not_configured" if gw.provider is None else "refused")
            continue
        else:
            done += 1
            counts, paper_stop = _extract_paper(
                db, job, settings, gw, consent, project, schema, paper, doc, todo, deadline, failures
            )
            stop = paper_stop or stop
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
            totals["papers_processed"] += 1
        job_repo.set_progress(db, job.id, min(95, int(95 * n / max(len(wanted), 1))), f"Extracted {n} of {len(wanted)}")
        db.commit()
        if stop is not None and block is None:
            break
    result: dict[str, Any] = {**totals, "papers_requested": len(wanted), "stop_reason": stop}
    result["message"] = STOP_MESSAGES.get(str(stop)) if stop else None
    if block is not None:
        result["blocked_reason"] = block
    audit_service.record(
        db,
        action="evidence.extraction_run",
        entity_type="job",
        entity_id=job.id,
        actor_id=None,
        project_id=project.id,
        after={k: v for k, v in result.items() if k not in ("message", "blocked_reason")},
    )
    target = fsm.CANCELLED if stop == "cancelled" else fsm.SUCCEEDED
    job_repo.finish(db, job.id, fsm.RUNNING, target, result=result)
    db.commit()
    return "cancelled" if stop == "cancelled" else "ok"
