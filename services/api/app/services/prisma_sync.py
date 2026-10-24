"""Thin adapter between screening_service and prisma.service, breaking the circular import.

screening_service should not import prisma.service directly (which imports from
app.services itself). This module is imported by both and holds the bridge functions.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import ProtocolVersion, ScreeningDecision
from app.prisma import service as prisma_service


def on_ta_decision(db: Session, decision: ScreeningDecision, pv: ProtocolVersion) -> None:
    """Called after each title/abstract screening decision is flushed (before commit).
    Skips 'uncertain' and 'cleared' — those leave the paper pending in the PRISMA flow."""
    if decision.decision in ("uncertain", "cleared"):
        return
    prisma_service.sync_from_screening_decision(db, decision, pv)


def seed_identified(
    db: Session,
    project_id: object,
    pv_id: object,
    paper_ids: list[uuid.UUID],
    *,
    source_tag: str = "database",
) -> int:
    """Emit 'identified' events for newly discovered papers. Called by search_service after a run completes."""
    import uuid

    return prisma_service.seed_identified_events(
        db,
        uuid.UUID(str(project_id)),
        uuid.UUID(str(pv_id)),
        [uuid.UUID(str(p)) for p in paper_ids],
        source_tag=source_tag,
    )
