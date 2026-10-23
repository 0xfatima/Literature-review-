"""Review protocol and its workflow. Every route depends on get_owned_project (404 for foreign/missing projects)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project
from app.core.config import Settings, get_settings
from app.db.models import Project, User, WorkflowRun
from app.db.session import get_db
from app.protocol.stages import STAGE_LABELS, STAGES
from app.schemas.protocol import (
    DraftUpdate,
    ProtocolOverview,
    ProtocolVersionBrief,
    ProtocolVersionOut,
    RevisionCreate,
)
from app.schemas.workflow import WorkflowAnswer, WorkflowEventList, WorkflowEventOut, WorkflowRunOut, WorkflowState
from app.services import protocol_service, workflow_service

router = APIRouter(prefix="/projects/{project_id}", tags=["protocol"])
Dispatch = Callable[[str, str], None]


def _overview(db: Session, project: Project) -> ProtocolOverview:
    d, a = protocol_service.draft(db, project), protocol_service.approved(db, project)
    return ProtocolOverview(
        draft=ProtocolVersionOut(**protocol_service.view(d)) if d else None,
        approved=ProtocolVersionOut(**protocol_service.view(a)) if a else None,
        versions=[ProtocolVersionBrief.model_validate(v) for v in protocol_service.versions(db, project)],
        stage_labels=workflow_service.stage_labels(),
        field_labels=workflow_service.field_labels(),
    )


def _state(run: WorkflowRun | None) -> WorkflowState:
    return WorkflowState(
        run=WorkflowRunOut.model_validate(run) if run else None,
        stages=[{"id": s, "label": STAGE_LABELS[s]} for s in STAGES],
    )


@router.get("/protocol", response_model=ProtocolOverview)
def read_protocol(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> ProtocolOverview:
    return _overview(db, project)


@router.get("/protocol/versions/{version_id}", response_model=ProtocolVersionOut)
def read_protocol_version(
    version_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> ProtocolVersionOut:
    return ProtocolVersionOut(**protocol_service.view(protocol_service.get_version(db, project, version_id)))


@router.patch("/protocol/draft", response_model=ProtocolVersionOut)
def update_protocol_draft(
    body: DraftUpdate,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ProtocolVersionOut:
    patch = body.model_dump(exclude_unset=True)
    expected = patch.pop("expected_row_version")
    pv = protocol_service.update_draft(db, user, project, expected_row_version=expected, patch=patch)
    db.commit()
    return ProtocolVersionOut(**protocol_service.view(pv))


@router.post("/protocol/revisions", response_model=WorkflowState, status_code=202)
def start_protocol_revision(
    body: RevisionCreate,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> WorkflowState:
    return _state(workflow_service.revise(db, settings, dispatch, user, project, reason=body.reason))


@router.get("/workflow", response_model=WorkflowState)
def read_workflow(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> WorkflowState:
    return _state(workflow_service.get(db, project))


@router.post("/workflow", response_model=WorkflowState, status_code=202)
def start_workflow(
    response: Response,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> WorkflowState:
    run, created = workflow_service.start(db, settings, dispatch, user, project)
    if not created:
        response.status_code = 200
    return _state(run)


@router.post("/workflow/resume", response_model=WorkflowState, status_code=202)
def resume_workflow(
    body: WorkflowAnswer,
    response: Response,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> WorkflowState:
    run, created = workflow_service.resume(
        db,
        settings,
        dispatch,
        user,
        project,
        question_id=body.question_id,
        action=body.action,
        values=body.values,
        expected_row_version=body.expected_row_version,
    )
    if not created:
        response.status_code = 200  # the same answer was already recorded: no-op
    return _state(run)


@router.post("/workflow/recover", response_model=WorkflowState, status_code=202)
def recover_workflow(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> WorkflowState:
    return _state(workflow_service.recover(db, settings, dispatch, user, project))


@router.get("/workflow/events", response_model=WorkflowEventList)
def list_workflow_events(
    limit: int = Query(100, ge=1, le=500), project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> WorkflowEventList:
    return WorkflowEventList(
        items=[WorkflowEventOut.model_validate(e) for e in workflow_service.events(db, project, limit)]
    )
