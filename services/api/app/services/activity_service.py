"""One review step at a time, and pausing / resuming it.

A "step" is the long background work of one review stage: a scholarly search, the relevance (AI screening)
assessment, AI extraction, AI appraisal suggestions or a synthesis draft. Only one may be queued or running per
project at a time, so results never race each other (screening while the search is still adding records, extraction
while screening decisions change) and the top bar can always say exactly what is running.

Pausing is a cooperative stop that is remembered: the step's job is cancelled the same way "Cancel" does (the worker
stops between pages / batches / papers and keeps everything finished so far) and is marked `paused_at`. Resuming
starts a continuation of that job (`rerun_of_job_id` = the paused job): a search continues its providers from their
saved cursors, the other steps skip the work they already finished. A synthesis draft commits in one go and cannot be
paused. If the automation (pipeline) is waiting for the step, it is paused and resumed with it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import Settings
from app.core.errors import Conflict
from app.db.models import Job, Project, User
from app.jobs import fsm
from app.services.job_service import Dispatcher

STEP_JOB_TYPES = ("search.run", "relevance.assess", "extraction.run", "appraisal.suggest", "synthesis.draft")
PAUSABLE = frozenset({"search.run", "relevance.assess", "extraction.run", "appraisal.suggest"})
STEP_LABELS = {
    "search.run": "The search",
    "relevance.assess": "AI screening",
    "extraction.run": "AI extraction",
    "appraisal.suggest": "AI appraisal",
    "synthesis.draft": "The synthesis draft",
}


def _now() -> datetime:
    return datetime.now(UTC)


def running_step(db: Session, project_id: uuid.UUID) -> Job | None:
    return db.scalar(
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.type.in_(STEP_JOB_TYPES),
            Job.status.in_(fsm.ACTIVE_STATUSES),
        )
        .order_by(Job.created_at)
        .limit(1)
    )


def require_no_running_step(db: Session, project_id: uuid.UUID | None, job_type: str) -> None:
    """Called by job_service.enqueue before anything is written."""
    if project_id is None or job_type not in STEP_JOB_TYPES:
        return
    other = running_step(db, project_id)
    if other is not None:
        raise Conflict(
            f"{STEP_LABELS.get(other.type, 'Another step')} is still running. Only one step runs at a time: wait for "
            "it to finish, or pause it (top right) first.",
            code="step_running",
            details=[{"job_id": str(other.id), "type": other.type}],
        )


def paused_step(db: Session, project_id: uuid.UUID) -> Job | None:
    """The most recently paused step that has not been resumed (and was not finished anyway)."""
    job = db.scalar(
        select(Job)
        .where(Job.project_id == project_id, Job.type.in_(STEP_JOB_TYPES), Job.paused_at.is_not(None))
        .order_by(Job.paused_at.desc())
        .limit(1)
    )
    if job is None or job.status == fsm.SUCCEEDED:
        return None
    resumed = db.scalar(select(Job.id).where(Job.rerun_of_job_id == job.id).limit(1))
    return None if resumed is not None else job


def _pipeline(db: Session, project: Project, *, lock: bool = False) -> Any:
    from app.pipeline import service as pipeline_service

    return pipeline_service.active(db, project, lock=lock)


def view(db: Session, project: Project) -> dict[str, Any]:
    running = running_step(db, project.id)
    paused = None if running is not None else paused_step(db, project.id)
    pl = _pipeline(db, project)
    return {
        "running": running,
        "running_label": STEP_LABELS.get(running.type) if running else None,
        "can_pause": bool(running and running.type in PAUSABLE and not running.cancel_requested),
        "paused": paused,
        "paused_label": STEP_LABELS.get(paused.type) if paused else None,
        "pausing": bool(paused and paused.status in fsm.ACTIVE_STATUSES),
        "automation": pl.status if pl is not None else None,
    }


def pause(db: Session, user: User, project: Project) -> dict[str, Any]:
    from app.services import job_service, search_service

    job = running_step(db, project.id)
    if job is None:
        raise Conflict("Nothing is running to pause.", code="nothing_running")
    if job.type not in PAUSABLE:
        raise Conflict(
            f"{STEP_LABELS[job.type]} cannot be paused safely; it finishes on its own in a moment.",
            code="not_pausable",
        )
    pl = _pipeline(db, project, lock=True)
    if pl is not None and pl.status in ("waiting", "queued"):
        from app.pipeline import service as pipeline_service

        pipeline_service.pause(db, user, project)  # commits
    elif pl is not None and pl.status == "running":
        db.rollback()
        raise Conflict("The automation is between two steps; press Pause again in a moment.", code="pipeline_busy")
    job = db.get(Job, job.id)
    assert job is not None
    if job.type == "search.run" and job.input.get("search_run_id"):
        search_service.cancel(db, user, project, uuid.UUID(str(job.input["search_run_id"])))
    else:
        job_service.request_cancel(db, user, job.id)
    job = db.get(Job, job.id)
    assert job is not None
    job.paused_at = _now()
    from app.services import audit_service

    audit_service.record(
        db, action="job.paused", entity_type="job", entity_id=job.id, actor_id=user.id, project_id=project.id
    )
    db.commit()
    return view(db, project)


def resume(db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project) -> dict[str, Any]:
    from app.services import audit_service, job_service, search_service

    if running_step(db, project.id) is not None:
        raise Conflict("A step is already running.", code="step_running")
    job = paused_step(db, project.id)
    if job is None:
        raise Conflict("Nothing is paused.", code="nothing_paused")
    if job.status in fsm.ACTIVE_STATUSES:
        raise Conflict("The step is still stopping; resume it in a few seconds.", code="still_pausing")
    if job.type == "search.run":
        run = search_service.retry(db, settings, dispatch, user, project, uuid.UUID(str(job.input["search_run_id"])))
        new_id = run.job_id
    else:
        payload = dict(job.input or {})
        if "nonce" in payload:
            payload["nonce"] = str(uuid.uuid4())
        new, _ = job_service.enqueue(
            db,
            settings,
            dispatch,
            user,
            job_type=job.type,
            project_id=project.id,
            payload=payload,
            request_key=None,
            rerun_of=job.id,
        )
        new_id = new.id
    audit_service.record(
        db,
        action="job.resumed",
        entity_type="job",
        entity_id=job.id,
        actor_id=user.id,
        project_id=project.id,
        after={"continued_by_job_id": str(new_id)},
    )
    db.commit()
    pl = _pipeline(db, project, lock=True)
    if pl is not None and pl.status == "paused":
        pending = dict(pl.pending or {})
        if pending.get("type") == "wait_job" and str(pending.get("job_id")) == str(job.id) and new_id is not None:
            pending["job_id"] = str(new_id)  # the automation now waits for the continuation
            pl.pending = pending
            flag_modified(pl, "pending")
            db.flush()
        from app.pipeline import service as pipeline_service

        pipeline_service.resume(db, settings, dispatch, user, project)
    else:
        db.commit()
    return view(db, project)
