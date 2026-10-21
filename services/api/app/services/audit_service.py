"""Writes audit events in the CALLER's transaction so a change and its audit record commit atomically.

Rules: never store passwords/tokens; never store free-text user content (titles, topics) — record
changed field NAMES instead, so that deleting a project leaves no personal text behind in the log.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import get_correlation_id
from app.core.logging import redact
from app.db.models import AuditEvent
from app.repositories import audit as audit_repo


def record(db: Session, *, action: str, entity_type: str, entity_id: uuid.UUID | None,
           actor_id: uuid.UUID | None, project_id: uuid.UUID | None = None,
           before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
           reason: str | None = None) -> AuditEvent:
    return audit_repo.add(db, AuditEvent(
        action=action, entity_type=entity_type, entity_id=entity_id, actor_id=actor_id, project_id=project_id,
        before=redact(before) if before is not None else None,
        after=redact(after) if after is not None else None,
        reason=reason, correlation_id=get_correlation_id()))
