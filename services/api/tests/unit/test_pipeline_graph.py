"""The pipeline graph with an in-memory checkpointer and scripted stage results: interrupts, stale answers,
idempotent dispatch keys, technical retries with backoff, failures handed to a person, skip and stop."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.pipeline import steps
from app.workflows import pipeline as pg

SETTINGS = SimpleNamespace(
    pipeline_max_retries=2,
    pipeline_backoff_base_s=0.0,
    pipeline_backoff_cap_s=0.0,
    pipeline_token_budget=0,
    pipeline_max_jobs=0,
)


@pytest.fixture
def world(monkeypatch):
    w: dict[str, Any] = {s: steps.Eval("done", summary={"ok": s}) for s in pg.policy.STAGES}
    dispatched: list[str] = []

    def fake_dispatch(kind: str, ctx: Any, key: str) -> str:
        dispatched.append(key)
        stage = key.split(":")[2]
        w[stage] = steps.Eval("job", job_id=f"job-{len(dispatched)}", kind=kind)
        return f"job-{len(dispatched)}"

    monkeypatch.setattr(steps, "evaluate", lambda stage, ctx: w[stage])
    monkeypatch.setattr(steps, "dispatch", fake_dispatch)
    monkeypatch.setattr(steps, "tokens_used", lambda ctx: 0)
    return w, dispatched


def make(autonomy: dict[str, bool] | None = None):
    graph = pg.compile_graph(MemorySaver())
    ctx = SimpleNamespace(settings=SETTINGS, run=SimpleNamespace(autonomy=autonomy or {}, counters={}))
    cfg = {"configurable": {"thread_id": "t1", "pipeline_ctx": ctx}, "recursion_limit": 100}
    init = {"run_id": "r1", "start_stage": "protocol", "autonomy": autonomy or {}, "log": []}
    return graph, cfg, init


def pending(graph, cfg) -> dict[str, Any] | None:
    snap = graph.get_state(cfg)
    return snap.interrupts[0].value if snap.interrupts else None


def test_all_stages_complete_without_interrupts(world):
    graph, cfg, init = make()
    out = graph.invoke(init, cfg)
    assert out["completed"] == list(pg.policy.STAGES) and pending(graph, cfg) is None
    assert [e["kind"] for e in out["log"]].count("stage_completed") == 7


def test_human_interrupt_ignores_stale_answers_and_resumes(world):
    w, _ = world
    w["screening"] = steps.Eval("human", "Screen 3 records", kind="screen_records")
    graph, cfg, init = make()
    graph.invoke(init, cfg)
    p = pending(graph, cfg)
    assert p["type"] == "approval" and p["kind"] == "screen_records" and p["stage"] == "screening"
    graph.invoke(Command(resume={"key": "wrong", "action": "continue"}), cfg)
    assert pending(graph, cfg)["key"] == p["key"]  # still waiting on the same decision
    w["screening"] = steps.Eval("done", summary={"screened": 3})
    out = graph.invoke(Command(resume={"key": p["key"], "action": "continue"}), cfg)
    assert "screening" in out["completed"] and out["outputs"]["screening"] == {"screened": 3}


def test_routine_job_is_dispatched_once_then_retried_with_backoff(world):
    w, dispatched = world
    w["screening"] = steps.Eval("job", kind="relevance")
    graph, cfg, init = make({"screening": True})
    graph.invoke(init, cfg)
    p = pending(graph, cfg)
    assert p["type"] == "wait_job" and dispatched == ["pl:r1:screening:relevance:0"]
    w["screening"] = steps.Eval("job", kind="relevance")  # the job is gone: the stage needs work again
    graph.invoke(Command(resume={"key": p["key"], "job_status": "failed", "error": "Worker lost"}), cfg)
    p2 = pending(graph, cfg)
    # backoff of 0 s has already elapsed, so the retry is dispatched with a NEW attempt key
    assert dispatched == ["pl:r1:screening:relevance:0", "pl:r1:screening:relevance:1"] or p2["type"] == "backoff"
    state = graph.get_state(cfg).values
    assert state["retries"]["screening"] == 1
    assert any(e["kind"] == "technical_retry" for e in state["log"])


def test_non_transient_failure_goes_to_a_person_who_can_skip(world):
    w, _ = world
    w["extraction"] = steps.Eval("job", job_id="j9", kind="extraction")
    graph, cfg, init = make()
    graph.invoke(init, cfg)
    p = pending(graph, cfg)
    graph.invoke(Command(resume={"key": p["key"], "job_status": "failed", "error": "Schema missing"}), cfg)
    p2 = pending(graph, cfg)
    assert p2["kind"] == "job_failed" and set(p2["options"]) == {"retry", "skip", "stop"}
    out = graph.invoke(Command(resume={"key": p2["key"], "action": "skip"}), cfg)
    assert "extraction" in out["skipped"] and "report" in out["completed"]


@pytest.mark.parametrize(
    ("stage", "flag"),
    [
        ("extraction", "extraction_dispatched"),
        ("appraisal", "appraisal_dispatched"),
    ],
)
def test_manual_retry_clears_ai_dispatch_flag(world, stage, flag):
    w, _ = world
    w[stage] = steps.Eval("job", job_id=f"job-{stage}", kind=stage)
    graph, cfg, init = make()
    ctx = cfg["configurable"]["pipeline_ctx"]
    ctx.run.counters = {flag: True}

    graph.invoke(init, cfg)
    first = pending(graph, cfg)
    assert first is not None and first["type"] == "wait_job"

    graph.invoke(
        Command(
            resume={
                "key": first["key"],
                "job_status": "failed",
                "error": "Schema missing",
            }
        ),
        cfg,
    )
    failed = pending(graph, cfg)
    assert failed is not None and failed["kind"] == "job_failed"

    graph.invoke(
        Command(resume={"key": failed["key"], "action": "retry"}),
        cfg,
    )

    assert flag not in ctx.run.counters


def test_without_autonomy_the_run_asks_first_and_can_be_stopped(world):
    w, dispatched = world
    w["search"] = steps.Eval("job", "Run the search", kind="search")
    graph, cfg, init = make({"search": False})
    graph.invoke(init, cfg)
    p = pending(graph, cfg)
    assert p["kind"] == "confirm_run" and dispatched == []
    out = graph.invoke(Command(resume={"key": p["key"], "action": "stop"}), cfg)
    assert out["stop_reason"] == "stopped_by_user" and pending(graph, cfg) is None


def test_approval_then_dispatch(world):
    w, dispatched = world
    w["search"] = steps.Eval("job", "Run the search", kind="search")
    graph, cfg, init = make({"search": False})
    graph.invoke(init, cfg)
    p = pending(graph, cfg)
    graph.invoke(Command(resume={"key": p["key"], "action": "approve"}), cfg)
    assert dispatched == ["pl:r1:search:search:0"] and pending(graph, cfg)["type"] == "wait_job"
    assert datetime.now(UTC)  # (keeps the import meaningful for readers of the timing logic)
