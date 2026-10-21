"""Celery application. Imported by the API only to *send* tasks; imported by the worker to run them."""
from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

_s = get_settings()
celery_app = Celery("srs", broker=_s.redis_url, include=["app.jobs.tasks"])
celery_app.conf.update(
    task_acks_late=True,                 # message is acked only after the task finishes ...
    task_reject_on_worker_lost=True,     # ... so a killed worker's message is redelivered
    worker_prefetch_multiplier=1,
    task_time_limit=_s.job_default_timeout_s,
    broker_connection_retry_on_startup=True,
    # Bound how long the API can block when Redis is down (send path). Connect timeout only: a read timeout would
    # break the worker's long-poll on idle connections.
    broker_transport_options={"socket_connect_timeout": 2},
    task_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True,
    beat_schedule={"sweep-jobs": {"task": "system.sweep_jobs", "schedule": _s.job_sweep_interval_s}},
)


def send_task(task_name: str, job_id: str) -> None:
    celery_app.send_task(task_name, args=[job_id], retry=False)
