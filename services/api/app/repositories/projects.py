"""Every query here is scoped by owner_id. There is deliberately NO get-by-id-only function:
ownership is part of the lookup, so a missing and a foreign project are indistinguishable (404)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import affected_rows
from app.db.models import Project


def get_for_owner(db: Session, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
    return db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))


def list_for_owner(db: Session, owner_id: uuid.UUID, include_archived: bool, limit: int, offset: int) -> list[Project]:
    q = select(Project).where(Project.owner_id == owner_id)
    if not include_archived:
        q = q.where(Project.archived_at.is_(None))
    return list(db.scalars(q.order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset)))


def count_for_owner(db: Session, owner_id: uuid.UUID, include_archived: bool) -> int:
    q = select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
    if not include_archived:
        q = q.where(Project.archived_at.is_(None))
    return int(db.scalar(q) or 0)


def add(db: Session, project: Project) -> Project:
    db.add(project)
    db.flush()
    return project


def update_with_version(
    db: Session, project_id: uuid.UUID, owner_id: uuid.UUID, expected_version: int, values: dict[str, Any]
) -> bool:
    """Optimistic concurrency: succeeds only if row_version still equals expected_version."""
    res = db.execute(
        update(Project)
        .where(Project.id == project_id, Project.owner_id == owner_id, Project.row_version == expected_version)
        .values(**values, row_version=Project.row_version + 1, updated_at=func.now())
    )
    return affected_rows(res) == 1


def delete(db: Session, project: Project) -> None:
    db.delete(project)
    db.flush()
