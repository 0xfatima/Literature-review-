from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs import fsm


def get_for_owner(db: Session, job_id: uuid.UUID, owner_id: uuid.UUID) -> Job | None:
    return db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))


def get_by_id(db: Session, job_id: uuid.UUID) -> Job | None:  # internal (worker) use only
    return db.get(Job, job_id)


def get_by_request_key(db: Session, owner_id: uuid.UUID, request_key: str) -> Job | None:
    return db.scalar(select(Job).where(Job.owner_id == owner_id, Job.request_key == request_key))


def get_active_by_fingerprint(db: Session, owner_id: uuid.UUID, job_type: str, project_id: uuid.UUID | None,
                              fp: str) -> Job | None:
    q = select(Job).where(Job.owner_id == owner_id, Job.type == job_type, Job.input_fingerprint == fp,
                          Job.status.in_(fsm.ACTIVE_STATUSES))
    q = q.where(Job.project_id.is_(None)) if project_id is None else q.where(Job.project_id == project_id)
    return db.scalar(q)


def add(db: Session, job: Job) -> Job:
    db.add(job)
    db.flush()
    return job


def claim(db: Session, job_id: uuid.UUID) -> bool:
    """Compare-and-set queued -> running. Exactly one concurrent/duplicate delivery wins."""
    res = db.execute(update(Job).where(Job.id == job_id, Job.status == fsm.QUEUED).values(
        status=fsm.RUNNING, attempts=Job.attempts + 1, started_at=func.coalesce(Job.started_at, func.now()),
        heartbeat_at=func.now()))
    return res.rowcount == 1


def set_progress(db: Session, job_id: uuid.UUID, progress: int, message: str | None) -> bool:
    res = db.execute(update(Job).where(Job.id == job_id, Job.status == fsm.RUNNING)
                     .values(progress=progress, progress_message=message, heartbeat_at=func.now()))
    return res.rowcount == 1


def finish(db: Session, job_id: uuid.UUID, from_status: str, to_status: str, *, result: dict[str, Any] | None = None,
           error: str | None = None) -> bool:
    fsm.assert_transition(from_status, to_status)
    vals: dict[str, Any] = dict(status=to_status, finished_at=func.now(), result=result, error_summary=error)
    if to_status == fsm.SUCCEEDED:
        vals["progress"] = 100
    res = db.execute(update(Job).where(Job.id == job_id, Job.status == from_status).values(**vals))
    return res.rowcount == 1


def stale_running(db: Session, older_than: datetime) -> list[Job]:
    return list(db.scalars(select(Job).where(
        Job.status == fsm.RUNNING,
        func.coalesce(Job.heartbeat_at, Job.started_at) < older_than).with_for_update(skip_locked=True)))


def queued_needing_dispatch(db: Session, older_than: datetime) -> list[Job]:
    return list(db.scalars(select(Job).where(
        Job.status == fsm.QUEUED,
        func.coalesce(Job.enqueued_at, Job.created_at) < older_than).with_for_update(skip_locked=True)))
