"""Writes audit events in the CALLER's transaction so a change and its audit record commit atomically.

Rules: never store passwords/tokens; never store free-text user content (titles, topics) — record
changed field NAMES instead, so that deleting a project leaves no personal text behind in the log.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.core.context import get_actor_process, get_correlation_id
from app.core.logging import redact
from app.db.models import AuditEvent
from app.repositories import audit as audit_repo


def record(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    return audit_repo.add(
        db,
        AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            project_id=project_id,
            before=redact(before) if before is not None else None,
            after=redact(after) if after is not None else None,
            reason=reason,
            correlation_id=get_correlation_id(),
            actor_process=get_actor_process(),
        ),
    )


# ---------- reading (Phase 14) ----------

ACTOR_KINDS = ("user", "system")


def _filters(category: str | None, entity_type: str | None, actor: str | None) -> dict[str, Any]:
    from app.audit.catalog import CATEGORY_NAMES, prefixes_for
    from app.core.errors import ValidationFailed

    if category and category not in CATEGORY_NAMES:
        raise ValidationFailed("Unknown category.", details=[{"field": "category", "issue": "invalid"}])
    if actor and actor not in ACTOR_KINDS:
        raise ValidationFailed("actor must be user or system.", details=[{"field": "actor", "issue": "invalid"}])
    if entity_type is not None and (len(entity_type) > 32 or not entity_type.replace("_", "").isalnum()):
        raise ValidationFailed("Invalid entity type.", details=[{"field": "entity_type", "issue": "invalid"}])
    prefixes: list[str] = []
    if category == "other":
        raise ValidationFailed("Filter by a named category.", details=[{"field": "category", "issue": "invalid"}])
    if category:
        prefixes = prefixes_for(category)
    return {"prefixes": prefixes, "entity_type": entity_type, "actor_kind": actor}


def list_events(
    db: Session,
    *,
    viewer_id: uuid.UUID,
    project_id: uuid.UUID | None,
    category: str | None = None,
    entity_type: str | None = None,
    actor: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """A page of a project's audit log (project_id) or of the viewer's account-level log (project_id=None).
    Authorization is the caller's job (get_owned_project / current_user)."""
    from app.audit import query

    f = _filters(category, entity_type, actor)
    q = audit_repo.project_query(project_id, **f) if project_id else audit_repo.account_query(viewer_id, **f)
    n = query.clamp_limit(limit)
    rows, more = audit_repo.page(db, q, after=query.decode_cursor(cursor), limit=n)
    return {
        "items": [query.event_view(e, viewer_id) for e in rows],
        "next_cursor": query.encode_cursor(rows[-1].at, rows[-1].id) if more and rows else None,
    }


def export_csv(
    db: Session, *, viewer_id: uuid.UUID, project_id: uuid.UUID, max_rows: int, category: str | None = None
) -> Iterator[str]:
    """Record the export (committed first, so the export itself is in the trail), then stream CSV oldest-first."""
    from app.audit import query

    f = _filters(category, None, None)
    record(
        db,
        action="audit.exported",
        entity_type="project",
        entity_id=project_id,
        actor_id=viewer_id,
        project_id=project_id,
        after={"format": "csv", "category": category, "max_rows": max_rows},
    )
    db.commit()

    # Materialised (bounded by max_rows) rather than streamed lazily: the request's DB session may be closed before a
    # lazy StreamingResponse body is consumed, depending on the FastAPI version.
    out = [query.csv_header()]
    out += [
        query.csv_row(query.event_view(e, viewer_id))
        for e in audit_repo.stream(db, audit_repo.project_query(project_id, **f), max_rows=max_rows)
    ]
    return iter(out)
