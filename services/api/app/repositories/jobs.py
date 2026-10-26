from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.base import affected_rows
from app.db.models import Job
from app.jobs import fsm


def get_for_owner(db: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
    return db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))


def list_for_owner(
    db: Session, owner_id: uuid.UUID, *, project_id: uuid.UUID | None, active_only: bool, limit: int
) -> list[Job]:
    q = select(Job).where(Job.owner_id == owner_id)
    if project_id is not None:
        q = q.where(Job.project_id == project_id)
    if active_only:
        q = q.where(Job.status.notin_(fsm.TERMINAL_STATUSES))  # includes awaiting_input
    return list(db.scalars(q.order_by(Job.created_at.desc(), Job.id).limit(limit)))


def get_by_id(db: Session, job_id: uuid.UUID) -> Job | None:  # internal (worker) use only
    return db.get(Job, job_id)


def get_by_request_key(db: Session, owner_id: uuid.UUID, request_key: str) -> Job | None:
    return db.scalar(select(Job).where(Job.owner_id == owner_id, Job.request_key == request_key))


def get_active_by_fingerprint(
    db: Session, owner_id: uuid.UUID, job_type: str, project_id: uuid.UUID | None, fp: str
) -> Job | None:
    q = select(Job).where(
        Job.owner_id == owner_id, Job.type == job_type, Job.input_fingerprint == fp, Job.status.in_(fsm.ACTIVE_STATUSES)
    )
    q = q.where(Job.project_id.is_(None)) if project_id is None else q.where(Job.project_id == project_id)
    return db.scalar(q)


def add(db: Session, job: Job) -> Job:
    db.add(job)
    db.flush()
    return job


def claim(db: Session, job_id: uuid.UUID) -> bool:
    """Compare-and-set queued -> running. Exactly one concurrent/duplicate delivery wins."""
    res = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == fsm.QUEUED)
        .values(
            status=fsm.RUNNING,
            attempts=Job.attempts + 1,
            started_at=func.coalesce(Job.started_at, func.now()),
            heartbeat_at=func.now(),
        )
    )
    return affected_rows(res) == 1


def set_progress(db: Session, job_id: uuid.UUID, progress: int, message: str | None) -> bool:
    res = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == fsm.RUNNING)
        .values(progress=progress, progress_message=message, heartbeat_at=func.now())
    )
    return affected_rows(res) == 1


def finish(
    db: Session,
    job_id: uuid.UUID,
    from_status: str,
    to_status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    fsm.assert_transition(from_status, to_status)
    vals: dict[str, Any] = dict(status=to_status, finished_at=func.now(), result=result, error_summary=error)
    if to_status == fsm.SUCCEEDED:
        vals["progress"] = 100
    res = db.execute(update(Job).where(Job.id == job_id, Job.status == from_status).values(**vals))
    return affected_rows(res) == 1


def stale_running(db: Session, older_than: datetime) -> list[Job]:
    return list(
        db.scalars(
            select(Job)
            .where(Job.status == fsm.RUNNING, func.coalesce(Job.heartbeat_at, Job.started_at) < older_than)
            .with_for_update(skip_locked=True)
        )
    )


def queued_needing_dispatch(db: Session, older_than: datetime) -> list[Job]:
    """Jobs that WERE handed to the broker (admitted) but never started: the message may be lost, so resend.
    Jobs never admitted (enqueued_at IS NULL) belong to the admission pump, which applies the fairness limits."""
    return list(
        db.scalars(
            select(Job)
            .where(Job.status == fsm.QUEUED, Job.enqueued_at.is_not(None), Job.enqueued_at < older_than)
            .with_for_update(skip_locked=True)
        )
    )


# ---------- Phase 15: fair admission ----------

PUMP_LOCK_KEY = 0x5352_5350  # pg advisory lock: one admission pump at a time (cluster-wide)
# Lower = admitted first. Short, user-facing steps go before bulk PDF parsing and AI extraction.
PRIORITY_SQL = (
    "CASE type WHEN 'document.process' THEN 2 WHEN 'extraction.run' THEN 2 WHEN 'appraisal.suggest' THEN 2 ELSE 1 END"
)
_INFLIGHT = "(status = 'running' OR (status = 'queued' AND enqueued_at IS NOT NULL))"
_INFLIGHT_J = "(j.status = 'running' OR (j.status = 'queued' AND j.enqueued_at IS NOT NULL))"


def lock_pump(db: Session, *, wait: bool) -> bool:
    """Transaction-scoped advisory lock serialising admission, so concurrent pumps cannot overshoot the limits.
    wait=False still waits briefly (a pump holds the lock for milliseconds), so a just-created job is normally
    admitted by its own request rather than at the next pump tick; it gives up after 500 ms."""
    if wait:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": PUMP_LOCK_KEY})
        return True
    if db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": PUMP_LOCK_KEY}):
        return True
    db.execute(text("SET LOCAL lock_timeout = '500ms'"))
    try:
        with db.begin_nested():
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": PUMP_LOCK_KEY})
        db.execute(text("SET LOCAL lock_timeout TO DEFAULT"))  # only the lock wait is bounded
        return True
    except OperationalError:  # lock_timeout: another pump is busy and will (or the next tick will) admit the job
        return False


def admission_candidates(
    db: Session, *, per_user: int, global_cap: int, fair_window_s: float = 600.0
) -> list[tuple[uuid.UUID, str]]:
    """(job id, type) of never-admitted queued jobs that fit the limits, in fair-share order: a job's rank is its
    position among its owner's waiting jobs PLUS how many of that owner's jobs were admitted in the last
    `fair_window_s`, so the least-recently-served user goes first and a heavy user's backlog cannot hold others back.
    Ties: priority (user-facing steps first), then age."""
    rows = db.execute(
        text(
            f"""
            WITH pending AS (
                SELECT id, owner_id, type, created_at, {PRIORITY_SQL} AS prio,
                       row_number() OVER (PARTITION BY owner_id ORDER BY {PRIORITY_SQL}, created_at, id) AS rn
                FROM jobs WHERE status = 'queued' AND enqueued_at IS NULL
            ), owners AS (
                SELECT DISTINCT owner_id FROM pending
            ), inflight AS (
                SELECT j.owner_id, count(*) AS n FROM jobs j JOIN owners o ON o.owner_id = j.owner_id
                WHERE {_INFLIGHT_J}
                GROUP BY j.owner_id
            ), served AS (
                SELECT j.owner_id, count(*) AS n FROM jobs j JOIN owners o ON o.owner_id = j.owner_id
                WHERE j.created_at >= now() - make_interval(secs => :window) AND j.enqueued_at IS NOT NULL
                GROUP BY j.owner_id
            )
            SELECT p.id, p.type FROM pending p
            LEFT JOIN inflight i ON i.owner_id = p.owner_id
            LEFT JOIN served s ON s.owner_id = p.owner_id
            WHERE p.rn <= :per_user - coalesce(i.n, 0)
            ORDER BY p.rn + coalesce(s.n, 0), p.prio, p.created_at, p.id
            LIMIT greatest(:global_cap - (SELECT count(*) FROM jobs WHERE {_INFLIGHT}), 0)
            """  # noqa: S608 - only module constants are interpolated; values are bound parameters
        ),
        {"per_user": per_user, "global_cap": global_cap, "window": float(fair_window_s)},
    ).all()
    return [(r[0], r[1]) for r in rows]


def mark_enqueued(db: Session, job_id: uuid.UUID, at: datetime) -> bool:
    res = db.execute(
        update(Job).where(Job.id == job_id, Job.status == fsm.QUEUED, Job.enqueued_at.is_(None)).values(enqueued_at=at)
    )
    return affected_rows(res) == 1


def waiting_ahead(db: Session, job: Job) -> int:
    """How many never-admitted jobs of the same owner would be admitted before this one (for a queue position)."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.owner_id == job.owner_id,
                Job.status == fsm.QUEUED,
                Job.enqueued_at.is_(None),
                Job.created_at < job.created_at,
            )
        )
        or 0
    )
