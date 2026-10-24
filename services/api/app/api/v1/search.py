"""Scholarly search and discovered papers. Every route depends on get_owned_project (404 for foreign projects);
run and paper ids are looked up inside that project only. Searches run in the worker (`search.run` jobs)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import Project, SearchRun, User
from app.db.session import get_db
from app.schemas.documents import DocumentOut
from app.schemas.search import (
    BroadenDecision,
    PaperDetail,
    PaperList,
    ProviderInfo,
    SearchPlan,
    SearchPlanRequest,
    SearchRunCreate,
    SearchRunList,
    SearchRunOut,
    SearchSummary,
)
from app.services import document_service, paper_service, search_service

router = APIRouter(prefix="/projects/{project_id}", tags=["search"])
Dispatch = Callable[[str, str], None]


def _out(db: Session, settings: Settings, project: Project, run: SearchRun) -> SearchRunOut:
    reg = search_service.providers(settings)
    return SearchRunOut(**search_service.view(run, search_service.approved_id(db, project), reg))


@router.get("/search/providers", response_model=list[ProviderInfo])
def list_providers(
    project: Project = Depends(get_owned_project), settings: Settings = Depends(get_settings)
) -> list[dict[str, Any]]:
    return search_service.provider_catalog(settings)


@router.post("/search/plan", response_model=SearchPlan)
def preview_search(
    body: SearchPlanRequest,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchPlan:
    """What a search would send to each provider, built from the approved protocol (read-only)."""
    plan = search_service.build_plan(
        db, settings, project, tier=body.tier, expression=body.expression, provider_keys=body.providers
    )
    return SearchPlan(**plan, catalog=search_service.provider_catalog(settings))


@router.get("/search/summary", response_model=SearchSummary)
def search_summary(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> SearchSummary:
    return SearchSummary(**paper_service.summary(db, project))


@router.get("/search-runs", response_model=SearchRunList)
def list_search_runs(
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchRunList:
    runs = search_service.list_runs(db, project, limit)
    approved, reg = search_service.approved_id(db, project), search_service.providers(settings)
    return SearchRunList(items=[SearchRunOut(**search_service.view(r, approved, reg)) for r in runs])


@router.post("/search-runs", response_model=SearchRunOut, status_code=202)
def start_search_run(
    body: SearchRunCreate,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> SearchRunOut:
    run = search_service.start(
        db,
        settings,
        dispatch,
        limiter,
        user,
        project,
        tier=body.tier,
        expression=body.expression,
        provider_keys=body.providers,
    )
    return _out(db, settings, project, run)


@router.get("/search-runs/{search_run_id}", response_model=SearchRunOut)
def read_search_run(
    search_run_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchRunOut:
    return _out(db, settings, project, search_service.get_run(db, project, search_run_id))


@router.post("/search-runs/{search_run_id}/retry", response_model=SearchRunOut, status_code=202)
def retry_search_run(
    search_run_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> SearchRunOut:
    run = search_service.retry(db, settings, dispatch, user, project, search_run_id)
    return _out(db, settings, project, run)


@router.post("/search-runs/{search_run_id}/cancel", response_model=SearchRunOut)
def cancel_search_run(
    search_run_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchRunOut:
    return _out(db, settings, project, search_service.cancel(db, user, project, search_run_id))


@router.post("/search-runs/{search_run_id}/broaden", response_model=SearchRunOut)
def decide_broaden(
    search_run_id: uuid.UUID,
    body: BroadenDecision,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchRunOut:
    """Record the answer to "broaden discovery to additional sources?" (yes / no / later)."""
    run = search_service.decide_broaden(db, user, project, search_run_id, body.decision)
    return _out(db, settings, project, run)


@router.get("/papers", response_model=PaperList)
def list_papers(
    q: str | None = Query(None, max_length=200),
    access: str | None = None,
    provider: str | None = Query(None, max_length=32),
    search_run_id: uuid.UUID | None = None,
    flagged: bool | None = None,
    preprint: bool | None = None,
    sort: str = "recent",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> PaperList:
    return PaperList(
        **paper_service.list_(
            db,
            project,
            text=q,
            access=access,
            provider=provider,
            run_id=search_run_id,
            flagged=flagged,
            preprint=preprint,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/papers/{paper_id}", response_model=PaperDetail)
def read_paper(
    paper_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PaperDetail:
    data = paper_service.detail(db, project, paper_id)
    documents = document_service.documents_for_paper_id(db, settings, paper_id)
    return PaperDetail(**data, documents=[DocumentOut(**d) for d in documents])
