"""Runs the pipeline graph inside a `pipeline.advance` job and mirrors its position into `pipeline_runs`.

Safe to run more than once for the same job (redelivery after a killed worker): the LangGraph checkpoint is the
position of record; a resume payload is applied only while the checkpoint is waiting on that payload's key, and a
crashed mid-step run continues from its last checkpoint. Jobs the graph dispatched are found again by their
idempotency key, so nothing is started twice."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_rate_limiter
from app.core.config import get_settings
from app.db.models import Job, PipelineEvent, PipelineRun, Project, User
from app.jobs import fsm
from app.jobs.celery_app import send_task
from app.pipeline import steps
from app.services.job_service import Dispatcher
from app.workflows.pipeline import compile_graph
from app.workflows.runtime import checkpointer

log = logging.getLogger(__name__)
MAX_INLINE_ROUNDS = 6  # resume immediately (without waiting for the sweeper) when what we wait for is already over


def _now() -> datetime:
    return datetime.now(UTC)


def _cfg(run: PipelineRun, ctx: steps.Ctx) -> RunnableConfig:
    return {"configurable": {"thread_id": str(run.thread_id), "pipeline_ctx": ctx}, "recursion_limit": 100}


def initial_state(run: PipelineRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "start_stage": run.start_stage,
        "stage": run.start_stage,
        "autonomy": dict(run.autonomy or {}),
        "approved": {},
        "skipped": [],
        "completed": [],
        "outputs": {},
        "retries": {},
        "not_before": {},
        "failed": {},
        "waiting": {},
        "extra_budget": 0,
        "stop_reason": None,
        "log": [_event_dict("run_started", run.start_stage, autonomy=dict(run.autonomy or {}))],
    }


def _event_dict(kind: str, stage: str | None, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "stage": stage, "payload": payload, "at": _now().isoformat()}


def auto_answer(db: Session, pending: dict[str, Any] | None) -> dict[str, Any] | None:
    """The resume payload for a wait we can settle without a person (job finished, backoff elapsed), or None."""
    if not pending:
        return None
    if pending.get("type") == "wait_job":
        job = db.get(Job, uuid.UUID(str(pending["job_id"])))
        if job is None:
            return {"key": pending["key"], "job_status": "failed", "error": "The job no longer exists."}
        if job.status in fsm.TERMINAL_STATUSES:
            return {"key": pending["key"], "job_status": job.status, "error": job.error_summary}
        return None
    if pending.get("type") == "backoff":
        until = pending.get("until")
        if until and datetime.fromisoformat(until) <= _now():
            return {"key": pending["key"]}
    return None


def sync(db: Session, run: PipelineRun, values: dict[str, Any], interrupts: list[dict[str, Any]]) -> None:
    written = int((run.counters or {}).get("events_written", 0))
    entries = list(values.get("log") or [])
    for e in entries[written:]:
        db.add(
            PipelineEvent(
                run_id=run.id,
                project_id=run.project_id,
                kind=str(e.get("kind"))[:32],
                stage=e.get("stage"),
                payload=e.get("payload") or {},
            )
        )
    run.counters = {
        **(run.counters or {}),
        "events_written": len(entries),
        "retries": values.get("retries") or {},
        "skipped": values.get("skipped") or [],
    }
    run.outputs = values.get("outputs") or {}
    run.stage = values.get("stage") or run.stage
    run.updated_at = _now()
    run.row_version += 1
    if interrupts:
        run.status, run.pending = "waiting", interrupts[0]
        if run.pending and run.pending.get("type") == "approval":
            db.add(
                PipelineEvent(
                    run_id=run.id,
                    project_id=run.project_id,
                    kind="approval_requested",
                    stage=run.pending.get("stage"),
                    payload={"question_kind": run.pending.get("kind"), "key": run.pending.get("key")},
                )
            )
    else:
        run.pending = None
        run.finished_at = _now()
        run.status = "cancelled" if values.get("stop_reason") else "completed"


def advance(db: Session, job: Job, dispatch: Dispatcher | None = None) -> str:
    """Move the run forward for this job; returns the run's status."""
    run = db.scalar(select(PipelineRun).where(PipelineRun.id == uuid.UUID(str(job.input["run_id"]))).with_for_update())
    if run is None:
        return "run_missing"
    if run.status not in ("queued", "running", "waiting"):
        db.rollback()
        return "not_runnable"  # paused, cancelled, superseded or finished: a person decides what happens next
    resume = job.input.get("resume")
    if run.status == "waiting" and resume and (run.pending or {}).get("key") != resume.get("key"):
        db.rollback()
        return "stale_resume"
    run.status = "running"
    db.commit()
    project = db.get(Project, run.project_id)
    user = db.get(User, run.created_by) if run.created_by else None
    if project is None or user is None:
        run.status, run.error_summary = "failed", "The project or its owner no longer exists."
        db.commit()
        return "failed"
    from app.services import user_llm_service

    ctx = steps.Ctx(
        db,
        user_llm_service.settings_for(db, get_settings(), user.id),
        dispatch or send_task,
        get_rate_limiter(),
        user,
        project,
        run,
    )
    # End the ORM transaction the lookups above opened BEFORE entering the checkpointer. Its first use in a worker
    # process runs LangGraph's setup (CREATE INDEX CONCURRENTLY), which waits for every open transaction to finish,
    # including this session's own, so an open transaction here hangs the worker forever (and, through the setup
    # advisory lock, every other worker process too).
    db.commit()
    with checkpointer() as saver:
        graph = compile_graph(saver)
        cfg = _cfg(run, ctx)
        for _ in range(MAX_INLINE_ROUNDS):
            snap = graph.get_state(cfg)
            pending = [i.value for i in snap.interrupts]
            if not snap.values:
                graph.invoke(initial_state(run), cfg)
            elif pending and resume and pending[0].get("key") == resume.get("key"):
                graph.invoke(Command(resume=resume), cfg)
            elif snap.next and not pending:
                graph.invoke(None, cfg)  # crashed mid-step: continue from the last checkpoint
            snap = graph.get_state(cfg)
            pending = [i.value for i in snap.interrupts]
            resume = auto_answer(db, pending[0] if pending else None)
            if resume is None:
                break
        sync(db, run, dict(snap.values or {}), pending)
    db.commit()
    return run.status
