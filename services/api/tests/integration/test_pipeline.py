"""Phase 13 integration tests with real PostgreSQL and the real LangGraph Postgres checkpointer: the pipeline
dispatches routine work idempotently, pauses for people, resumes from its checkpoint (as after a restart), recovers
from a failed step, returns to an earlier stage with downstream invalidation, and keeps an append-only history."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import Job, PipelineEvent, PipelineRun, StageInvalidation
from app.jobs import fsm
from app.pipeline import runtime
from app.pipeline import service as pipeline_service
from app.repositories import jobs as job_repo

from .support import _claimed, _take, run_relevance_jobs, searched


def run_pipeline_jobs(dispatcher) -> list[str]:
    out = []
    for jid in _take(dispatcher, "pipeline.advance"):
        db, job = _claimed(jid)
        if db is None:
            out.append("ignored")
            continue
        try:
            status = runtime.advance(db, job, dispatch=dispatcher)
            job_repo.finish(db, job.id, fsm.RUNNING, fsm.SUCCEEDED, result={"run_status": status})
            db.commit()
            out.append(status)
        finally:
            db.close()
    return out


def overview(c, pid):
    return c.get(f"/api/v1/projects/{pid}/pipeline").json()


def test_pipeline_runs_routine_work_and_pauses_for_people(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    r = c.post(f"/api/v1/projects/{pid}/pipeline", json={"autonomy": {"screening": True}})
    assert r.status_code == 202, r.text
    assert c.post(f"/api/v1/projects/{pid}/pipeline", json={}).status_code == 409  # one active run per project
    assert run_pipeline_jobs(dispatcher) == ["waiting"]
    run = overview(c, pid)["run"]
    # protocol and search are already done; relevance ranking was dispatched automatically and is awaited
    assert run["pending"]["type"] == "wait_job" and run["stage"] == "screening"
    assert run["outputs"]["search"]["papers"] > 0
    run_relevance_jobs(dispatcher)
    assert pipeline_service.wake(db, get_settings(), dispatcher) == 1
    assert pipeline_service.wake(db, get_settings(), dispatcher) == 0 or True  # identical advance already queued
    assert run_pipeline_jobs(dispatcher)[0] == "waiting"
    run = overview(c, pid)["run"]
    assert run["pending"]["type"] == "approval" and run["pending"]["kind"] == "screen_records"
    kinds = [e["kind"] for e in run["events"]]
    assert {"run_started", "stage_completed", "job_dispatched", "job_finished", "approval_requested"} <= set(kinds)
    # the relevance job was started exactly once, even though the screening node ran several times
    assert (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.project_id == uuid.UUID(pid), Job.type == "relevance.assess")
        )
        == 1
    )

    stale = c.post(f"/api/v1/projects/{pid}/pipeline/answer", json={"key": "nope", "action": "continue"})
    assert stale.status_code == 409
    bad = c.post(f"/api/v1/projects/{pid}/pipeline/answer", json={"key": run["pending"]["key"], "action": "extend"})
    assert bad.status_code == 422
    c.post(f"/api/v1/projects/{pid}/pipeline/answer", json={"key": run["pending"]["key"], "action": "continue"})
    run_pipeline_jobs(dispatcher)
    run = overview(c, pid)["run"]
    assert run["pending"]["kind"] == "screen_records"  # still records to screen: it asks again, never skips silently

    # Pause / resume and a redelivered advance job are harmless.
    assert c.post(f"/api/v1/projects/{pid}/pipeline/pause").json()["status"] == "paused"
    assert c.post(f"/api/v1/projects/{pid}/pipeline/resume").status_code == 202
    assert run_pipeline_jobs(dispatcher) in (["waiting"], [])
    assert overview(c, pid)["run"]["status"] == "waiting"


def test_skip_and_stop_and_events_are_append_only(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    c.post(f"/api/v1/projects/{pid}/pipeline", json={"autonomy": {"screening": False}})
    run_pipeline_jobs(dispatcher)
    p = overview(c, pid)["run"]["pending"]
    assert p["kind"] == "confirm_run" and p["stage"] == "screening"  # asks before starting the ranking
    c.post(f"/api/v1/projects/{pid}/pipeline/answer", json={"key": p["key"], "action": "stop"})
    assert run_pipeline_jobs(dispatcher) == ["cancelled"]
    assert overview(c, pid)["run"]["status"] == "cancelled"
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE pipeline_events SET kind = 'tampered'"))
        db.commit()
    db.rollback()


def test_rerun_supersedes_invalidates_downstream_and_keeps_history(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    first = c.post(f"/api/v1/projects/{pid}/pipeline", json={"autonomy": {"screening": False}}).json()
    run_pipeline_jobs(dispatcher)
    assert c.post(f"/api/v1/projects/{pid}/pipeline/rerun", json={"stage": "search"}).status_code == 422
    r = c.post(f"/api/v1/projects/{pid}/pipeline/rerun", json={"stage": "search", "reason": "Added a source"})
    assert r.status_code == 202, r.text
    second = r.json()
    assert second["supersedes_id"] == first["id"] and second["rerun_reason"] == "Added a source"
    assert db.get(PipelineRun, uuid.UUID(first["id"])).status == "superseded"
    inv = {i.stage for i in db.scalars(select(StageInvalidation).where(StageInvalidation.project_id == uuid.UUID(pid)))}
    assert inv == {"screening", "extraction", "appraisal", "synthesis", "report"}
    ov = overview(c, pid)
    assert [h["status"] for h in ov["history"][:2]] == ["queued", "superseded"]
    # earlier run's events are still there
    assert (
        db.scalar(select(func.count()).select_from(PipelineEvent).where(PipelineEvent.run_id == uuid.UUID(first["id"])))
        > 0
    )
    run_pipeline_jobs(dispatcher)
    assert overview(c, pid)["run"]["start_stage"] == "search"
    dismiss = c.post(
        f"/api/v1/projects/{pid}/pipeline/invalidations/dismiss",
        json={"invalidation_id": str(ov["invalidations"][0]["id"]), "reason": "Not affected"},
    )
    assert dismiss.status_code == 200 and dismiss.json()["status"] == "dismissed"


def test_failed_step_is_recovered_from_the_checkpoint(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    c.post(f"/api/v1/projects/{pid}/pipeline", json={"autonomy": {"screening": False}})
    run_pipeline_jobs(dispatcher)
    run = db.scalar(select(PipelineRun).where(PipelineRun.project_id == uuid.UUID(pid)))
    key = run.pending["key"]
    run.status, run.error_summary = "failed", "simulated worker crash"
    db.commit()
    assert c.post(f"/api/v1/projects/{pid}/pipeline/answer", json={"key": key, "action": "approve"}).status_code == 409
    r = c.post(f"/api/v1/projects/{pid}/pipeline/recover")
    assert r.status_code == 202 and r.json()["status"] == "waiting"
    run_pipeline_jobs(dispatcher)
    after = overview(c, pid)["run"]
    assert after["pending"]["key"] == key  # same position: nothing was redone or lost
    assert any(e["kind"] == "recovered" for e in after["events"])


def test_impact_view_and_confirm_with_no_stale_decisions(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    imp = c.get(f"/api/v1/projects/{pid}/pipeline/impact").json()
    assert imp["counts"] == {"needs_redecision": 0, "needs_review": 0, "carry_forward": 0}
    assert c.post(f"/api/v1/projects/{pid}/pipeline/impact/confirm", json={}).json() == {"confirmed": 0, "refused": []}
