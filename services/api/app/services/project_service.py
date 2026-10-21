from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Project, User
from app.repositories import projects as repo
from app.services import audit_service


def _require_owned(db: Session, user: User, project_id: uuid.UUID) -> Project:
    p = repo.get_for_owner(db, project_id, user.id)
    if p is None:
        raise NotFound("Project not found.")  # identical for missing and foreign projects (no enumeration)
    return p


def create(db: Session, user: User, *, title: str, topic: str) -> Project:
    title, topic = title.strip(), topic.strip()
    if not title:
        raise ValidationFailed("Title is required.", details=[{"field": "title", "issue": "required"}])
    if not topic:
        raise ValidationFailed("Topic is required.", details=[{"field": "topic", "issue": "required"}])
    p = repo.add(db, Project(owner_id=user.id, title=title, topic_original=topic))
    audit_service.record(db, action="project.created", entity_type="project", entity_id=p.id,
                         actor_id=user.id, project_id=p.id, after={"status": p.status})
    db.commit()
    return p


def get(db: Session, user: User, project_id: uuid.UUID) -> Project:
    return _require_owned(db, user, project_id)


def list_(db: Session, user: User, *, include_archived: bool, limit: int, offset: int) -> tuple[list[Project], int]:
    return (repo.list_for_owner(db, user.id, include_archived, limit, offset),
            repo.count_for_owner(db, user.id, include_archived))


def update(db: Session, user: User, project_id: uuid.UUID, *, expected_version: int,
           title: str | None, archived: bool | None) -> Project:
    p = _require_owned(db, user, project_id)
    values: dict[str, Any] = {}
    changed: list[str] = []
    if title is not None:
        title = title.strip()
        if not title:
            raise ValidationFailed("Title cannot be empty.", details=[{"field": "title", "issue": "required"}])
        if title != p.title:
            values["title"] = title
            changed.append("title")  # field NAME only: no free text in the audit log
    before_state = {"archived": p.archived_at is not None}
    if archived is not None and archived != (p.archived_at is not None):
        values["archived_at"] = datetime.now(timezone.utc) if archived else None
        changed.append("archived")
    if not values:
        return p
    if not repo.update_with_version(db, p.id, user.id, expected_version, values):
        db.rollback()
        raise Conflict("This project was changed elsewhere. Reload and try again.", code="version_conflict")
    after_state = {"archived": values["archived_at"] is not None} if "archived_at" in values else before_state
    action = ("project.archived" if archived else "project.unarchived") if changed == ["archived"] else "project.updated"
    audit_service.record(db, action=action, entity_type="project", entity_id=p.id, actor_id=user.id,
                         project_id=p.id, before=before_state, after={**after_state, "changed_fields": changed})
    db.commit()
    db.refresh(p)
    return p


def delete(db: Session, user: User, project_id: uuid.UUID) -> None:
    p = _require_owned(db, user, project_id)
    pid = p.id
    repo.delete(db, p)  # dependent rows cascade; stored objects will be purged here in later phases
    audit_service.record(db, action="project.deleted", entity_type="project", entity_id=pid,
                         actor_id=user.id, project_id=pid)
    db.commit()
