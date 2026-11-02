"""PRISMA 2020 flow and full-text screening. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.db.models import Project, User
from app.db.session import get_db
from app.prisma import service as prisma_service
from app.services import protocol_service

router = APIRouter(prefix="/projects/{project_id}", tags=["prisma"])


@router.get("/prisma/flow")
def get_flow(
    protocol_version_id: uuid.UUID | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """PRISMA 2020 flow counts derived from record_status_events (never typed in, never model-generated)."""
    flow = prisma_service.get_flow(db, project.id, protocol_version_id)
    return prisma_service.flow_view(flow)


@router.get("/prisma/ft-reasons")
def ft_reasons(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    pv = protocol_service.approved(db, project)
    if pv is None:
        return []
    return prisma_service.ft_reason_options(pv)


@router.post("/prisma/ft-decisions/{paper_id}")
def ft_decide(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record a full-text screening decision. Exclusion always requires a reason_code."""
    decision = str(body.get("decision") or "")
    reason_code = body.get("reason_code") or None
    note = body.get("note") or None
    expected = body.get("expected_row_version")
    dec = prisma_service.ft_decide(
        db,
        project_id=project.id,
        paper_id=paper_id,
        user_id=user.id,
        decision=decision,
        reason_code=reason_code,
        note=note,
        expected_row_version=int(expected) if expected is not None else None,
    )
    return {"id": str(dec.id), "stage": dec.stage, "decision": dec.decision, "reason_code": dec.reason_code}


@router.post("/studies")
def create_study(
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Group included paper records into a study."""
    paper_ids = [uuid.UUID(str(i)) for i in (body.get("paper_ids") or [])]
    study = prisma_service.group_into_study(
        db,
        project.id,
        paper_ids,
        user_id=user.id,
        short_name=body.get("short_name") or None,
        notes=body.get("notes") or None,
    )
    return {"id": str(study.id), "short_name": study.short_name, "project_id": str(study.project_id)}


@router.get("/prisma/ft-queue")
def ft_queue(
    status: str | None = None,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Records awaiting or holding a full-text outcome, with counts per status. Derived from stored data only."""
    return prisma_service.ft_queue(db, project.id, status=status)


@router.post("/prisma/retrieval/{paper_id}")
def mark_retrieval(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Mark a report as not retrieved (PRISMA 'reports not retrieved'), or undo that with {"retrieved": true}."""
    return prisma_service.mark_retrieval(
        db,
        project_id=project.id,
        paper_id=paper_id,
        user_id=user.id,
        retrieved=bool(body.get("retrieved")),
        note=body.get("note") if isinstance(body.get("note"), str) else None,
    )


@router.get("/studies")
def list_studies(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Included reports grouped into studies; ungrouped reports count as one study each."""
    return prisma_service.list_studies(db, project.id)


@router.delete("/studies/{study_id}", status_code=204)
def ungroup_study(
    study_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    """Dissolve a study grouping. The reports themselves and every decision stay as they were."""
    prisma_service.ungroup_study(db, project.id, study_id, user_id=user.id)
