import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.config import get_settings
from app.db.models import Job
from app.jobs import fsm
from app.repositories import jobs as repo
from app.services import job_service


def post(c, body=None, key=None):
    return c.post("/api/v1/jobs/ping", json=body or {}, headers={"Idempotency-Key": key} if key else {})


def force(db, job_id, **values):
    db.execute(update(Job).where(Job.id == uuid.UUID(job_id)).values(**values))
    db.commit()


def test_enqueue_persists_job_and_dispatches_once(user_client, dispatcher):
    c, _, _ = user_client()
    r = post(c)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert dispatcher.sent == [("system.ping", r.json()["id"])]


def test_idempotency_key_replay_returns_same_job_and_does_not_redispatch(user_client, dispatcher):
    c, _, _ = user_client()
    a = post(c, key="req-1")
    b = post(c, key="req-1")
    assert a.status_code == 202 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(dispatcher.sent) == 1


def test_idempotency_key_reused_with_different_params_conflicts(user_client):
    c, _, _ = user_client()
    post(c, {"delay_s": 1}, key="req-2")
    r = post(c, {"delay_s": 2}, key="req-2")
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_key_reuse"


def test_identical_job_in_flight_is_rejected_with_pointer_to_existing(user_client):
    c, _, _ = user_client()
    first = post(c, {"delay_s": 5}).json()
    r = post(c, {"delay_s": 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_already_active"
    assert r.json()["error"]["details"][0]["job_id"] == first["id"]


def test_different_parameters_run_concurrently(user_client):
    c, _, _ = user_client()
    assert post(c, {"delay_s": 1}).status_code == 202 and post(c, {"delay_s": 2}).status_code == 202


def test_LEGITIMATE_RERUNS_are_not_blocked_after_any_terminal_state(user_client, db):
    """Regression test for the Phase-0 flaw: a permanent unique(project, type, key) blocked reruns."""
    c, _, _ = user_client()
    for terminal in ("succeeded", "failed", "cancelled"):
        first = post(c, {"delay_s": 7}).json()
        force(db, first["id"], status=terminal)           # job finished (any way)
        again = post(c, {"delay_s": 7})                     # same search, run again
        assert again.status_code == 202 and again.json()["id"] != first["id"], terminal
        force(db, again.json()["id"], status="succeeded")  # tidy so the next loop starts clean


def test_other_users_may_run_identical_work(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    assert post(a, {"delay_s": 3}).status_code == 202 and post(b, {"delay_s": 3}).status_code == 202


def test_claim_is_compare_and_set(user_client, db):
    c, _, _ = user_client()
    jid = uuid.UUID(post(c).json()["id"])
    assert repo.claim(db, jid) is True
    db.commit()
    assert repo.claim(db, jid) is False  # duplicate delivery loses
    db.commit()
    job = repo.get_by_id(db, jid)
    assert job.status == "running" and job.attempts == 1


def test_finish_enforces_state_machine(user_client, db):
    c, _, _ = user_client()
    jid = uuid.UUID(post(c).json()["id"])
    from app.jobs.fsm import InvalidTransition
    import pytest
    with pytest.raises(InvalidTransition):
        repo.finish(db, jid, fsm.QUEUED, fsm.SUCCEEDED)  # cannot skip 'running'


def test_broker_outage_does_not_lose_the_job_and_sweeper_resends(user_client, dispatcher, db):
    c, _, _ = user_client()
    dispatcher.fail = True
    r = post(c, {"delay_s": 9})
    assert r.status_code == 202 and r.json()["status"] == "queued" and dispatcher.sent == []
    dispatcher.fail = False
    future = datetime.now(timezone.utc) + timedelta(seconds=get_settings().job_requeue_queued_after_s + 5)
    out = job_service.sweep(db, get_settings(), dispatcher, now=future)
    assert out["redispatched"] >= 1 and r.json()["id"] in [j for _, j in dispatcher.sent]


def test_worker_crash_recovery_requeues_then_fails_after_max_attempts(user_client, dispatcher, db):
    c, _, _ = user_client()
    jid = post(c, {"delay_s": 11}).json()["id"]
    s = get_settings()
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=s.job_stale_after_s + 30)
    # attempt 1 started, worker died (heartbeat stale)
    force(db, jid, status="running", attempts=1, started_at=long_ago, heartbeat_at=long_ago)
    out = job_service.sweep(db, s, dispatcher)
    assert out["requeued_after_worker_loss"] >= 1
    assert repo.get_by_id(db, uuid.UUID(jid)).status == "queued"
    # attempts exhausted -> visible failure, not an infinite loop
    force(db, jid, status="running", attempts=3, heartbeat_at=long_ago)
    job_service.sweep(db, s, dispatcher)
    j = repo.get_by_id(db, uuid.UUID(jid))
    assert j.status == "failed" and "exhausted" in j.error_summary


def test_healthy_running_job_is_left_alone_by_sweeper(user_client, dispatcher, db):
    c, _, _ = user_client()
    jid = post(c, {"delay_s": 13}).json()["id"]
    force(db, jid, status="running", attempts=1, heartbeat_at=datetime.now(timezone.utc))
    job_service.sweep(db, get_settings(), dispatcher)
    assert repo.get_by_id(db, uuid.UUID(jid)).status == "running"


def test_cancel_queued_and_running(user_client, db):
    c, _, _ = user_client()
    q = post(c, {"delay_s": 21}).json()
    assert c.post(f"/api/v1/jobs/{q['id']}/cancel").json()["status"] == "cancelled"
    r = post(c, {"delay_s": 22}).json()
    force(db, r["id"], status="running")
    assert c.post(f"/api/v1/jobs/{r['id']}/cancel").json()["cancel_requested"] is True
    assert c.post(f"/api/v1/jobs/{q['id']}/cancel").status_code == 409  # already terminal


def test_job_visible_only_to_owner(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    jid = post(a).json()["id"]
    assert a.get(f"/api/v1/jobs/{jid}").status_code == 200
    assert b.get(f"/api/v1/jobs/{jid}").status_code == 404
    assert b.post(f"/api/v1/jobs/{jid}/cancel").status_code == 404
