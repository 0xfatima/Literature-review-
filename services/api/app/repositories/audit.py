from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def add(db: Session, event: AuditEvent) -> AuditEvent:
    db.add(event)
    db.flush()
    return event


def list_for_project(db: Session, project_id: uuid.UUID, limit: int = 100) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id)
                           .order_by(AuditEvent.at.desc()).limit(limit)))
