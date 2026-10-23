"""Protocol-version persistence. Every lookup is scoped by project_id (callers have already done the owner check)."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ProtocolVersion


def _q(project_id: uuid.UUID) -> Select[ProtocolVersion]:
    return (
        select(ProtocolVersion)
        .where(ProtocolVersion.project_id == project_id)
        .options(selectinload(ProtocolVersion.questions), selectinload(ProtocolVersion.criteria))
    )


def get_in_project(db: Session, project_id: uuid.UUID, version_id: uuid.UUID) -> ProtocolVersion | None:
    return db.scalar(_q(project_id).where(ProtocolVersion.id == version_id))


def by_status(db: Session, project_id: uuid.UUID, status: str, *, lock: bool = False) -> ProtocolVersion | None:
    q = _q(project_id).where(ProtocolVersion.status == status)
    if lock:
        q = q.with_for_update(of=ProtocolVersion)
    return db.scalar(q)


def list_for_project(db: Session, project_id: uuid.UUID) -> list[ProtocolVersion]:
    return list(db.scalars(_q(project_id).order_by(ProtocolVersion.version.desc())))


def next_version(db: Session, project_id: uuid.UUID) -> int:
    current = db.scalar(select(func.max(ProtocolVersion.version)).where(ProtocolVersion.project_id == project_id))
    return int(current or 0) + 1
