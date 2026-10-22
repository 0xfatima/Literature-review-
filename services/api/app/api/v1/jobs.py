from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher
from app.core.config import Settings, get_settings
from app.db.models import Job, User
from app.db.session import get_db
from app.schemas.jobs import JobOut, PingJobRequest
from app.services import job_service, project_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/ping", response_model=JobOut, status_code=202)
def start_ping(
    body: PingJobRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Callable[[str, str], None] = Depends(get_dispatcher),
) -> Job:
    """System job used to prove the queue/worker path end to end (and to exercise recovery)."""
    if body.project_id is not None:
        project_service.get(db, user, body.project_id)  # ownership check before attaching a job to a project
    job, created = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type="system.ping",
        project_id=body.project_id,
        payload={"delay_s": body.delay_s},
        request_key=idempotency_key,
    )
    if not created:
        response.status_code = 200  # replay of the same Idempotency-Key
    return job


@router.get("/{job_id}", response_model=JobOut)
def read_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Job:
    return job_service.get(db, user, job_id)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Job:
    return job_service.request_cancel(db, user, job_id)
