"""Job orchestration. Postgres is the source of truth; Redis/Celery is only the transport."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Job, User
from app.jobs import fsm
from app.repositories import jobs as repo
from app.services import audit_service

log = logging.getLogger(__name__)
Dispatcher = Callable[[str, str], None]  # (task_name, job_id) -> None; raises on broker failure
TASK_FOR_TYPE = {"system.ping": "system.ping"}


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    user: User,
    *,
    job_type: str,
    project_id: uuid.UUID | None,
    payload: dict[str, Any],
    request_key: str | None,
    rerun_of: uuid.UUID | None = None,
) -> tuple[Job, bool]:
    """Returns (job, created). created=False means an Idempotency-Key replay returned the existing job."""
    spec = fsm.JOB_TYPES.get(job_type)
    if spec is None:
        raise ValidationFailed(f"Unknown job type '{job_type}'.")
    if request_key is not None and not (1 <= len(request_key) <= 128):
        raise ValidationFailed("Idempotency-Key must be 1-128 characters.")
    fp = fsm.fingerprint(job_type, str(project_id) if project_id else None, payload)

    if request_key:  # mechanism 1: replay of the same HTTP request
        existing = repo.get_by_request_key(db, user.id, request_key)
        if existing is not None:
            if existing.input_fingerprint != fp or existing.type != job_type:
                raise Conflict(
                    "This Idempotency-Key was already used with different parameters.", code="idempotency_key_reuse"
                )
            return existing, False

    job = Job(
        owner_id=user.id,
        project_id=project_id,
        type=job_type,
        input=payload,
        input_fingerprint=fp,
        request_key=request_key,
        max_attempts=spec["max_attempts"],
        rerun_of_job_id=rerun_of,
    )
    try:
        with db.begin_nested():  # savepoint so a unique violation does not poison the outer transaction
            repo.add(db, job)
    except IntegrityError:
        db.rollback()
        # Either mechanism-2 (identical job in flight) or a racing mechanism-1 replay.
        if request_key:
            raced = repo.get_by_request_key(db, user.id, request_key)
            if raced is not None and raced.input_fingerprint == fp:
                return raced, False
        active = repo.get_active_by_fingerprint(db, user.id, job_type, project_id, fp)
        raise Conflict(
            "An identical job is already queued or running.",
            code="job_already_active",
            details=[{"job_id": str(active.id) if active else None}],
        ) from None
    audit_service.record(
        db,
        action="job.enqueued",
        entity_type="job",
        entity_id=job.id,
        actor_id=user.id,
        project_id=project_id,
        after={"type": job_type, "rerun": rerun_of is not None},
    )
    db.commit()
    _dispatch(db, dispatch, job)
    return job, True


def _dispatch(db: Session, dispatch: Dispatcher, job: Job) -> None:
    """Send AFTER commit. If the broker is down the job stays 'queued' and the sweeper re-sends it."""
    try:
        dispatch(TASK_FOR_TYPE[job.type], str(job.id))
        job.enqueued_at = _now()
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("job dispatch failed; will be re-sent by sweeper", extra={"job_id": str(job.id)})


def get(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = repo.get_for_owner(db, job_id, user.id)
    if job is None:
        raise NotFound("Job not found.")
    return job


def request_cancel(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = get(db, user, job_id)
    if not fsm.JOB_TYPES[job.type]["cancellable"]:
        raise Conflict("This job type cannot be cancelled safely.", code="not_cancellable")
    if job.status == fsm.QUEUED:
        repo.finish(db, job.id, fsm.QUEUED, fsm.CANCELLED)
    elif job.status == fsm.RUNNING:
        job.cancel_requested = True  # cooperative: the worker checks between steps
    else:
        raise Conflict(f"Job is already {job.status}.", code="not_cancellable")
    audit_service.record(
        db,
        action="job.cancel_requested",
        entity_type="job",
        entity_id=job.id,
        actor_id=user.id,
        project_id=job.project_id,
    )
    db.commit()
    db.refresh(job)
    return job


def sweep(db: Session, settings: Settings, dispatch: Dispatcher, now: datetime | None = None) -> dict[str, int]:
    """Recover from worker crashes and lost broker messages. Safe to run concurrently (SKIP LOCKED + CAS claim)."""
    now = now or _now()
    policy = fsm.RecoveryPolicy(
        timedelta(seconds=settings.job_stale_after_s), timedelta(seconds=settings.job_requeue_queued_after_s)
    )
    requeued = failed = redispatched = 0
    for job in repo.stale_running(db, now - policy.stale_after):
        outcome = fsm.worker_loss_outcome(job.attempts, job.max_attempts)
        fsm.assert_transition(fsm.RUNNING, outcome)
        job.status = outcome
        if outcome == fsm.FAILED:
            job.finished_at, job.error_summary = now, "Worker lost; retry attempts exhausted."
            failed += 1
        else:
            requeued += 1
        audit_service.record(
            db,
            action=f"job.recovered_{outcome}",
            entity_type="job",
            entity_id=job.id,
            actor_id=None,
            project_id=job.project_id,
            after={"attempts": job.attempts},
        )
    db.commit()
    for job in repo.queued_needing_dispatch(db, now - policy.requeue_queued_after):
        try:
            dispatch(TASK_FOR_TYPE[job.type], str(job.id))
            job.enqueued_at = now
            redispatched += 1
        except Exception:  # noqa: BLE001
            log.warning("sweep dispatch failed", extra={"job_id": str(job.id)})
    db.commit()
    return {"requeued_after_worker_loss": requeued, "failed_after_worker_loss": failed, "redispatched": redispatched}
