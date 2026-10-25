"""The end-to-end review pipeline graph (Phase 13): one small node per stage — protocol planning, search,
screening support, extraction, appraisal, synthesis, reporting.

Each node evaluates its stage through `app.pipeline.steps` (all business logic lives there) and then either
completes, dispatches routine background work (idempotently, then waits for it), or interrupts for a person. A
node issues at most ONE interrupt per execution and loops back to itself, so a resumed node can always re-check
the stored data: every interrupt carries a `key` and an answer for another key is ignored.

Technical retries are bounded with exponential backoff (policy.retry_decision); research reruns are new runs
(see pipeline service). Events are appended to `log` (reducer: concatenation) and written to pipeline_events by the
runtime after each checkpoint.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.pipeline import policy, steps

GRAPH_NAME = "pipeline"
GRAPH_VERSION = "pipeline-v1"


class PipelineState(TypedDict, total=False):
    run_id: str
    project_id: str
    start_stage: str
    stage: str
    autonomy: dict[str, bool]
    approved: dict[str, bool]  # stages a person allowed to start this run
    skipped: list[str]
    completed: list[str]
    outputs: dict[str, Any]
    retries: dict[str, int]  # technical retries per stage (not research reruns)
    not_before: dict[str, str]  # backoff deadlines (ISO)
    failed: dict[str, str]  # last unrecoverable job failure per stage, awaiting a person
    waiting: dict[str, dict[str, Any]]  # job a stage is waiting for
    extra_budget: int
    stop_reason: str | None
    log: Annotated[list[dict[str, Any]], operator.add]
    _route: str


def _now() -> datetime:
    return datetime.now(UTC)


def _next(stage: str) -> str:
    rest = policy.downstream(stage)
    return rest[0] if rest else END


def _ev(kind: str, stage: str, **payload: Any) -> dict[str, Any]:
    return {"kind": kind, "stage": stage, "payload": payload, "at": _now().isoformat()}


def _ctx(config: RunnableConfig) -> steps.Ctx:
    return cast(steps.Ctx, (config.get("configurable") or {})["pipeline_ctx"])


def make_node(stage: str) -> Any:
    def node(state: PipelineState, config: RunnableConfig) -> dict[str, Any]:
        ctx = _ctx(config)
        retries = dict(state.get("retries") or {})
        failed = dict(state.get("failed") or {})
        not_before = dict(state.get("not_before") or {})
        waiting = dict(state.get("waiting") or {})
        approved = dict(state.get("approved") or {})
        n = retries.get(stage, 0)
        ask: dict[str, Any]
        # Branches that depend only on checkpointed state come first, so a resumed node always reaches the same
        # interrupt again and receives its answer.
        if stage in failed:
            ask = {
                "type": "approval",
                "kind": "job_failed",
                "stage": stage,
                "key": f"{stage}:failed:{n}",
                "message": f"{policy.STAGE_LABELS[stage]}: the background job failed — {failed[stage]}",
                "options": ["retry", "skip", "stop"],
            }
        elif waiting.get(stage):
            w = waiting[stage]
            ask = {
                "type": "wait_job",
                "stage": stage,
                "job_id": w["job_id"],
                "key": f"{stage}:job:{w['job_id']}",
                "message": w.get("message") or "Waiting for a background job.",
            }
        elif not_before.get(stage):
            ask = {
                "type": "backoff",
                "stage": stage,
                "until": not_before[stage],
                "key": f"{stage}:backoff:{n}",
                "message": f"Waiting before automatic retry {n} of {ctx.settings.pipeline_max_retries}.",
            }
        else:
            ev = steps.evaluate(stage, ctx)
            if ev.status == "done":
                out = dict(state.get("outputs") or {})
                out[stage] = ev.summary
                return {
                    "stage": stage,
                    "outputs": out,
                    "completed": [*(state.get("completed") or []), stage],
                    "_route": _next(stage),
                    "log": [_ev("stage_completed", stage, summary=ev.summary)],
                }
            if ev.status == "job" and ev.job_id:
                return {
                    "stage": stage,
                    "_route": stage,
                    "waiting": {**waiting, stage: {"job_id": ev.job_id, "message": ev.message}},
                }
            if ev.status == "job":
                over = policy.over_budget(
                    steps.tokens_used(ctx),
                    ctx.settings.pipeline_token_budget + int(state.get("extra_budget") or 0),
                    int((ctx.run.counters or {}).get("jobs_dispatched", 0)),
                    ctx.settings.pipeline_max_jobs,
                )
                allowed = ctx.run.autonomy.get("screening" if ev.kind == "relevance" else stage) or approved.get(stage)
                if over:
                    ask = {
                        "type": "approval",
                        "kind": "budget",
                        "stage": stage,
                        "key": f"{stage}:budget:{n}",
                        "message": over,
                        "options": ["extend", "stop"],
                    }
                elif allowed:
                    job_id = steps.dispatch(ev.kind, ctx, f"pl:{state['run_id']}:{stage}:{ev.kind}:{n}")
                    ctx.run.counters = {
                        **(ctx.run.counters or {}),
                        "jobs_dispatched": int((ctx.run.counters or {}).get("jobs_dispatched", 0)) + 1,
                    }
                    log = [_ev("job_dispatched", stage, job_id=job_id, operation=ev.kind, attempt=n, ai=ev.ai)]
                    if not job_id:  # synchronous operation (report draft) already finished
                        return {"stage": stage, "_route": stage, "log": log}
                    return {
                        "stage": stage,
                        "_route": stage,
                        "log": log,
                        "waiting": {**waiting, stage: {"job_id": job_id, "message": ev.message}},
                    }
                else:
                    ask = {
                        "type": "approval",
                        "kind": "confirm_run",
                        "stage": stage,
                        "key": f"{stage}:confirm:{n}",
                        "message": ev.message,
                        "ai": ev.ai,
                        "options": ["approve", "skip", "stop"],
                    }
            else:
                ask = {
                    "type": "approval",
                    "kind": ev.kind,
                    "stage": stage,
                    "key": f"{stage}:{ev.kind}:{n}",
                    "message": ev.message,
                    "detail": ev.detail,
                    "options": ["continue", "skip", "stop"],
                }
        answer = interrupt(ask)
        if not isinstance(answer, dict) or answer.get("key") != ask["key"]:
            return {"stage": stage, "_route": stage}  # an answer to an earlier question: re-check the stage
        action = answer.get("action")
        log = [_ev("answer", stage, question_kind=ask.get("kind") or ask["type"], action=action, key=ask["key"])]
        if ask["type"] == "wait_job":
            waiting.pop(stage, None)
            status, error = answer.get("job_status"), answer.get("error")
            log.append(_ev("job_finished", stage, job_id=ask["job_id"], status=status, error=error))
            if status == "succeeded":
                return {"stage": stage, "_route": stage, "log": log, "waiting": waiting}

            dispatch_flag = {
                "extraction": "extraction_dispatched",
                "appraisal": "appraisal_dispatched",
            }.get(stage)
            if dispatch_flag:
                counters = dict(ctx.run.counters or {})
                counters.pop(dispatch_flag, None)
                ctx.run.counters = counters

            d = policy.retry_decision(
                n,
                error,
                max_retries=ctx.settings.pipeline_max_retries,
                base_s=ctx.settings.pipeline_backoff_base_s,
                cap_s=ctx.settings.pipeline_backoff_cap_s,
                now=_now(),
            )
            if status == "failed" and d.retry and d.not_before is not None:
                log.append(
                    _ev("technical_retry", stage, attempt=d.attempt, until=d.not_before.isoformat(), error=error)
                )
                return {
                    "stage": stage,
                    "_route": stage,
                    "log": log,
                    "waiting": waiting,
                    "retries": {**retries, stage: d.attempt},
                    "not_before": {**not_before, stage: d.not_before.isoformat()},
                }
            reason = "the job was cancelled" if status == "cancelled" else (error or "unknown error")
            return {
                "stage": stage,
                "_route": stage,
                "log": log,
                "waiting": waiting,
                "failed": {**failed, stage: f"{reason}. {d.reason}"},
            }
        if ask["type"] == "backoff":
            not_before.pop(stage, None)
            return {"stage": stage, "_route": stage, "log": log, "not_before": not_before}
        if action == "stop":
            return {"stage": stage, "stop_reason": "stopped_by_user", "_route": END, "log": log}
        if action == "skip":
            failed.pop(stage, None)
            return {
                "stage": stage,
                "failed": failed,
                "skipped": [*(state.get("skipped") or []), stage],
                "_route": _next(stage),
                "log": [*log, _ev("stage_skipped", stage)],
            }
        if action == "extend":
            return {
                "stage": stage,
                "_route": stage,
                "log": log,
                "extra_budget": int(state.get("extra_budget") or 0) + ctx.settings.pipeline_token_budget,
            }
        if action == "retry":
            dispatch_flag = {
                "extraction": "extraction_dispatched",
                "appraisal": "appraisal_dispatched",
            }.get(stage)
            if dispatch_flag:
                counters = dict(ctx.run.counters or {})
                counters.pop(dispatch_flag, None)
                ctx.run.counters = counters
            failed.pop(stage, None)
            return {
                "stage": stage,
                "_route": stage,
                "failed": failed,
                "approved": {**approved, stage: True},
                "retries": {**retries, stage: n + 1},
                "log": [*log, _ev("manual_retry", stage, attempt=n + 1)],
            }
        if action == "approve":
            return {"stage": stage, "_route": stage, "approved": {**approved, stage: True}, "log": log}
        if ask.get("kind") == "verify_report" and action == "continue":
            ctx.run.counters = {**(ctx.run.counters or {}), "report_reviewed": True}
        return {"stage": stage, "_route": stage, "log": log}  # "continue": re-check the stage

    node.__name__ = f"node_{stage}"
    return node


def _route(state: dict[str, Any]) -> str:
    if state.get("stop_reason"):
        return END
    return str(state.get("_route") or END)


def _start(state: PipelineState) -> str:
    s = state.get("start_stage") or "protocol"
    return s if s in policy.STAGES else "protocol"


def build_graph() -> StateGraph[PipelineState]:
    g: StateGraph[PipelineState] = StateGraph(PipelineState)
    for s in policy.STAGES:
        g.add_node(s, cast(Any, make_node(s)))
    g.add_conditional_edges(START, _start, list(policy.STAGES))
    for s in policy.STAGES:
        g.add_conditional_edges(s, _route, [*policy.STAGES, END])
    return g


def compile_graph(checkpointer: Any) -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_graph().compile(checkpointer=checkpointer)
