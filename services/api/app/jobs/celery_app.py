"""Celery application. Imported by the API only to *send* tasks; imported by the worker to run them."""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.signals import task_postrun, task_prerun, worker_shutdown, worker_shutting_down
from kombu import Queue

from app.core import shutdown
from app.core.config import get_settings
from app.core.context import reset_actor_process, set_actor_process

_s = get_settings()
QUEUE_INTERACTIVE, QUEUE_HEAVY, QUEUE_SYSTEM, QUEUE_LEGACY = "interactive", "heavy", "system", "celery"
TASK_QUEUES = {
    "system.ping": QUEUE_INTERACTIVE,
    "workflow.advance": QUEUE_INTERACTIVE,
    "pipeline.advance": QUEUE_INTERACTIVE,
    "search.run": QUEUE_INTERACTIVE,  # mostly waiting on provider spacing; keeps searches responsive
    "relevance.assess": QUEUE_INTERACTIVE,
    "synthesis.draft": QUEUE_INTERACTIVE,
    "document.process": QUEUE_HEAVY,  # CPU-bound PDF parsing / OCR
    "extraction.run": QUEUE_HEAVY,
    "appraisal.suggest": QUEUE_HEAVY,
    "system.sweep_jobs": QUEUE_SYSTEM,
    "system.pump_jobs": QUEUE_SYSTEM,
    "system.purge_documents": QUEUE_SYSTEM,
}
celery_app = Celery("srs", broker=_s.redis_url, include=["app.jobs.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # message is acked only after the task finishes ...
    task_reject_on_worker_lost=True,  # ... so a killed worker's message is redelivered
    worker_prefetch_multiplier=1,
    task_time_limit=_s.job_default_timeout_s,
    broker_connection_retry_on_startup=True,
    # Bound how long the API can block when Redis is down (send path). Connect timeout only: a read timeout would
    # break the worker's long-poll on idle connections.
    broker_transport_options={"socket_connect_timeout": 2},
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Phase 15: three queues so bulk work cannot starve user-facing steps. A worker started without -Q consumes all of
    # them (development); production runs separate pools (docker-compose.prod.yml).
    # "celery" (Celery's default) stays consumed: messages from producers without these routes (an older API
    # process during a rolling deploy, external tools) must never be stranded.
    task_queues=(Queue(QUEUE_INTERACTIVE), Queue(QUEUE_HEAVY), Queue(QUEUE_SYSTEM), Queue(QUEUE_LEGACY)),
    task_default_queue=QUEUE_INTERACTIVE,
    task_routes={name: {"queue": q} for name, q in TASK_QUEUES.items()},
    beat_schedule={
        "sweep-jobs": {"task": "system.sweep_jobs", "schedule": _s.job_sweep_interval_s},
        "pump-jobs": {"task": "system.pump_jobs", "schedule": _s.job_pump_interval_s},
        "purge-documents": {"task": "system.purge_documents", "schedule": _s.document_purge_interval_s},
    },
)


_process_tokens: dict[str, Any] = {}


def _attribute_audit_to_task(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    """Audit events written while a task runs without a user actor are attributed to this worker task (Phase 14)."""
    if task_id is not None:
        _process_tokens[task_id] = set_actor_process(f"worker:{getattr(task, 'name', 'unknown')}")


def _clear_audit_attribution(task_id: str | None = None, **_: Any) -> None:
    token = _process_tokens.pop(task_id, None) if task_id is not None else None
    if token is not None:
        try:
            reset_actor_process(token)
        except ValueError:  # token from another context (should not happen with prefork); never fail a task for it
            set_actor_process(None)


# Connected with plain calls, not decorators: Celery is untyped and strict mypy rejects untyped decorators here.
task_prerun.connect(_attribute_audit_to_task, weak=False)
task_postrun.connect(_clear_audit_attribution, weak=False)


def _admit_after_task(task: Any = None, **_: Any) -> None:
    """A finished job frees capacity: admit waiting jobs now instead of at the next pump tick (Phase 15)."""
    name = getattr(task, "name", "") or ""
    if name.startswith("system."):
        return
    try:
        from app.db.session import get_sessionmaker
        from app.services import job_service

        with get_sessionmaker()() as db:
            job_service.pump(db, get_settings(), send_task, wait_for_lock=False)
    except Exception:  # noqa: BLE001 - the periodic pump will admit them; never fail the finished task for this
        logging.getLogger(__name__).warning("post-task admission pump failed; the periodic pump will retry")


def _on_worker_shutting_down(**_: Any) -> None:
    shutdown.begin_drain()


def _on_worker_shutdown(**_: Any) -> None:
    shutdown.end_drain()


task_postrun.connect(_admit_after_task, weak=False)
worker_shutting_down.connect(_on_worker_shutting_down, weak=False)
worker_shutdown.connect(_on_worker_shutdown, weak=False)


def send_task(task_name: str, job_id: str) -> None:
    celery_app.send_task(task_name, args=[job_id], retry=False, queue=TASK_QUEUES.get(task_name, QUEUE_INTERACTIVE))
