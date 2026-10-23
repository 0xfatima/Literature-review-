"""Relevance assessment and title/abstract screening. Every route depends on get_owned_project; paper ids are looked
up inside that project only. AI output is returned as `ai` (a suggestion); decisions are human records."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import Job, Project, User
from app.db.session import get_db
from app.schemas.jobs import JobOut
from app.schemas.screening import (
    BulkDecisionRequest,
    BulkResult,
    DecisionRequest,
    DecisionResult,
    PaperScreeningHistory,
    Queue,
    ReasonList,
    ReasonOut,
    RelevanceRequest,
    RelevanceStatus,
    UndoRequest,
    UndoResult,
)
from app.services import relevance_service, screening_service

router = APIRouter(prefix="/projects/{project_id}/screening", tags=["screening"])
Dispatch = Callable[[str, str], None]


@router.get("/queue", response_model=Queue)
def screening_queue(
    decision: str | None = None,
    band: str | None = None,
    suggestion: str | None = None,
    q: str | None = Query(None, max_length=200),
    flagged: bool | None = None,
    sort: str = "relevance",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> Queue:
    return Queue(
        **screening_service.queue(
            db,
            project,
            decision=decision,
            band=band,
            suggestion=suggestion,
            text=q,
            flagged=flagged,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/reasons", response_model=ReasonList)
def screening_reasons(
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReasonList:
    return ReasonList(
        items=[ReasonOut(code=r.code, label=r.label, group=r.group) for r in screening_service.reasons(db, project)],
        require_reason=settings.screening_ta_require_exclusion_reason,
    )


@router.get("/papers/{paper_id}/history", response_model=PaperScreeningHistory)
def screening_history(
    paper_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> PaperScreeningHistory:
    return PaperScreeningHistory(**screening_service.paper_history(db, project, paper_id))


@router.post("/papers/{paper_id}/decision", response_model=DecisionResult)
def record_decision(
    paper_id: uuid.UUID,
    body: DecisionRequest,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DecisionResult:
    return DecisionResult(
        **screening_service.decide(
            db,
            settings,
            user,
            project,
            paper_id,
            decision=body.decision,
            reason_code=body.reason_code,
            note=body.note,
            expected_row_version=body.expected_row_version,
        )
    )


@router.post("/bulk", response_model=BulkResult)
def record_bulk_decision(
    body: BulkDecisionRequest,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BulkResult:
    return BulkResult(
        **screening_service.bulk(
            db,
            settings,
            user,
            project,
            body.paper_ids,
            decision=body.decision,
            reason_code=body.reason_code,
            note=body.note,
        )
    )


@router.post("/undo", response_model=UndoResult)
def undo_decisions(
    body: UndoRequest,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> UndoResult:
    return UndoResult(**screening_service.undo(db, user, project, batch_id=body.batch_id, decision_id=body.decision_id))


@router.get("/relevance", response_model=RelevanceStatus)
def relevance_status(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RelevanceStatus:
    return RelevanceStatus(**relevance_service.status(db, settings, user, project))


@router.post("/relevance", response_model=JobOut, status_code=202)
def start_relevance(
    body: RelevanceRequest,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> Job:
    return relevance_service.start(
        db, settings, dispatch, limiter, user, project, use_llm=body.use_llm, force=body.force
    )
