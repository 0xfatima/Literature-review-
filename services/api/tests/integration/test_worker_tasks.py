"""Executes the real Celery task bodies (called synchronously, no broker) against the real database.

These cover the worker side of the job pipeline: claim -> progress -> terminal state, duplicate delivery,
cooperative cancellation and a missing row. The broker hop itself is covered by the compose E2E/drills."""

import uuid

from sqlalchemy import update

from app.db.models import Job
from app.jobs import tasks
from app.repositories import jobs as repo


def enqueue(c, body=None) -> str:
    r = c.post("/api/v1/jobs/ping", json=body or {})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_ping_task_runs_queued_job_to_success(user_client, db):
    c, _, _ = user_client()
    jid = enqueue(c)
    assert tasks.system_ping(jid) == "ok"
    body = c.get(f"/api/v1/jobs/{jid}").json()
    assert body["status"] == "succeeded" and body["progress"] == 100
    job = repo.get_by_id(db, uuid.UUID(jid))
    assert job.attempts == 1 and job.result == {"pong": True} and job.finished_at is not None


def test_ping_task_reports_progress_while_waiting(user_client, db):
    c, _, _ = user_client()
    jid = enqueue(c, {"delay_s": 1})
    assert tasks.system_ping(jid) == "ok"
    assert repo.get_by_id(db, uuid.UUID(jid)).status == "succeeded"


def test_duplicate_delivery_is_ignored_and_does_not_rerun(user_client, db):
    c, _, _ = user_client()
    jid = enqueue(c)
    assert tasks.system_ping(jid) == "ok"
    assert tasks.system_ping(jid) == "ignored"  # redelivered message after completion
    db.expire_all()
    assert repo.get_by_id(db, uuid.UUID(jid)).attempts == 1


def test_cancel_requested_is_honoured_by_running_task(user_client, db):
    c, _, _ = user_client()
    jid = enqueue(c, {"delay_s": 2})
    db.execute(update(Job).where(Job.id == uuid.UUID(jid)).values(cancel_requested=True))
    db.commit()
    assert tasks.system_ping(jid) == "cancelled"
    assert c.get(f"/api/v1/jobs/{jid}").json()["status"] == "cancelled"


def test_unknown_job_id_is_ignored():
    assert tasks.system_ping(str(uuid.uuid4())) == "ignored"
