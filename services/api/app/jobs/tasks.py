"""Celery tasks: thin wrappers that call services. Business logic does not live here."""

from __future__ import annotations

import logging
import time
import uuid

from app.core.config import get_settings
from app.core.context import new_correlation_id, reset_correlation_id, set_correlation_id
from app.core.logging import configure_logging
from app.db.session import get_sessionmaker
from app.jobs import fsm
from app.jobs.celery_app import celery_app, send_task
from app.repositories import jobs as repo
from app.services import job_service

log = logging.getLogger(__name__)
MAX_PING_SLEEP_S = 60


@celery_app.task(name="system.ping")
def system_ping(job_id: str) -> str:
    """Round-trip proof that queue -> worker -> database works. Optional input.delay_s lets a human kill the
    worker mid-job to observe recovery (see README 'Manual verification')."""
    configure_logging(get_settings().log_level)
    token = set_correlation_id(new_correlation_id())
    try:
        jid = uuid.UUID(job_id)
        with get_sessionmaker()() as db:
            if not repo.claim(db, jid):
                db.rollback()
                log.info("duplicate/late delivery ignored", extra={"job_id": job_id})
                return "ignored"
            db.commit()
            job = repo.get_by_id(db, jid)
            if job is None:  # claimed row vanished (deleted between claim and read): nothing to do
                log.warning("claimed job no longer exists", extra={"job_id": job_id})
                return "ignored"
            delay = min(int((job.input or {}).get("delay_s", 0)), MAX_PING_SLEEP_S)
            for i in range(delay):
                time.sleep(1)
                db.refresh(job)
                if job.cancel_requested:
                    repo.finish(db, jid, fsm.RUNNING, fsm.CANCELLED)
                    db.commit()
                    return "cancelled"
                repo.set_progress(db, jid, int(90 * (i + 1) / max(delay, 1)), f"waiting {i + 1}/{delay}s")
                db.commit()
            repo.finish(db, jid, fsm.RUNNING, fsm.SUCCEEDED, result={"pong": True})
            db.commit()
        return "ok"
    finally:
        reset_correlation_id(token)


@celery_app.task(name="system.sweep_jobs")
def sweep_jobs() -> dict[str, int]:
    configure_logging(get_settings().log_level)
    with get_sessionmaker()() as db:
        return job_service.sweep(db, get_settings(), send_task)
