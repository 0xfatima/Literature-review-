"""The review workflow graph (protocol module). Nodes ask questions and route; they do not write business data.

Why nodes are side-effect free: the API validates and applies the user's answer (protocol_service) in the same
transaction that records it, then resumes the graph with a compact routing payload. LangGraph re-runs an
interrupted node from the top on resume, and the worker may be killed at any point, so anything a node did before
`interrupt()` could run twice. With no side effects, replay is always safe.

Branching: framework 'none' skips the elements step; the review question routes to approval or back to any
section (controlled backtracking) and then returns to the review. A revision re-enters at the review.
Bounded loops: every answered question increments iteration_count; at max_iterations the run stops with a
recorded reason instead of asking again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.core.config import get_settings
from app.workflows import questions as qs
from app.workflows.state import ReviewState

GRAPH_NAME = "review"
GRAPH_VERSION = "protocol-v1"


class ResumeMismatch(Exception):
    """The resume payload does not answer the question this node asked (a bug or a tampered checkpoint)."""


class _Routed(ReviewState, total=False):
    _route: str  # transient routing key written by a node and read by the edge right after it


def _debug_pause() -> None:
    # Test hook for the worker-crash drill (ADR A7 test 3). Settings refuse a non-zero value outside dev/test.
    delay = get_settings().workflow_debug_node_delay_s
    if delay > 0:
        time.sleep(delay)


def _ask(node: str) -> Callable[[_Routed], dict[str, Any]]:
    def run(state: _Routed) -> dict[str, Any]:
        _debug_pause()
        if state.get("iteration_count", 0) >= state.get("max_iterations", 1):
            return {"stop_reason": "max_iterations"}
        q = qs.build(
            node,
            thread_id=state["thread_id"],
            step=state.get("step", 0),
            protocol_version_id=state["protocol_version_id"],
        )
        answer = interrupt(q)
        if not isinstance(answer, dict) or answer.get("question_id") != q["id"]:
            raise ResumeMismatch(f"resume payload does not answer question {q['id']}")
        update: dict[str, Any] = {
            "step": state.get("step", 0) + 1,
            "iteration_count": state.get("iteration_count", 0) + 1,
            "stage": q["stage"],
        }
        if "framework" in answer:
            update["framework"] = answer["framework"]
        if node == "review":
            if answer.get("action") == "approve":
                update.update(
                    stage=answer["next_stage"],
                    protocol_version_id=answer["protocol_version_id"],
                    return_to=None,
                    mode=None,
                )
                update["_route"] = "approved"
            else:
                update.update(return_to="review", _route=answer["section"])
        else:
            back = state.get("return_to")
            fw = answer.get("framework", state.get("framework"))
            update["_route"] = back or qs.default_next(node, framework=fw)
            update["return_to"] = None
        return update

    return run


def _route_after(state: dict[str, Any]) -> str:
    if state.get("stop_reason"):
        return END
    return str(state.get("_route") or END)


def _route_start(state: _Routed) -> str:
    mode = state.get("mode")
    if mode in ("revise", "rebuild_review"):
        return "review"
    if mode == "rebuild_done":
        return END
    return "objective"


def build_graph() -> StateGraph[_Routed]:
    g: StateGraph[_Routed] = StateGraph(_Routed)
    nodes = list(qs.ORDER)
    for node in nodes:
        # cast: LangGraph's _Node protocol does not accept a plain Callable under mypy; behaviour is identical.
        g.add_node(node, cast(Any, _ask(node)))
    g.add_node("approved", lambda s: {"_route": END})
    g.add_conditional_edges(START, _route_start, ["objective", "review", END])
    for node in nodes:
        g.add_conditional_edges(node, _route_after, [*nodes, "approved", END])
    g.add_edge("approved", END)
    return g


def compile_graph(checkpointer: Any) -> CompiledStateGraph[Any, Any, Any, Any]:
    return build_graph().compile(checkpointer=checkpointer)
