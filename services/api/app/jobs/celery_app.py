"""Celery application. Imported by the API only to *send* tasks; imported by the worker to run them."""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import task_postrun, task_prerun

from app.core.config import get_settings
from app.core.context import reset_actor_process, set_actor_process

_s = get_settings()
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
    beat_schedule={
        "sweep-jobs": {"task": "system.sweep_jobs", "schedule": _s.job_sweep_interval_s},
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


def send_task(task_name: str, job_id: str) -> None:
    celery_app.send_task(task_name, args=[job_id], retry=False)
