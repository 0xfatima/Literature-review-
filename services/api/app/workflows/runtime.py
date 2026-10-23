"""Runs the graph inside a worker job and mirrors its position into the tables.

advance() is safe to call more than once for the same job (redelivery, a requeue after a killed worker): it looks
at the checkpoint first and only resumes when the checkpoint is still waiting on the question being answered.

Tables are authoritative (ADR D3). Before touching the graph, the checkpoint's protocol version is compared with
the run row; if they disagree the run is marked needs_attention and the graph is not advanced.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Job, WorkflowRun
from app.repositories import workflows as wf_repo
from app.services import workflow_service
from app.workflows.graph import compile_graph

log = logging.getLogger(__name__)
CHECKPOINT_SCHEMA = "lg_checkpoints"
_SETUP_LOCK_KEY = 0x5352_5333  # pg advisory lock id for one-time checkpointer setup
_setup_done = False
_setup_mutex = threading.Lock()


def _conninfo() -> str:
    url = get_settings().database_url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def checkpointer() -> Iterator[PostgresSaver]:
    """A PostgresSaver on its own autocommit connection, confined to the lg_checkpoints schema."""
    global _setup_done
    with psycopg.connect(
        _conninfo(),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-c search_path={CHECKPOINT_SCHEMA}",
    ) as conn:
        saver = PostgresSaver(conn)
        if not _setup_done:
            with _setup_mutex:
                if not _setup_done:
                    conn.execute("SELECT pg_advisory_lock(%s)", (_SETUP_LOCK_KEY,))
                    try:
                        saver.setup()  # idempotent; runs LangGraph's own checkpoint-table migrations
                    finally:
                        conn.execute("SELECT pg_advisory_unlock(%s)", (_SETUP_LOCK_KEY,))
                    _setup_done = True
        yield saver


def _cfg(run: WorkflowRun) -> RunnableConfig:
    return {"configurable": {"thread_id": str(run.thread_id)}}


def _initial_state(run: WorkflowRun, mode: str, stage: str) -> dict[str, Any]:
    s = get_settings()
    return {
        "project_id": str(run.project_id),
        "run_id": str(run.id),
        "thread_id": str(run.thread_id),
        "protocol_version_id": str(run.protocol_version_id),
        "stage": stage,
        "step": 0,
        "iteration_count": 0,
        "max_iterations": run.max_iterations or s.workflow_max_iterations,
        "framework": None,
        "return_to": None,
        "mode": mode,
        "stop_reason": None,
    }


def advance(db: Session, job: Job) -> str:
    """Move the run's graph forward for this job and persist the outcome. Returns a short outcome string."""
    command = str(job.input.get("command"))
    run = wf_repo.get_by_id(db, uuid.UUID(str(job.input["run_id"])), lock=True)
    if run is None:
        return "run_missing"
    if run.status not in ("queued", "running"):
        db.rollback()
        return "not_runnable"  # already finished by an earlier delivery, or stopped by a human
    run.status = "running"
    db.commit()

    with checkpointer() as saver:
        graph = compile_graph(saver)
        cfg = _cfg(run)
        snap = graph.get_state(cfg)
        values: dict[str, Any] = dict(snap.values or {})

        expected = str(job.input.get("checkpoint_version_id") or run.protocol_version_id)
        if values and values.get("protocol_version_id") != expected:
            workflow_service.mark_needs_attention(
                db,
                run,
                "The saved workflow position refers to a different protocol version than the project's records. "
                "The records are kept; restart the workflow from them.",
            )
            db.commit()
            return "needs_attention"

        pending = [i.value for i in snap.interrupts]
        if command in ("start", "rebuild"):
            if not values:
                graph.invoke(_initial_state(run, str(job.input.get("mode") or "start"), run.stage), cfg)
            elif snap.next and not pending:
                graph.invoke(None, cfg)  # crashed mid-step: continue from the last checkpoint
        elif command == "resume":
            qid = str(job.input["question_id"])
            ev = wf_repo.answer_for(db, run.id, uuid.UUID(qid))
            if ev is None:
                workflow_service.mark_needs_attention(db, run, "An answer was expected but is not recorded.")
                db.commit()
                return "needs_attention"
            if pending and pending[0].get("id") == qid:
                graph.invoke(Command(resume=ev.payload["route"]), cfg)
            elif snap.next and not pending:
                graph.invoke(None, cfg)
            # else: the checkpoint is already past this question (an earlier delivery got further); just sync.
        elif command == "revise":
            if not snap.next:
                graph.invoke(
                    {
                        "mode": "revise",
                        "protocol_version_id": str(run.protocol_version_id),
                        "stage": "PROTOCOL_REVIEW",
                        "return_to": None,
                        "stop_reason": None,
                    },
                    cfg,
                )
            elif not pending:
                graph.invoke(None, cfg)
        else:
            raise ValueError(f"unknown workflow command {command!r}")

        snap = graph.get_state(cfg)
    workflow_service.sync_from_checkpoint(
        db, run, values=dict(snap.values or {}), interrupts=[i.value for i in snap.interrupts]
    )
    db.commit()
    return run.status
