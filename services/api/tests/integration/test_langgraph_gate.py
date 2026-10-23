"""ADR A7 gate: LangGraph checkpoint / interrupt / resume with a REAL Celery worker process, a real Redis broker and
the real Postgres checkpointer. Nothing about the graph runtime is mocked.

Each test starts its own worker subprocess on a dedicated Redis database, so a killed worker cannot leave messages
for other tests. The API side sends tasks to the same broker database.
"""

import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis
from celery import Celery
from sqlalchemy import func, select, update

from app.api.deps import get_dispatcher, get_rate_limiter
from app.core.config import get_settings
from app.core.rate_limit import InMemoryRateLimiter
from app.db.models import Job, Message, WorkflowEvent, WorkflowRun
from app.db.session import get_engine, get_sessionmaker
from app.main import create_app
from app.services import job_service

from .conftest import API_DIR, CSRF, PASSWORD

GATE_DB = 9
TIMEOUT_S = 90


def _broker_url() -> str:
    base = os.environ.get("REDIS_URL", "redis://localhost:6379/0").rsplit("/", 1)[0]
    return f"{base}/{GATE_DB}"


@pytest.fixture(autouse=True)
def no_foreign_jobs():
    """The recovery sweep is global. Jobs left in flight by earlier tests (e.g. a 41 s ping) would be re-sent to
    this test's single worker and delay it, so each gate test starts with none in flight."""
    with get_sessionmaker()() as db:
        db.execute(update(Job).where(Job.status.in_(("queued", "running"))).values(status="cancelled"))
        db.commit()


@pytest.fixture
def broker():
    r = redis.Redis.from_url(_broker_url())
    r.flushdb()
    producer = Celery("gate", broker=_broker_url())

    def send(task: str, job_id: str) -> None:
        producer.send_task(task, args=[job_id], retry=False)

    yield send
    r.flushdb()


@pytest.fixture
def workers(tmp_path):
    procs: list[subprocess.Popen] = []

    def start(**env_extra: str) -> subprocess.Popen:
        env = {
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
            "REDIS_URL": _broker_url(),
            **env_extra,
        }
        log = open(tmp_path / f"worker-{len(procs)}.log", "w")  # noqa: SIM115
        p = subprocess.Popen(  # noqa: S603  fixed argv, no shell
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.jobs.celery_app",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--without-gossip",
                "--without-mingle",
                "--loglevel=INFO",
                "-n",
                f"gate-{uuid.uuid4().hex[:8]}@%h",
            ],
            cwd=API_DIR,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        procs.append(p)
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait(10)


def gate_client(broker):
    """A fresh app instance (as after an API restart) using the real broker, signed in as a new user."""
    app = create_app()
    limiter = InMemoryRateLimiter()  # as in the other integration tests: no shared Redis rate-limit state
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_dispatcher] = lambda: broker
    from fastapi.testclient import TestClient

    c = TestClient(app, headers=CSRF, base_url="http://localhost:8000")
    email = f"gate-{uuid.uuid4().hex[:10]}@example.test"
    assert c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}).status_code == 201
    assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return c, email


def relogin(broker, email):
    app = create_app()
    limiter = InMemoryRateLimiter()  # as in the other integration tests: no shared Redis rate-limit state
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_dispatcher] = lambda: broker
    from fastapi.testclient import TestClient

    c = TestClient(app, headers=CSRF, base_url="http://localhost:8000")
    assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return c


def wait_run(pid: str, pred, what: str) -> WorkflowRun:
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        with get_sessionmaker()() as db:
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.project_id == uuid.UUID(pid)))
            if run is not None and pred(run):
                return run
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for: {what}")


def awaiting(kind: str):
    return lambda r: r.status == "awaiting_input" and r.pending_question["kind"] == kind


def new_project(c) -> str:
    r = c.post("/api/v1/projects", json={"title": "Gate", "topic": "Effect of exercise on mood"})
    assert r.status_code == 201
    return r.json()["id"]


def answer(c, pid, run: WorkflowRun, action: str, values: dict | None = None):
    d = c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]
    return c.post(
        f"/api/v1/projects/{pid}/workflow/resume",
        json={
            "question_id": str(run.pending_question_id),
            "action": action,
            "values": values or {},
            "expected_row_version": d["row_version"],
        },
    )


def counts(run_id: uuid.UUID, conversation_id: uuid.UUID) -> dict[str, int]:
    with get_sessionmaker()() as db:

        def ev(kind):
            return db.scalar(
                select(func.count())
                .select_from(WorkflowEvent)
                .where(WorkflowEvent.run_id == run_id, WorkflowEvent.kind == kind)
            )

        def msgs(role):
            return db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id, Message.role == role)
            )

        return {
            "questions": ev("question"),
            "answers": ev("answer"),
            "assistant": msgs("assistant"),
            "user": msgs("user"),
        }


# ---------- A7.1 ----------


def test_gate1_interrupt_is_persisted_and_the_worker_is_released(broker, workers):
    workers()
    c, _ = gate_client(broker)
    pid = new_project(c)
    assert c.post(f"/api/v1/projects/{pid}/workflow").status_code == 202
    run = wait_run(pid, awaiting("objective"), "first question")
    q = run.pending_question
    assert {"id", "kind", "step", "stage", "prompt", "actions", "protocol_version_id"} <= set(q)
    assert q["id"] == str(run.pending_question_id) and q["step"] == 0
    with get_sessionmaker()() as db:
        job = db.scalar(select(Job).where(Job.workflow_run_id == run.id))
        assert job.status == "succeeded" and job.finished_at is not None  # no worker is held while a human thinks
        assert job.result == {"outcome": "awaiting_input"}


# ---------- A7.2 ----------


def test_gate2_full_restart_then_resume_continues_from_checkpoint_exactly_once(broker, workers):
    w = workers()
    c, email = gate_client(broker)
    pid = new_project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")
    run = wait_run(pid, awaiting("objective"), "first question")

    # "Restart" API and worker: stop the worker, drop every pooled connection and app object.
    w.send_signal(signal.SIGTERM)
    w.wait(30)
    del c
    get_engine().dispose()
    workers()
    c = relogin(broker, email)

    assert answer(c, pid, run, "save", {"objective": "Measure the effect on mood."}).status_code == 202
    run2 = wait_run(pid, awaiting("framework"), "second question")
    assert run2.pending_question["step"] == 1
    assert counts(run.id, run.conversation_id) == {"questions": 2, "answers": 1, "assistant": 2, "user": 1}
    assert c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]["content"]["objective"] == (
        "Measure the effect on mood."
    )


# ---------- A7.3 ----------


def test_gate3_worker_sigkill_mid_node_is_recovered_without_duplicate_side_effects(broker, workers):
    slow = workers(WORKFLOW_DEBUG_NODE_DELAY_S="5")
    c, _ = gate_client(broker)
    pid = new_project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")
    run = wait_run(pid, awaiting("objective"), "first question")
    assert answer(c, pid, run, "save", {"objective": "Kept exactly once"}).status_code == 202
    wait_run(pid, lambda r: r.status == "running", "worker inside the slow node")
    time.sleep(1.0)
    slow.send_signal(signal.SIGKILL)  # mid-node: no chance to clean up
    slow.wait(10)

    with get_sessionmaker()() as db:
        job = db.scalar(select(Job).where(Job.workflow_run_id == run.id).order_by(Job.created_at.desc()))
        assert job.status == "running" and job.attempts == 1
        jid = job.id
    workers()  # a healthy worker comes back
    later = datetime.now(UTC) + timedelta(seconds=get_settings().job_stale_after_s + 5)
    with get_sessionmaker()() as db:
        out = job_service.sweep(db, get_settings(), broker, now=later)  # heartbeat is stale -> requeue + resend
    assert out["requeued_after_worker_loss"] >= 1  # the sweep is global: earlier tests may leave stale jobs too
    with get_sessionmaker()() as db:
        assert db.get(Job, jid).status in ("queued", "running", "succeeded")  # ours was requeued, not failed

    run2 = wait_run(pid, awaiting("framework"), "question after recovery")
    with get_sessionmaker()() as db:
        job = db.get(Job, jid)
        assert job.status == "succeeded" and job.attempts == 2
    assert counts(run.id, run.conversation_id) == {"questions": 2, "answers": 1, "assistant": 2, "user": 1}
    assert run2.pending_question["step"] == 1
    assert c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]["content"]["objective"] == "Kept exactly once"


# ---------- A7.4 ----------


def test_gate4_duplicate_invalid_and_out_of_turn_resumes(broker, workers):
    workers()
    c, _ = gate_client(broker)
    pid = new_project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")
    run = wait_run(pid, awaiting("objective"), "first question")
    v = c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]["row_version"]
    url = f"/api/v1/projects/{pid}/workflow/resume"

    bad = {
        "question_id": str(run.pending_question_id),
        "action": "save",
        "values": {"objective": "x" * 3000},
        "expected_row_version": v,
    }
    assert c.post(url, json=bad).status_code == 422  # fails validation: rejected...
    assert wait_run(pid, awaiting("objective"), "still waiting").pending_question_id == run.pending_question_id

    good = {**bad, "values": {"objective": "Fine"}}
    assert c.post(url, json=good).status_code == 202
    assert c.post(url, json=good).status_code == 200  # duplicate: no-op, whatever the worker is doing
    nxt = wait_run(pid, awaiting("framework"), "next question")
    assert c.post(url, json=good).status_code == 200  # still a no-op afterwards
    assert counts(run.id, run.conversation_id)["answers"] == 1

    # Out of turn: while the run is queued/running, nothing can be answered.
    assert answer(c, pid, nxt, "skip").status_code == 202
    r = c.post(url, json={"question_id": str(uuid.uuid4()), "action": "skip"})
    assert r.status_code == 409 and r.json()["error"]["code"] in ("not_awaiting_input", "stale_question")


# ---------- A7.5 ----------


def test_gate5_checkpoint_table_disagreement_marks_needs_attention_and_tables_win(broker, workers):
    workers()
    c, _ = gate_client(broker)
    pid = new_project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")
    run = wait_run(pid, awaiting("objective"), "first question")
    with get_sessionmaker()() as db:  # the table now disagrees with the checkpoint
        db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(protocol_version_id=None))
        db.commit()
    assert answer(c, pid, run, "save", {"objective": "From the table"}).status_code == 202
    wait_run(pid, lambda r: r.status == "needs_attention", "needs_attention")
    assert c.post(f"/api/v1/projects/{pid}/workflow/recover").status_code == 202
    after = wait_run(pid, awaiting("review"), "rebuilt from tables")
    assert after.thread_id != run.thread_id
    assert c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]["content"]["objective"] == "From the table"


# ---------- A7.6 ----------


def test_gate6_caps_stop_with_a_reason_and_never_loop(broker, workers):
    workers(WORKFLOW_MAX_ITERATIONS="2")
    c, _ = gate_client(broker)
    pid = new_project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")
    with get_sessionmaker()() as db:  # the API process reads its own settings; the cap is per run
        db.execute(update(WorkflowRun).where(WorkflowRun.project_id == uuid.UUID(pid)).values(max_iterations=2))
        db.commit()
    run = wait_run(pid, awaiting("objective"), "q1")
    assert run.max_iterations == 2
    answer(c, pid, run, "skip")
    run = wait_run(pid, awaiting("framework"), "q2")
    answer(c, pid, run, "skip")
    run = wait_run(pid, lambda r: r.status == "failed", "stopped by the iteration cap")
    assert run.stop_reason == "max_iterations" and run.iteration_count == 2

    # Job attempts exhausted after repeated worker loss -> the job AND the run end failed (visible, no loop).
    with get_sessionmaker()() as db:
        db.execute(update(WorkflowRun).where(WorkflowRun.id == run.id).values(status="running", stop_reason=None))
        job = db.scalar(select(Job).where(Job.workflow_run_id == run.id).order_by(Job.created_at.desc()))
        db.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                status="running",
                attempts=job.max_attempts,
                heartbeat_at=datetime.now(UTC) - timedelta(hours=1),
                finished_at=None,
            )
        )
        db.commit()
        out = job_service.sweep(db, get_settings(), broker)
    assert out["failed_after_worker_loss"] >= 1
    with get_sessionmaker()() as db:
        assert db.get(Job, job.id).status == "failed"
        r = db.get(WorkflowRun, run.id)
        assert r.status == "failed" and r.stop_reason == "worker_lost"
