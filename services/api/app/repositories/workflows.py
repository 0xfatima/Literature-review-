"""Workflow-run persistence. Lookups are scoped by project_id; the worker uses get_by_id (internal only)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import WorkflowEvent, WorkflowRun


def for_project(db: Session, project_id: uuid.UUID, *, lock: bool = False) -> WorkflowRun | None:
    q = select(WorkflowRun).where(WorkflowRun.project_id == project_id)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def get_by_id(db: Session, run_id: uuid.UUID, *, lock: bool = False) -> WorkflowRun | None:  # worker only
    q = select(WorkflowRun).where(WorkflowRun.id == run_id)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def answer_for(db: Session, run_id: uuid.UUID, question_id: uuid.UUID) -> WorkflowEvent | None:
    return db.scalar(
        select(WorkflowEvent).where(
            WorkflowEvent.run_id == run_id, WorkflowEvent.kind == "answer", WorkflowEvent.question_id == question_id
        )
    )


def add_event(
    db: Session,
    run_id: uuid.UUID,
    kind: str,
    *,
    question_id: uuid.UUID | None = None,
    stage: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Insert an event. Events keyed by a question id are written at most once (returns False if it existed)."""
    stmt = insert(WorkflowEvent).values(
        id=uuid.uuid4(),
        run_id=run_id,
        kind=kind,
        question_id=question_id,
        stage=stage,
        payload=payload or {},
        actor_id=actor_id,
    )
    if question_id is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["run_id", "kind", "question_id"], index_where=WorkflowEvent.question_id.isnot(None)
        )
    res = db.execute(stmt)
    return bool(getattr(res, "rowcount", 1))


def list_events(db: Session, run_id: uuid.UUID, limit: int) -> list[WorkflowEvent]:
    q = (
        select(WorkflowEvent)
        .where(WorkflowEvent.run_id == run_id)
        .order_by(WorkflowEvent.created_at.desc(), WorkflowEvent.id)
        .limit(limit)
    )
    return list(reversed(list(db.scalars(q))))
