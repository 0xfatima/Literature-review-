"""Phase 15: fair admission (backpressure), cross-process provider limits on real Redis, the deployment-wide token
budget, the metrics endpoint, and several users working concurrently without seeing each other's data."""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime

import redis

from app.core.config import get_settings
from app.core.distributed import RedisSemaphore
from app.db.models import ModelRun
from app.jobs import fsm
from app.providers.scholarly.throttle import RedisThrottle
from app.repositories import jobs as repo
from app.services import job_service
from app.services.llm_jobs import token_guard

from .conftest import CSRF, PASSWORD


def ping(c, n: int):
    r = c.post("/api/v1/jobs/ping", json={"delay_s": n})  # distinct inputs: not de-duplicated as identical work
    assert r.status_code == 202, r.text
    return r.json()


def test_fair_admission_holds_back_a_heavy_user_without_starving_others(user_client, dispatcher, set_settings, db):
    s = set_settings(job_max_inflight_per_user=2, job_max_inflight_global=3)
    a, _, _ = user_client()
    b, _, _ = user_client()
    a_jobs = [ping(a, n) for n in range(1, 6)]
    b_job = ping(b, 1)
    sent = [jid for _, jid in dispatcher.sent]
    assert [j["id"] in sent for j in a_jobs] == [True, True, False, False, False]  # per-user limit
    assert b_job["id"] in sent and len(sent) == 3  # B is not starved by A's backlog; global limit
    third = a.get(f"/api/v1/jobs/{a_jobs[2]['id']}").json()
    assert third["status"] == "queued" and third["waiting_for_capacity"] is True
    assert a.get(f"/api/v1/jobs/{a_jobs[0]['id']}").json()["waiting_for_capacity"] is False

    # one of A's jobs finishes -> the pump admits A's next job, oldest first
    jid = uuid.UUID(a_jobs[0]["id"])
    assert repo.claim(db, jid)
    assert repo.finish(db, jid, fsm.RUNNING, fsm.SUCCEEDED)
    db.commit()
    assert job_service.pump(db, s, dispatcher) == 1
    assert dispatcher.sent[-1][1] == a_jobs[2]["id"]
    assert job_service.pump(db, s, dispatcher) == 0  # limits hold: nothing else fits


def test_round_robin_interleaves_users_when_capacity_frees(user_client, dispatcher, set_settings, db):
    """Fair share: the least-recently-served user goes first. B submits after ALL of A's jobs, yet B's job starts as
    soon as capacity frees, ahead of A's older backlog (with FIFO it would wait behind every one of them)."""
    s = set_settings(job_max_inflight_per_user=1, job_max_inflight_global=1)
    a, _, _ = user_client()
    b, _, _ = user_client()
    first = ping(a, 1)
    a2, a3 = ping(a, 2), ping(a, 3)
    b1 = ping(b, 1)
    for _ in range(3):
        running = uuid.UUID(dispatcher.sent[-1][1])
        assert repo.claim(db, running) and repo.finish(db, running, fsm.RUNNING, fsm.SUCCEEDED)
        db.commit()
        job_service.pump(db, s, dispatcher)
    assert [j for _, j in dispatcher.sent] == [first["id"], b1["id"], a2["id"], a3["id"]]


def test_broker_outage_leaves_jobs_waiting_and_the_pump_admits_them_later(user_client, dispatcher, db):
    c, _, _ = user_client()
    dispatcher.fail = True
    j = ping(c, 3)
    assert dispatcher.sent == [] and c.get(f"/api/v1/jobs/{j['id']}").json()["waiting_for_capacity"] is True
    dispatcher.fail = False
    assert job_service.pump(db, get_settings(), dispatcher) == 1
    assert dispatcher.sent == [("system.ping", j["id"])]


def _redis():
    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def test_redis_semaphore_bounds_concurrency_across_threads_and_frees_crashed_leases():
    name = f"test-{uuid.uuid4().hex}"
    sem = RedisSemaphore(_redis(), name, 2, poll_s=0.01)
    lock, now, peak = threading.Lock(), [0], [0]

    def work():
        assert sem.acquire(timeout=10)
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        time.sleep(0.05)
        with lock:
            now[0] -= 1
        sem.release()

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 2 and sem.in_use() == 0

    crashed = RedisSemaphore(_redis(), name + "-lease", 1, lease_s=0.3, poll_s=0.01)
    assert crashed.acquire(timeout=1)  # never released: the holder "crashed"
    assert not crashed.acquire(timeout=0.1)
    time.sleep(0.35)
    assert crashed.acquire(timeout=1)  # the lease expired


def test_a_429_cooldown_is_shared_by_every_process():
    r = _redis()
    key = f"test-{uuid.uuid4().hex}"
    RedisThrottle(r).penalize(key, 1.5)  # one worker was told "Retry-After: 1.5"
    slept: list[float] = []
    RedisThrottle(r, sleep=lambda s: (slept.append(s), time.sleep(s))).wait(key, 0.01)  # another worker
    assert slept and 1.0 <= sum(slept) <= 2.0


def test_deployment_wide_token_budget_stops_ai_calls(user_client, db, monkeypatch):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "Budget", "topic": "t"}).json()["id"]
    monkeypatch.setattr(get_settings(), "llm_global_daily_token_budget", 1000)
    db.add(
        ModelRun(
            project_id=uuid.UUID(pid),
            purpose="relevance",
            provider="p",
            model="m",
            prompt_version="v",
            data_class="public_metadata",
            status="succeeded",
            input_tokens=900,
            output_tokens=200,
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    guard = token_guard(db, uuid.UUID(pid), 0)  # no per-project budget: only the global one applies
    assert guard is not None and "service-wide" in (guard() or "")


def test_metrics_endpoint_requires_its_token_and_reports_jobs(user_client, dispatcher, set_settings):
    set_settings(metrics_token="m-token")
    c, _, _ = user_client()
    ping(c, 2)
    assert c.get("/internal/metrics").status_code == 404
    assert c.get("/internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    r = c.get("/internal/metrics", headers={"Authorization": "Bearer m-token"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert 'srs_jobs_active{type="system.ping",state="admitted"}' in r.text
    assert 'route="/api/v1/jobs/ping"' in r.text and "srs_llm_tokens_24h" in r.text
    assert "project_id=" not in r.text and "@example.test" not in r.text


def test_many_users_work_concurrently_without_seeing_each_others_data(app, db):
    from fastapi.testclient import TestClient

    n_users, n_projects = 6, 4
    clients, errors, created = [], [], {}

    for i in range(n_users):
        c = TestClient(app, headers=CSRF, base_url="http://localhost:8000")
        email = f"conc-{i}-{uuid.uuid4().hex[:8]}@example.test"
        assert c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}).status_code == 201
        assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
        clients.append(c)

    def user(i: int) -> None:
        try:
            c = clients[i]
            mine = []
            for k in range(n_projects):
                r = c.post("/api/v1/projects", json={"title": f"u{i}-p{k}", "topic": f"topic {i}"})
                assert r.status_code == 201, r.text
                mine.append(r.json()["id"])
                assert (
                    c.patch(
                        f"/api/v1/projects/{mine[-1]}", json={"expected_version": 1, "title": f"u{i}-p{k}!"}
                    ).status_code
                    == 200
                )
            created[i] = mine
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=user, args=(i,)) for i in range(n_users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[0]
    for i, c in enumerate(clients):
        items = c.get("/api/v1/projects?limit=100").json()["items"]
        assert {p["id"] for p in items} == set(created[i])  # exactly my projects
        assert all(p["title"].startswith(f"u{i}-") and p["title"].endswith("!") for p in items)
        for j, other in created.items():
            if j != i:
                assert all(c.get(f"/api/v1/projects/{pid}").status_code == 404 for pid in other)

    # Two simultaneous edits of the same project: optimistic concurrency lets exactly one win, no lost update.
    c = clients[0]
    pid = created[0][0]
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def rename(title: str) -> None:
        barrier.wait()
        codes.append(c.patch(f"/api/v1/projects/{pid}", json={"expected_version": 2, "title": title}).status_code)

    ts = [threading.Thread(target=rename, args=(t,)) for t in ("left", "right")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes) == [200, 409]
    assert c.get(f"/api/v1/projects/{pid}").json()["row_version"] == 3
