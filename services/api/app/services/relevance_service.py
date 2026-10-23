"""Relevance assessment: API side (status, start) and worker side (execute). Relevance is never eligibility.

Worker design (same reliability rules as the search job):
  1. Lexical scoring of every paper still without a `lexical-v1` assessment for the approved protocol version
     (deterministic, no network), committed every LEXICAL_BATCH papers.
  2. Only if the user asked for it AND a model is configured: AI assessment, best lexical scores first, in batches of
     LLM_BATCH_SIZE papers per request, at most LLM_MAX_PAPERS_PER_JOB per job. Each request is one `model_runs` row
     (also on failure) and its validated results commit together. A rate-limit/quota error or a rejected key/model
     stops the AI part cleanly (lexical results are kept); the job can be started again later to continue.
  3. Cancellation between batches; a time budget stops cleanly before the worker's hard limit.
Only public metadata is sent (data class `public_metadata`), through the LLM gateway.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, RateLimited
from app.core.rate_limit import RateLimiter
from app.db.models import Job, ModelRun, PaperRecord, Project, User
from app.jobs import fsm
from app.providers.llm import LLMError, LLMGateway, build_gateway, describe
from app.repositories import jobs as job_repo
from app.repositories import screening as repo
from app.screening import ai_assessment as ai
from app.screening import relevance as lex
from app.services import audit_service, job_service, protocol_service
from app.services.job_service import Dispatcher

JOB_TYPE = "relevance.assess"
LEXICAL_BATCH = 200
STOP_KINDS = frozenset({"rate_limited", "rejected", "not_configured", "refused"})
MAX_CONSECUTIVE_FAILURES = 3
log = logging.getLogger(__name__)


# ---------- API side ----------


def _active_job(db: Session, user: User, project: Project) -> Job | None:
    for j in job_service.list_(db, user, project_id=project.id, active_only=True, limit=50):
        if j.type == JOB_TYPE:
            return j
    return None


def status(db: Session, settings: Settings, user: User, project: Project) -> dict[str, Any]:
    pv = protocol_service.approved(db, project)
    total = db.scalar(select(func.count()).where(PaperRecord.project_id == project.id)) or 0
    recent = [
        j for j in job_service.list_(db, user, project_id=project.id, active_only=False, limit=30) if j.type == JOB_TYPE
    ]
    runs = repo.recent_model_runs(db, project.id, 10)
    return {
        "llm": describe(settings),
        "total_papers": total,
        "protocol_version_id": pv.id if pv else None,
        "counts": repo.assessment_counts(db, project.id, pv.id) if pv else {"lexical": {}, "llm": {}},
        "active_job_id": next((j.id for j in recent if j.status in fsm.ACTIVE_STATUSES), None),
        "last_result": next((j.result for j in recent if j.status in fsm.TERMINAL_STATUSES and j.result), None),
        "last_error": next((j.error_summary for j in recent if j.status == fsm.FAILED), None),
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
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "duration_ms": r.duration_ms,
                "attempts": r.attempts,
                "created_at": r.created_at,
            }
            for r in runs
        ],
        "methods": {"lexical": lex.METHOD_VERSION, "llm": ai.PROMPT_VERSION},
    }


def start(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    *,
    use_llm: bool,
    force: bool,
) -> Job:
    protocol_service.require_writable(project)
    pv = protocol_service.approved(db, project)
    if pv is None:
        raise Conflict("Approve the review protocol before assessing relevance.", code="protocol_not_approved")
    if use_llm and not describe(settings)["configured"]:
        raise Conflict(
            "No AI model is configured on this server; run the word-overlap assessment only.", code="llm_not_configured"
        )
    if _active_job(db, user, project) is not None:
        raise Conflict("A relevance assessment is already running.", code="job_already_active")
    allowed, retry = limiter.hit(f"relevance:{user.id}", settings.rate_limit_relevance_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many relevance assessments started in the last hour. Try again later.")
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=project.id,
        payload={"protocol_version_id": str(pv.id), "use_llm": use_llm, "force": force, "nonce": str(uuid.uuid4())},
        request_key=None,
    )
    return job


# ---------- worker side ----------


def _paper_input(key: str, p: PaperRecord) -> ai.PaperInput:
    return ai.PaperInput(key, str(p.id), p.title, p.abstract, list(p.keywords or []), p.year, p.publication_type)


def _lexical(
    db: Session, job: Job, project_id: uuid.UUID, pv_id: uuid.UUID, content: dict[str, Any], force: bool
) -> int:
    concepts = lex.concepts_from_protocol(content)
    papers = repo.papers_needing(db, project_id, pv_id, "lexical", lex.METHOD_VERSION, force=force, limit=None)
    for i, p in enumerate(papers, start=1):
        a = lex.assess({"title": p.title, "abstract": p.abstract, "keywords": p.keywords}, concepts)
        repo.upsert_assessment(
            db,
            {
                "project_id": project_id,
                "paper_id": p.id,
                "protocol_version_id": pv_id,
                "method": "lexical",
                "method_version": lex.METHOD_VERSION,
                "score": a.score,
                "band": a.band,
                "confidence": a.confidence,
                "explanation": a.explanation,
                "eligibility_suggestion": None,
                "suggestion_detail": None,
                "warnings": [],
                "model_run_id": None,
            },
        )
        if i % LEXICAL_BATCH == 0:
            job_repo.set_progress(db, job.id, min(30, int(30 * i / max(len(papers), 1))), f"Word overlap: {i} papers")
            db.commit()
    db.commit()
    return len(papers)


def _model_run(
    db: Session, job: Job, project_id: uuid.UUID, gw: LLMGateway, settings: Settings, n: int, **kw: Any
) -> ModelRun:
    provider = gw.provider
    mr = ModelRun(
        project_id=project_id,
        job_id=job.id,
        purpose="relevance_assessment",
        provider=provider.name if provider else "none",
        model=(kw.pop("model", None) or (provider.model if provider else "none"))[:120],
        prompt_version=ai.PROMPT_VERSION,
        data_class="public_metadata",
        settings={"temperature": 0.0, "batch_size": settings.llm_batch_size},
        item_count=n,
        **kw,
    )
    db.add(mr)
    db.flush()
    return mr


def _llm(
    db: Session,
    job: Job,
    settings: Settings,
    gw: LLMGateway,
    project_id: uuid.UUID,
    pv_id: uuid.UUID,
    content: dict[str, Any],
    force: bool,
    deadline: float,
) -> dict[str, Any]:
    protocol = ai.protocol_input(content)
    todo = repo.papers_needing(
        db, project_id, pv_id, "llm", ai.PROMPT_VERSION, force=force, limit=max(1, settings.llm_max_papers_per_job)
    )
    size = max(1, min(settings.llm_batch_size, 25))
    assessed = failed = 0
    consecutive = 0
    stop: str | None = None
    for start in range(0, len(todo), size):
        db.refresh(job)
        if job.cancel_requested:
            stop = "cancelled"
            break
        if time.monotonic() > deadline:
            stop = "time_budget"
            break
        chunk = todo[start : start + size]
        inputs = [_paper_input(f"P{i + 1}", p) for i, p in enumerate(chunk)]
        try:
            res = gw.generate_json(
                data_class="public_metadata",
                system=ai.SYSTEM,
                prompt=ai.build_prompt(protocol, inputs),
                schema=ai.SCHEMA,
                temperature=0.0,
                max_output_tokens=min(8192, 700 * len(chunk) + 400),
            )
        except LLMError as e:
            _model_run(
                db,
                job,
                project_id,
                gw,
                settings,
                len(chunk),
                status="failed",
                error_kind=e.kind,
                error_summary=e.message[:500],
            )
            failed += len(chunk)
            consecutive += 1
            db.commit()
            log.warning("llm relevance batch failed", extra={"kind": e.kind})
            if e.kind in STOP_KINDS:
                stop = e.kind
                break
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                stop = "repeated_failures"
                break
            continue
        valid, missing, problems = ai.validate(res.data or {}, protocol, inputs)
        mr = _model_run(
            db,
            job,
            project_id,
            gw,
            settings,
            len(chunk),
            model=res.model,
            status="succeeded",
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            duration_ms=res.duration_ms,
            attempts=res.attempts,
            error_summary=("; ".join(problems)[:500] or None) if problems or missing else None,
            error_kind="partial_answer" if missing else None,
        )
        for v in valid:
            repo.upsert_assessment(
                db,
                {
                    "project_id": project_id,
                    "paper_id": uuid.UUID(v.paper_id),
                    "protocol_version_id": pv_id,
                    "method": "llm",
                    "method_version": ai.PROMPT_VERSION,
                    "score": v.score,
                    "band": v.band,
                    "confidence": v.confidence,
                    "explanation": v.explanation,
                    "eligibility_suggestion": v.suggestion,
                    "suggestion_detail": v.suggestion_detail,
                    "warnings": v.warnings,
                    "model_run_id": mr.id,
                },
            )
        assessed += len(valid)
        failed += len(missing)
        consecutive = 0
        pct = 30 + int(65 * (start + len(chunk)) / max(len(todo), 1))
        job_repo.set_progress(db, job.id, min(95, pct), f"AI assessment: {assessed} of {len(todo)} papers")
        db.commit()
    remaining = len(repo.papers_needing(db, project_id, pv_id, "llm", ai.PROMPT_VERSION, force=False, limit=None))
    return {"llm_assessed": assessed, "llm_not_assessed": failed, "llm_stop_reason": stop, "llm_remaining": remaining}


STOP_MESSAGES = {
    "rate_limited": "The AI provider's rate limit or daily quota was reached. Word-overlap scores are complete; start "
    "the assessment again later to continue the AI part.",
    "rejected": "The AI provider rejected the request (check LLM_API_KEY and LLM_MODEL). Word-overlap scores are "
    "complete.",
    "repeated_failures": "Several AI requests failed in a row; stopped. Word-overlap scores are complete.",
    "time_budget": "Reached this job's time budget. Start the assessment again to continue where it stopped.",
    "cancelled": "Cancelled. Assessments made so far are kept.",
}


def execute(db: Session, job: Job, settings: Settings, *, gateway: LLMGateway | None = None) -> str:
    """Run one relevance job (claimed by the caller). Finishes the job."""
    payload = job.input or {}
    project = db.get(Project, job.project_id) if job.project_id else None
    pv = protocol_service.approved(db, project) if project else None
    if project is None or pv is None or str(pv.id) != payload.get("protocol_version_id"):
        job_repo.finish(
            db,
            job.id,
            fsm.RUNNING,
            fsm.FAILED,
            error="The approved protocol changed before the assessment ran. Start it again.",
        )
        db.commit()
        return "failed"
    deadline = time.monotonic() + settings.relevance_job_budget_s
    content = protocol_service.to_content(pv)
    force = bool(payload.get("force"))
    scored = _lexical(db, job, project.id, pv.id, content, force)
    result: dict[str, Any] = {"lexical_scored": scored, "protocol_version": pv.version}
    if payload.get("use_llm"):
        gw = gateway or build_gateway(settings)
        if not gw.available:
            result.update(llm_stop_reason="not_configured")
        else:
            result.update(_llm(db, job, settings, gw, project.id, pv.id, content, force, deadline))
    stop = result.get("llm_stop_reason")
    result["message"] = STOP_MESSAGES.get(str(stop)) if stop else None
    audit_service.record(
        db,
        action="relevance.assessed",
        entity_type="job",
        entity_id=job.id,
        actor_id=None,
        project_id=project.id,
        after={k: v for k, v in result.items() if k != "message"},
    )
    target = fsm.CANCELLED if stop == "cancelled" else fsm.SUCCEEDED
    job_repo.finish(db, job.id, fsm.RUNNING, target, result=result)
    db.commit()
    return "cancelled" if stop == "cancelled" else "ok"
