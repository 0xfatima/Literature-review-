"""Controlled autonomy, API side: start a pipeline run with an autonomy policy, answer its approval interrupts,
pause / resume / cancel, return to an earlier stage (a rerun = a new run that supersedes the current one and
invalidates downstream outputs, never deleting them), show what an upstream change affects, confirm carried-forward
decisions, and wake runs whose awaited job or backoff has finished (called by the job sweeper)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import (
    PIPELINE_ACTIVE,
    Job,
    PaperRecord,
    PipelineEvent,
    PipelineRun,
    Project,
    StageInvalidation,
    User,
)
from app.jobs import fsm
from app.pipeline import policy, steps
from app.services import audit_service, job_service, protocol_service
from app.services.job_service import Dispatcher
from app.workflows.pipeline import GRAPH_VERSION

JOB_TYPE = "pipeline.advance"
ANSWER_ACTIONS = ("approve", "continue", "skip", "stop", "retry", "extend")
log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def active(db: Session, project: Project, *, lock: bool = False) -> PipelineRun | None:
    q = select(PipelineRun).where(PipelineRun.project_id == project.id, PipelineRun.status.in_(PIPELINE_ACTIVE))
    return db.scalar(q.with_for_update() if lock else q)


def latest(db: Session, project: Project) -> PipelineRun | None:
    return db.scalar(
        select(PipelineRun).where(PipelineRun.project_id == project.id).order_by(PipelineRun.created_at.desc()).limit(1)
    )


def _event(db: Session, run: PipelineRun, kind: str, stage: str | None, actor: User | None, **payload: Any) -> None:
    db.add(
        PipelineEvent(
            run_id=run.id,
            project_id=run.project_id,
            kind=kind,
            stage=stage,
            payload=payload,
            actor_id=actor.id if actor else None,
        )
    )


def _enqueue(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, run: PipelineRun, resume: dict[str, Any] | None
) -> None:
    try:
        job_service.enqueue(
            db,
            settings,
            dispatch,
            user,
            job_type=JOB_TYPE,
            project_id=run.project_id,
            payload={"run_id": str(run.id), "resume": resume},
            request_key=None,
        )
    except Conflict as e:
        if e.code != "job_already_active":
            raise
        db.rollback()  # an identical advance is already queued: it will do the same work


def start(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    user: User,
    project: Project,
    body: dict[str, Any],
    *,
    supersedes: PipelineRun | None = None,
    reason: str | None = None,
) -> PipelineRun:
    protocol_service.require_writable(project)
    stage = str(body.get("start_stage") or "protocol")
    if stage not in policy.STAGES:
        raise ValidationFailed(
            f"start_stage must be one of {', '.join(policy.STAGES)}.", details=[{"field": "start_stage"}]
        )
    if active(db, project) is not None:
        raise Conflict("A pipeline run is already active for this project.", code="pipeline_active")
    pv = protocol_service.approved(db, project)
    base = supersedes or latest(db, project)
    run = PipelineRun(
        id=uuid.uuid4(),
        project_id=project.id,
        thread_id=uuid.uuid4(),
        graph_version=GRAPH_VERSION,
        status="queued",
        stage=stage,
        start_stage=stage,
        autonomy=policy.normalise_autonomy(body.get("autonomy") or (base.autonomy if base else None)),
        protocol_version_id=(
            base.protocol_version_id if base and base.protocol_version_id else (pv.id if pv else None)
        ),
        supersedes_id=supersedes.id if supersedes else None,
        rerun_reason=reason,
        created_by=user.id,
        counters={},
        outputs={},
    )
    db.add(run)
    db.flush()
    _event(
        db,
        run,
        "rerun" if supersedes else "run_created",
        stage,
        user,
        reason=reason,
        supersedes=str(supersedes.id) if supersedes else None,
    )
    audit_service.record(
        db,
        action="pipeline.started",
        entity_type="pipeline_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={"start_stage": stage, "rerun": bool(supersedes)},
    )
    _enqueue(db, settings, dispatch, user, run, None)
    db.refresh(run)
    return run


def get_run(db: Session, project: Project, run_id: uuid.UUID) -> PipelineRun:
    run = db.scalar(select(PipelineRun).where(PipelineRun.id == run_id, PipelineRun.project_id == project.id))
    if run is None:
        raise NotFound("Pipeline run not found.")
    return run


def answer(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project, body: dict[str, Any]
) -> PipelineRun:
    protocol_service.require_writable(project)
    run = active(db, project, lock=True)
    if run is None:
        raise Conflict("No pipeline run is active.", code="pipeline_not_active")
    action = str(body.get("action") or "")
    if action not in ANSWER_ACTIONS:
        raise ValidationFailed(f"action must be one of {', '.join(ANSWER_ACTIONS)}.", details=[{"field": "action"}])
    pending = run.pending or {}
    if run.status != "waiting" or pending.get("type") != "approval" or pending.get("key") != body.get("key"):
        raise Conflict("The pipeline is not waiting for this decision any more. Reload.", code="stale_answer")
    if action not in (pending.get("options") or []):
        raise ValidationFailed(f"'{action}' is not an option here.", details=[{"field": "action"}])
    _event(
        db,
        run,
        "approval_given",
        pending.get("stage"),
        user,
        question_kind=pending.get("kind"),
        action=action,
        note=(str(body.get("note") or "")[:500] or None),
    )
    audit_service.record(
        db,
        action="pipeline.answered",
        entity_type="pipeline_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={"kind": pending.get("kind"), "action": action},
    )
    _enqueue(db, settings, dispatch, user, run, {"key": pending["key"], "action": action})
    db.refresh(run)
    return run


def pause(db: Session, user: User, project: Project) -> PipelineRun:
    run = active(db, project, lock=True)
    if run is None:
        raise Conflict("No pipeline run is active.", code="pipeline_not_active")
    if run.status in ("waiting", "queued"):
        run.status = "paused"
        run.row_version += 1
        _event(db, run, "paused", run.stage, user)
        db.commit()
    else:
        raise Conflict("The pipeline is in the middle of a step; pause again in a moment.", code="pipeline_busy")
    return run


def resume(db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project) -> PipelineRun:
    run = active(db, project, lock=True)
    if run is None or run.status != "paused":
        raise Conflict("No paused pipeline run.", code="pipeline_not_paused")
    run.status = "waiting" if run.pending else "queued"
    run.row_version += 1
    _event(db, run, "resumed", run.stage, user)
    from app.pipeline.runtime import auto_answer

    _enqueue(db, settings, dispatch, user, run, auto_answer(db, run.pending))
    db.refresh(run)
    return run


def _cancel_awaited_job(db: Session, user: User, run: PipelineRun) -> None:
    pending = run.pending or {}
    if pending.get("type") != "wait_job":
        return
    job = db.get(Job, uuid.UUID(str(pending["job_id"])))
    if job is not None and job.status in fsm.ACTIVE_STATUSES and fsm.JOB_TYPES[job.type]["cancellable"]:
        try:
            job_service.request_cancel(db, user, job.id)
        except Conflict:
            db.rollback()


def recover(db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project) -> PipelineRun:
    """Continue a failed run from its last checkpoint (the graph state is durable; nothing is redone twice)."""
    protocol_service.require_writable(project)
    if active(db, project) is not None:
        raise Conflict("A pipeline run is already active.", code="pipeline_active")
    run = latest(db, project)
    if run is None or run.status != "failed":
        raise Conflict("There is no failed pipeline run to recover.", code="pipeline_not_failed")
    run.status = "waiting" if run.pending else "queued"
    run.error_summary = None
    run.row_version += 1
    _event(db, run, "recovered", run.stage, user)
    from app.pipeline.runtime import auto_answer

    _enqueue(db, settings, dispatch, user, run, auto_answer(db, run.pending))
    db.refresh(run)
    return run


def cancel(db: Session, user: User, project: Project) -> PipelineRun:
    run = active(db, project, lock=True)
    if run is None:
        raise Conflict("No pipeline run is active.", code="pipeline_not_active")
    _cancel_awaited_job(db, user, run)
    run = active(db, project, lock=True) or run
    run.status, run.finished_at = "cancelled", _now()
    run.row_version += 1
    _event(db, run, "cancelled", run.stage, user)
    audit_service.record(
        db,
        action="pipeline.cancelled",
        entity_type="pipeline_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
    )
    db.commit()
    return run


def rerun(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project, body: dict[str, Any]
) -> PipelineRun:
    """Return to an earlier stage: mark downstream outputs as needing recalculation and start a new run there."""
    protocol_service.require_writable(project)
    stage = str(body.get("stage") or "")
    reason = str(body.get("reason") or "").strip()
    if stage not in policy.STAGES:
        raise ValidationFailed("Choose a stage to return to.", details=[{"field": "stage"}])
    if not reason:
        raise ValidationFailed(
            "Say why you are returning to this stage (it is kept in the history).", details=[{"field": "reason"}]
        )
    current = active(db, project, lock=True)
    prev = current or latest(db, project)
    if current is not None:
        _cancel_awaited_job(db, user, current)
        current = active(db, project, lock=True) or current
        current.status, current.finished_at = "superseded", _now()
        current.row_version += 1
        _event(db, current, "superseded", current.stage, user, reason=reason, return_to=stage)
        db.flush()
    for st in policy.downstream(stage):
        db.add(
            StageInvalidation(
                project_id=project.id,
                pipeline_run_id=prev.id if prev else None,
                stage=st,
                cause="upstream_rerun",
                created_by=user.id,
                detail={"returned_to": stage, "reason": reason},
            )
        )
    db.flush()
    return start(db, settings, dispatch, user, project, {"start_stage": stage}, supersedes=prev, reason=reason)


# ---------- views ----------


def run_view(db: Session, run: PipelineRun, events: int = 60) -> dict[str, Any]:
    evs = list(
        db.scalars(
            select(PipelineEvent)
            .where(PipelineEvent.run_id == run.id)
            .order_by(PipelineEvent.created_at.desc())
            .limit(events)
        )
    )
    prev = db.get(PipelineRun, run.supersedes_id) if run.supersedes_id else None
    changes = {
        s: policy.diff_outputs((prev.outputs or {}).get(s) if prev else None, out)
        for s, out in (run.outputs or {}).items()
        if prev is not None
    }
    return {
        "id": run.id,
        "status": run.status,
        "stage": run.stage,
        "start_stage": run.start_stage,
        "autonomy": run.autonomy,
        "pending": run.pending,
        "outputs": run.outputs,
        "counters": run.counters,
        "supersedes_id": run.supersedes_id,
        "rerun_reason": run.rerun_reason,
        "error_summary": run.error_summary,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "changes_since_previous_run": {k: v for k, v in changes.items() if v},
        "stages": [{"key": s, "label": policy.STAGE_LABELS[s]} for s in policy.STAGES],
        "events": [
            {
                "kind": e.kind,
                "stage": e.stage,
                "payload": e.payload,
                "at": e.created_at,
                "by_person": e.actor_id is not None,
            }
            for e in evs
        ],
    }


def overview(db: Session, settings: Settings, project: Project) -> dict[str, Any]:
    run = active(db, project) or latest(db, project)
    invs = list(
        db.scalars(
            select(StageInvalidation)
            .where(StageInvalidation.project_id == project.id)
            .order_by(StageInvalidation.created_at.desc())
            .limit(30)
        )
    )
    history = list(
        db.scalars(
            select(PipelineRun)
            .where(PipelineRun.project_id == project.id)
            .order_by(PipelineRun.created_at.desc())
            .limit(10)
        )
    )
    return {
        "run": run_view(db, run) if run else None,
        "history": [
            {
                "id": r.id,
                "status": r.status,
                "start_stage": r.start_stage,
                "rerun_reason": r.rerun_reason,
                "created_at": r.created_at,
                "finished_at": r.finished_at,
            }
            for r in history
        ],
        "invalidations": [
            {
                "id": i.id,
                "stage": i.stage,
                "cause": i.cause,
                "detail": i.detail,
                "status": i.status,
                "resolution": i.resolution,
                "created_at": i.created_at,
                "resolved_at": i.resolved_at,
            }
            for i in invs
        ],
        "defaults": {"autonomy": policy.DEFAULT_AUTONOMY, "stages": list(policy.STAGES)},
        "limits": {
            "max_retries": settings.pipeline_max_retries,
            "token_budget": settings.pipeline_token_budget,
            "max_jobs": settings.pipeline_max_jobs,
            "llm_max_concurrent_calls": settings.llm_max_concurrent_calls,
            "llm_daily_token_budget": settings.llm_daily_token_budget,
        },
    }


def impact_view(db: Session, project: Project) -> dict[str, Any]:
    imp = steps.criteria_impact(db, project)
    ids = {uuid.UUID(d["paper_id"]) for k in ("needs_redecision", "needs_review", "carry_forward") for d in imp[k]}
    titles = (
        dict(db.execute(select(PaperRecord.id, PaperRecord.title).where(PaperRecord.id.in_(ids))).all()) if ids else {}
    )
    for k in ("needs_redecision", "needs_review", "carry_forward"):
        imp[k] = [{**d, "title": titles.get(uuid.UUID(d["paper_id"]), "")} for d in imp[k][:200]]
    return imp


def confirm_decisions(
    db: Session, settings: Settings, user: User, project: Project, body: dict[str, Any]
) -> dict[str, Any]:
    """Re-record decisions whose basis is unchanged under the current protocol (one new decision each, so the
    history shows the confirmation). Records needing re-decision are refused; decide them in Screening."""
    from app.services import screening_service

    imp = steps.criteria_impact(db, project)
    allowed = {d["paper_id"]: d for d in imp["carry_forward"]}
    if body.get("include_needs_review"):
        allowed.update({d["paper_id"]: d for d in imp["needs_review"]})
    wanted = [str(p) for p in body.get("paper_ids") or allowed.keys()]
    refused = [p for p in wanted if p not in allowed]
    done = 0
    pv = protocol_service.approved(db, project)
    for pid in wanted:
        d = allowed.get(pid)
        if d is None or d.get("stage") != "title_abstract":
            continue
        screening_service.decide(
            db,
            settings,
            user,
            project,
            uuid.UUID(pid),
            decision=d["decision"],
            reason_code=d.get("reason_code"),
            note=f"Confirmed under protocol version {pv.version if pv else '?'} after the criteria changed.",
            expected_row_version=None,
        )
        done += 1
    run = active(db, project)
    if run is not None:
        _event(db, run, "decisions_confirmed", "screening", user, confirmed=done, refused=len(refused))
        db.commit()
    return {"confirmed": done, "refused": refused}


def dismiss_invalidation(db: Session, user: User, project: Project, body: dict[str, Any]) -> dict[str, Any]:
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise ValidationFailed("Give a reason for dismissing this.", details=[{"field": "reason"}])
    try:
        inv_id = uuid.UUID(str(body.get("invalidation_id")))
    except ValueError:
        raise ValidationFailed("invalidation_id must be a UUID.") from None
    inv = db.scalar(
        select(StageInvalidation).where(StageInvalidation.id == inv_id, StageInvalidation.project_id == project.id)
    )
    if inv is None:
        raise NotFound("Invalidation not found.")
    inv.status, inv.resolved_at, inv.resolution = "dismissed", _now(), f"Dismissed by a reviewer: {reason}"
    audit_service.record(
        db,
        action="pipeline.invalidation_dismissed",
        entity_type="stage_invalidation",
        entity_id=inv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"reason": reason},
    )
    db.commit()
    return {"id": inv.id, "status": inv.status}


# ---------- sweeper hook ----------


def wake(db: Session, settings: Settings, dispatch: Dispatcher) -> int:
    """Enqueue an advance for every waiting run whose awaited job finished or whose backoff elapsed."""
    from app.pipeline.runtime import auto_answer

    woken = 0
    for run in db.scalars(select(PipelineRun).where(PipelineRun.status == "waiting")):
        resume_payload = auto_answer(db, run.pending)
        if resume_payload is None or run.created_by is None:
            continue
        user = db.get(User, run.created_by)
        if user is None:
            continue
        _enqueue(db, settings, dispatch, user, run, resume_payload)
        woken += 1
    return woken
