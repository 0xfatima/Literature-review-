"""One small operation per review stage, called by the pipeline graph's nodes. Kept outside the graph so each is
testable on its own.

`evaluate(stage, ctx)` only READS stored data and says what the stage needs next:
  done   – the stage's output exists and nothing upstream invalidated it
  job    – routine background work should run (or is running: `job_id` set)
  human  – a person must decide or review something (always an interrupt; never automated)
`dispatch(stage, ctx, key)` starts that background work through the normal service (same permissions, consent gates,
rate limits) with a deterministic idempotency key, so a replayed node never starts a second job.
Two side effects happen during evaluation and are idempotent: recording invalidations when the approved protocol
changed, and generating the report draft (skipped when a draft for the same data fingerprint exists).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.db.models import (
    AppraisalRecord,
    DraftVersion,
    EvidenceItem,
    Job,
    ModelRun,
    PaperRecord,
    PipelineRun,
    Project,
    ProtocolVersion,
    RelevanceAssessment,
    ScreeningDecision,
    ScreeningState,
    SearchRun,
    StageInvalidation,
    SynthesisVersion,
    User,
)
from app.extraction import schema_service
from app.jobs import fsm
from app.pipeline import impact, policy
from app.repositories import jobs as job_repo
from app.services import protocol_service
from app.services.job_service import Dispatcher


@dataclass
class Ctx:
    db: Session
    settings: Settings
    dispatch: Dispatcher
    limiter: RateLimiter
    user: User
    project: Project
    run: PipelineRun


@dataclass
class Eval:
    status: str  # done | job | human
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None  # job already running for this stage
    kind: str = ""  # human: what to do; job: which operation
    detail: dict[str, Any] = field(default_factory=dict)
    ai: bool = False  # the job sends data to an AI provider


def _now() -> datetime:
    return datetime.now(UTC)


def open_invalidations(db: Session, project_id: uuid.UUID, stage: str) -> list[StageInvalidation]:
    return list(
        db.scalars(
            select(StageInvalidation).where(
                StageInvalidation.project_id == project_id,
                StageInvalidation.stage == stage,
                StageInvalidation.status == "open",
            )
        )
    )


def resolve(db: Session, inv: StageInvalidation, resolution: str) -> None:
    inv.status, inv.resolved_at, inv.resolution = "resolved", _now(), resolution


def job_by_key(ctx: Ctx, key: str) -> Job | None:
    return job_repo.get_by_request_key(ctx.db, ctx.user.id, key)


def tokens_used(ctx: Ctx) -> int:
    since = ctx.run.created_at
    v = ctx.db.scalar(
        select(
            func.coalesce(
                func.sum(func.coalesce(ModelRun.input_tokens, 0) + func.coalesce(ModelRun.output_tokens, 0)), 0
            )
        ).where(ModelRun.project_id == ctx.project.id, ModelRun.created_at >= since)
    )
    return int(v or 0)


# ---------- protocol ----------


def record_protocol_change(ctx: Ctx, old: ProtocolVersion, new: ProtocolVersion) -> list[str]:
    """Open invalidations (idempotently) for the stages a newly approved protocol version affects."""
    affected = (new.impact or {}).get("affected_stages") or []
    mapping = {
        "SEARCH": "search",
        "RECORD_SCREENING": "screening",
        "FULL_TEXT_SCREENING": "screening",
        "EXTRACTION": "extraction",
        "SYNTHESIS": "synthesis",
        "REPORT": "report",
    }
    stages = sorted({mapping[s] for s in affected if s in mapping}, key=policy.STAGES.index)
    if stages:  # everything after the earliest affected stage depends on it
        stages = [stages[0], *policy.downstream(stages[0])]
    created = []
    for st in stages:
        exists = ctx.db.scalar(
            select(StageInvalidation.id).where(
                StageInvalidation.project_id == ctx.project.id,
                StageInvalidation.stage == st,
                StageInvalidation.cause == "protocol_change",
                StageInvalidation.detail["new_protocol_version_id"].astext == str(new.id),
            )
        )
        if exists:
            continue
        ctx.db.add(
            StageInvalidation(
                project_id=ctx.project.id,
                pipeline_run_id=ctx.run.id,
                stage=st,
                cause="protocol_change",
                created_by=ctx.user.id,
                detail={
                    "old_protocol_version": old.version,
                    "new_protocol_version": new.version,
                    "new_protocol_version_id": str(new.id),
                    "triggers": (new.impact or {}).get("triggers", {}),
                },
            )
        )
        created.append(st)
    return created


def eval_protocol(ctx: Ctx) -> Eval:
    pv = protocol_service.approved(ctx.db, ctx.project)
    if pv is None:
        return Eval(
            "human",
            "Approve the review protocol (chat workflow or Protocol tab), then continue.",
            kind="approve_protocol",
        )
    run = ctx.run
    if run.protocol_version_id and run.protocol_version_id != pv.id:
        old = ctx.db.get(ProtocolVersion, run.protocol_version_id)
        if old is not None:
            created = record_protocol_change(ctx, old, pv)
            run.counters = {
                **(run.counters or {}),
                "protocol_changes": int((run.counters or {}).get("protocol_changes", 0)) + 1,
                "last_invalidated": created,
            }
    run.protocol_version_id = pv.id
    return Eval("done", summary={"protocol_version": pv.version})


# ---------- search ----------


def eval_search(ctx: Ctx) -> Eval:
    db, pid = ctx.db, ctx.project.id
    invs = open_invalidations(db, pid, "search")
    active = db.scalar(
        select(SearchRun).where(SearchRun.project_id == pid, SearchRun.status.in_(("queued", "running")))
    )
    if active is not None and active.job_id:
        return Eval("job", "A search is running.", job_id=str(active.job_id), kind="search")
    papers = db.scalar(select(func.count()).select_from(PaperRecord).where(PaperRecord.project_id == pid)) or 0
    done_run = db.scalar(
        select(SearchRun)
        .where(SearchRun.project_id == pid, SearchRun.status.in_(("completed", "partial")))
        .order_by(SearchRun.created_at.desc())
        .limit(1)
    )
    if done_run is not None and papers and not any(i.created_at > done_run.created_at for i in invs):
        for i in invs:
            resolve(db, i, f"Search run of {done_run.created_at.date()} reflects the current protocol.")
        return Eval("done", summary={"papers": papers})
    msg = "Run the search derived from the approved protocol (open-access sources)."
    if invs:
        msg = "The protocol's search fields changed; run a new search (earlier records are kept)."
    return Eval("job", msg, kind="search")


def dispatch_search(ctx: Ctx, key: str) -> str:
    from app.services import search_service

    run = search_service.start(
        ctx.db,
        ctx.settings,
        ctx.dispatch,
        ctx.limiter,
        ctx.user,
        ctx.project,
        tier="open_access",
        expression=None,
        provider_keys=None,
        request_key=key,
    )
    if run.job_id is None:
        raise RuntimeError("search run has no job")
    return str(run.job_id)


# ---------- screening support ----------


def criteria_impact(db: Session, project: Project) -> dict[str, Any]:
    """Decisions recorded under an older protocol version, classified against the current criteria."""
    pv = protocol_service.approved(db, project)
    if pv is None:
        return {"counts": {}, "needs_redecision": [], "needs_review": [], "carry_forward": [], "criteria_changes": {}}
    rows = db.execute(
        select(ScreeningState, ScreeningDecision)
        .join(ScreeningDecision, ScreeningDecision.id == ScreeningState.decision_id)
        .where(ScreeningState.project_id == project.id, ScreeningState.protocol_version_id != pv.id)
    ).all()
    new = protocol_service.to_content(pv)["criteria"]
    by_old: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for st, d in rows:
        by_old.setdefault(st.protocol_version_id, []).append(
            {"paper_id": str(st.paper_id), "decision": st.decision, "reason_code": d.reason_code, "stage": st.stage}
        )
    merged: dict[str, Any] = {"needs_redecision": [], "needs_review": [], "carry_forward": [], "criteria_changes": {}}
    for old_id, decisions in by_old.items():
        old = db.get(ProtocolVersion, old_id)
        res = impact.classify(decisions, protocol_service.to_content(old)["criteria"] if old else [], new)
        for k in ("needs_redecision", "needs_review", "carry_forward"):
            merged[k] += res[k]
        merged["criteria_changes"] = res["criteria_changes"]
    merged["counts"] = {k: len(merged[k]) for k in ("needs_redecision", "needs_review", "carry_forward")}
    merged["protocol_version"] = pv.version
    return merged


def eval_screening(ctx: Ctx) -> Eval:
    from app.prisma import service as prisma_service

    db, project = ctx.db, ctx.project
    pv = protocol_service.approved(db, project)
    invs = open_invalidations(db, project.id, "screening")
    imp = criteria_impact(db, project)
    stale = sum(imp["counts"].values()) if imp["counts"] else 0
    if stale:
        return Eval(
            "human",
            f"The eligibility criteria changed: {stale} earlier decision(s) must be confirmed or "
            "re-decided under the current protocol before screening can be considered complete.",
            kind="criteria_changed",
            detail={"counts": imp["counts"], "criteria_changes": imp["criteria_changes"]},
        )
    for i in invs:
        resolve(db, i, "Every screening decision is now recorded under the current protocol.")
    papers = db.scalar(select(func.count()).select_from(PaperRecord).where(PaperRecord.project_id == project.id)) or 0
    if pv is not None and papers:
        assessed = (
            db.scalar(
                select(func.count(func.distinct(RelevanceAssessment.paper_id))).where(
                    RelevanceAssessment.project_id == project.id,
                    RelevanceAssessment.protocol_version_id == pv.id,
                    RelevanceAssessment.method == "lexical",
                )
            )
            or 0
        )
        if assessed < papers:
            running = db.scalar(
                select(Job)
                .where(
                    Job.project_id == project.id, Job.type == "relevance.assess", Job.status.in_(fsm.ACTIVE_STATUSES)
                )
                .limit(1)
            )
            return Eval(
                "job",
                f"Rank {papers - assessed} record(s) by relevance (advisory; never a decision).",
                job_id=str(running.id) if running else None,
                kind="relevance",
            )
    flow = prisma_service.flow_view(prisma_service.get_flow(db, project.id))
    if flow["pending_ta"] or not flow["screened"]:
        return Eval(
            "human",
            f"Screen the remaining {flow['pending_ta'] or papers} record(s) by title and abstract.",
            kind="screen_records",
            detail={"pending": flow["pending_ta"]},
        )
    pending_ft = int(flow["pending_retrieval"]) + int(flow["pending_ft"])
    if pending_ft:
        return Eval(
            "human",
            f"Obtain and assess {pending_ft} full text(s) (upload PDFs you are authorised to use).",
            kind="full_text",
            detail={"pending": pending_ft},
        )
    return Eval(
        "done",
        summary={
            "screened": flow["screened"],
            "included_studies": flow["included_studies"],
            "excluded": int(flow["ta_excluded"]) + int(flow["ft_excluded"]),
        },
    )


def dispatch_relevance(ctx: Ctx, key: str) -> str:
    from app.services import relevance_service

    job = relevance_service.start(
        ctx.db,
        ctx.settings,
        ctx.dispatch,
        ctx.limiter,
        ctx.user,
        ctx.project,
        use_llm=False,
        force=False,
        request_key=key,
    )
    return str(job.id)


# ---------- extraction ----------


def _consent_ok(ctx: Ctx) -> bool:
    from app.providers.llm import build_gateway
    from app.services import llm_jobs

    gw = build_gateway(ctx.settings)
    _, block = llm_jobs.gate(ctx.db, ctx.project, gw)
    return block is None


def eval_extraction(ctx: Ctx) -> Eval:
    db, project = ctx.db, ctx.project
    schema = schema_service.current_schema(db, project)
    if schema is None:
        return Eval("human", "Approve an extraction schema (Extraction tab).", kind="approve_schema")
    included = schema_service.included_paper_ids(db, project)
    if not included:
        return Eval("done", summary={"included_studies": 0})
    invs = open_invalidations(db, project.id, "extraction")
    have = set(db.scalars(select(EvidenceItem.paper_id).where(EvidenceItem.extraction_schema_id == schema.id)))
    missing = [p for p in included if p not in have]
    running = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.type == "extraction.run", Job.status.in_(fsm.ACTIVE_STATUSES))
        .limit(1)
    )
    if running is not None:
        return Eval("job", "Evidence extraction is running.", job_id=str(running.id), kind="extraction", ai=True)
    if missing and _consent_ok(ctx) and not ctx.run.counters.get("extraction_dispatched"):
        return Eval(
            "job",
            f"Extract evidence from {len(missing)} included paper(s) with AI (values are proposals; "
            "you review each one).",
            kind="extraction",
            ai=True,
        )
    unreviewed = (
        db.scalar(
            select(func.count())
            .select_from(EvidenceItem)
            .where(
                EvidenceItem.extraction_schema_id == schema.id,
                EvidenceItem.origin == "ai",
                EvidenceItem.review_status == "unreviewed",
                EvidenceItem.status != "pending",
            )
        )
        or 0
    )
    if unreviewed or missing:
        return Eval(
            "human",
            (f"Review {unreviewed} AI-extracted value(s)" if unreviewed else "Enter evidence")
            + (f"; {len(missing)} paper(s) have no values yet" if missing else "")
            + " (Tables tab).",
            kind="review_evidence",
            detail={"unreviewed": unreviewed, "missing": len(missing)},
        )
    for i in invs:
        resolve(db, i, "Evidence for the included studies has been reviewed.")
    reviewed = (
        db.scalar(
            select(func.count())
            .select_from(EvidenceItem)
            .where(
                EvidenceItem.extraction_schema_id == schema.id, EvidenceItem.review_status.in_(("approved", "edited"))
            )
        )
        or 0
    )
    return Eval("done", summary={"schema_version": schema.version, "reviewed_values": reviewed})


def dispatch_extraction(ctx: Ctx, key: str) -> str:
    from app.services import extraction_service

    schema = schema_service.current_schema(ctx.db, ctx.project)
    assert schema is not None
    job = extraction_service.start(
        ctx.db,
        ctx.settings,
        ctx.dispatch,
        ctx.limiter,
        ctx.user,
        ctx.project,
        schema.id,
        paper_ids=None,
        force=False,
        request_key=key,
    )
    ctx.run.counters = {**(ctx.run.counters or {}), "extraction_dispatched": True}
    return str(job.id)


# ---------- appraisal ----------


def eval_appraisal(ctx: Ctx) -> Eval:
    db, project = ctx.db, ctx.project
    included = schema_service.included_paper_ids(db, project)
    if not included:
        return Eval("done", summary={"appraised": 0})
    approved = set(
        db.scalars(
            select(AppraisalRecord.paper_id).where(
                AppraisalRecord.paper_id.in_(included), AppraisalRecord.status == "approved"
            )
        )
    )
    todo = [p for p in included if p not in approved]
    running = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.type == "appraisal.suggest", Job.status.in_(fsm.ACTIVE_STATUSES))
        .limit(1)
    )
    if running is not None:
        return Eval("job", "AI appraisal suggestions are running.", job_id=str(running.id), kind="appraisal", ai=True)
    if todo and not ctx.run.counters.get("appraisal_dispatched") and _consent_ok(ctx):
        return Eval(
            "job",
            f"Ask AI for appraisal suggestions for {len(todo)} study(ies) (suggestions only).",
            kind="appraisal",
            ai=True,
        )
    if todo:
        return Eval(
            "human",
            f"Approve the critical appraisal of {len(todo)} of {len(included)} study(ies) (Appraisal tab).",
            kind="approve_appraisals",
            detail={"remaining": len(todo)},
        )
    for i in open_invalidations(db, project.id, "appraisal"):
        resolve(db, i, "Every included study has an approved appraisal.")
    return Eval("done", summary={"appraised": len(included)})


def dispatch_appraisal(ctx: Ctx, key: str) -> str:
    from app.appraisal import service as appraisal_service

    job = appraisal_service.start_suggestions(
        ctx.db,
        ctx.settings,
        ctx.dispatch,
        ctx.limiter,
        ctx.user,
        ctx.project,
        paper_ids=None,
        frameworks=None,
        request_key=key,
    )
    ctx.run.counters = {**(ctx.run.counters or {}), "appraisal_dispatched": True}
    return str(job.id)


# ---------- synthesis ----------


def eval_synthesis(ctx: Ctx) -> Eval:
    db, project = ctx.db, ctx.project
    if schema_service.current_schema(db, project) is None:
        return Eval("human", "Approve an extraction schema before synthesis.", kind="approve_schema")
    invs = open_invalidations(db, project.id, "synthesis")
    versions = list(
        db.scalars(
            select(SynthesisVersion)
            .where(SynthesisVersion.project_id == project.id)
            .order_by(SynthesisVersion.version.desc())
        )
    )
    draft = next((v for v in versions if v.status == "draft"), None)
    accepted = next((v for v in versions if v.status == "accepted"), None)
    running = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.type == "synthesis.draft", Job.status.in_(fsm.ACTIVE_STATUSES))
        .limit(1)
    )
    if running is not None:
        return Eval("job", "A synthesis draft is being generated.", job_id=str(running.id), kind="synthesis")
    if draft is not None:
        return Eval(
            "human",
            f"Review every statement of synthesis draft v{draft.version} and accept it (Synthesis tab).",
            kind="accept_synthesis",
            detail={"draft_version": draft.version},
        )
    newest_inv = max((i.created_at for i in invs), default=None)
    if accepted is not None and (newest_inv is None or (accepted.accepted_at and accepted.accepted_at > newest_inv)):
        for i in invs:
            resolve(db, i, f"Synthesis v{accepted.version} was accepted after the change.")
        return Eval("done", summary={"synthesis_version": accepted.version})
    msg = "Draft the synthesis from the reviewed evidence."
    if accepted is not None:
        msg = (
            f"Upstream data changed since synthesis v{accepted.version} was accepted; draft a new version "
            "(the accepted one is kept)."
        )
    return Eval("job", msg, kind="synthesis", ai=bool(ctx.run.autonomy.get("synthesis_ai")))


def dispatch_synthesis(ctx: Ctx, key: str) -> str:
    from app.synthesis import service as synthesis_service

    job = synthesis_service.start_draft(
        ctx.db,
        ctx.settings,
        ctx.dispatch,
        ctx.limiter,
        ctx.user,
        ctx.project,
        {"use_ai": bool(ctx.run.autonomy.get("synthesis_ai"))},
        request_key=key,
    )
    return str(job.id)


# ---------- report ----------


def eval_report(ctx: Ctx) -> Eval:
    from app.reports import build as report_build
    from app.reports import service as report_service

    db, project = ctx.db, ctx.project
    current = report_build.review_state(db, project)["fingerprint"]
    latest = db.scalar(
        select(DraftVersion).where(DraftVersion.project_id == project.id).order_by(DraftVersion.version.desc()).limit(1)
    )
    if latest is None or (latest.review_state or {}).get("fingerprint") != current:
        if not ctx.run.autonomy.get("report", True):
            return Eval("job", "Generate a new report draft from the current data.", kind="report")
        latest = report_service.generate(
            db, project, ctx.user, {"note": f"Generated by pipeline run {str(ctx.run.id)[:8]}"}
        )
    for i in open_invalidations(db, project.id, "report"):
        resolve(db, i, f"Report draft v{latest.version} reflects the current data.")
    s = (latest.validation or {}).get("summary") or {}
    if not ctx.run.counters.get("report_reviewed"):
        return Eval(
            "human",
            f"Verify report draft v{latest.version} ({s.get('errors', 0)} error(s), "
            f"{s.get('warnings', 0)} warning(s)). The application never marks a review publication-ready.",
            kind="verify_report",
            summary={"report_version": latest.version},
        )
    return Eval("done", summary={"report_version": latest.version, "errors": s.get("errors", 0)})


EVALUATORS = {
    "protocol": eval_protocol,
    "search": eval_search,
    "screening": eval_screening,
    "extraction": eval_extraction,
    "appraisal": eval_appraisal,
    "synthesis": eval_synthesis,
    "report": eval_report,
}
DISPATCHERS = {
    "search": dispatch_search,
    "relevance": dispatch_relevance,
    "extraction": dispatch_extraction,
    "appraisal": dispatch_appraisal,
    "synthesis": dispatch_synthesis,
}


def evaluate(stage: str, ctx: Ctx) -> Eval:
    return EVALUATORS[stage](ctx)


def dispatch(kind: str, ctx: Ctx, key: str) -> str:
    """Start the job (once): an existing job with this idempotency key is reused instead of starting another."""
    existing = job_by_key(ctx, key)
    if existing is not None:
        return str(existing.id)
    if kind == "report":
        from app.reports import service as report_service

        report_service.generate(ctx.db, ctx.project, ctx.user, {"note": "Generated by the pipeline"})
        return ""
    return DISPATCHERS[kind](ctx, key)
