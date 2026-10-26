from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime

from sqlalchemy import Select, and_, literal, or_, select, tuple_
from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def add(db: Session, event: AuditEvent) -> AuditEvent:
    db.add(event)
    db.flush()
    return event


def list_for_project(db: Session, project_id: uuid.UUID, limit: int = 100) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.at.desc()).limit(limit)
        )
    )


def _filtered(
    q: Select[tuple[AuditEvent]],
    *,
    prefixes: Sequence[str] = (),
    entity_type: str | None = None,
    actor_kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Select[tuple[AuditEvent]]:
    if prefixes:
        q = q.where(or_(*(AuditEvent.action.startswith(p, autoescape=True) for p in prefixes)))
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    if actor_kind == "system":
        q = q.where(AuditEvent.actor_id.is_(None))
    elif actor_kind == "user":
        q = q.where(AuditEvent.actor_id.is_not(None))
    if since is not None:
        q = q.where(AuditEvent.at >= since)
    if until is not None:
        q = q.where(AuditEvent.at < until)
    return q


def project_query(project_id: uuid.UUID, **filters: object) -> Select[tuple[AuditEvent]]:
    return _filtered(select(AuditEvent).where(AuditEvent.project_id == project_id), **filters)  # type: ignore[arg-type]


def account_query(user_id: uuid.UUID, **filters: object) -> Select[tuple[AuditEvent]]:
    """Account-level events: things about this user (sign-ins, failed sign-ins, registration) and actions the user took
    outside any project. Project events are listed per project."""
    q = select(AuditEvent).where(
        AuditEvent.project_id.is_(None),
        or_(and_(AuditEvent.entity_type == "user", AuditEvent.entity_id == user_id), AuditEvent.actor_id == user_id),
    )
    return _filtered(q, **filters)  # type: ignore[arg-type]


def page(
    db: Session, q: Select[tuple[AuditEvent]], *, after: tuple[datetime, uuid.UUID] | None, limit: int
) -> tuple[list[AuditEvent], bool]:
    """Newest first, keyset-paginated on (at, id). Returns (rows, has_more)."""
    if after is not None:
        q = q.where(
            tuple_(AuditEvent.at, AuditEvent.id)
            < tuple_(literal(after[0], AuditEvent.at.type), literal(after[1], AuditEvent.id.type))
        )
    rows = list(db.scalars(q.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit + 1)))
    return rows[:limit], len(rows) > limit


def stream(db: Session, q: Select[tuple[AuditEvent]], *, max_rows: int, batch: int = 500) -> Iterator[AuditEvent]:
    """Oldest first, in batches, at most max_rows."""
    sent = 0
    last: tuple[datetime, uuid.UUID] | None = None
    while sent < max_rows:
        qq = q
        if last is not None:
            qq = qq.where(
                tuple_(AuditEvent.at, AuditEvent.id)
                > tuple_(literal(last[0], AuditEvent.at.type), literal(last[1], AuditEvent.id.type))
            )
        qq = qq.order_by(AuditEvent.at.asc(), AuditEvent.id.asc()).limit(min(batch, max_rows - sent))
        rows = list(db.scalars(qq))
        if not rows:
            return
        yield from rows
        sent += len(rows)
        last = (rows[-1].at, rows[-1].id)
