"""Contextual workspace actions: explain an exclusion, find supporting evidence in a paper's full text, and ask the
workspace assistant in a chat thread. Every route depends on get_owned_project."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_llm_gateway, get_owned_project, get_rate_limiter
from app.assist import service as svc
from app.core.config import Settings, get_settings
from app.core.errors import ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import Project, User
from app.db.session import get_db
from app.providers.llm import LLMGateway, describe

router = APIRouter(prefix="/projects/{project_id}", tags=["assist"])


def _uuid(v: Any, name: str) -> uuid.UUID | None:
    if v in (None, ""):
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise ValidationFailed(f"{name} must be a UUID.", details=[{"field": name}]) from None


@router.get("/assist/status")
def assist_status(
    project: Project = Depends(get_owned_project), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    llm = describe(settings)
    return {
        "assistant_available": llm["configured"],
        "llm": llm,
        "data_sent": "Your protocol, the current workflow step and, when you ask about a paper, its bibliographic "
        "record and abstract. Uploaded documents are never sent by the assistant.",
        "actions": {
            "explain_exclusion": "From the recorded decision; no AI involved.",
            "find_supporting_evidence": "Searches the paper's stored full text; no AI involved.",
            "reassess_relevance": "Re-runs the relevance assessment for the paper in the background.",
        },
    }


@router.get("/workspace/overview")
def workspace_overview(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.workspace_overview(db, project)


@router.post("/papers/{paper_id}/explain-exclusion")
def explain_exclusion(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.explain_to_chat(db, project, user, paper_id, _uuid(body.get("conversation_id"), "conversation_id"))


@router.post("/papers/{paper_id}/find-evidence")
def find_evidence(
    paper_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.find_evidence(db, project, paper_id, body)


@router.post("/conversations/{conversation_id}/ask", status_code=201)
def ask_assistant(
    conversation_id: uuid.UUID,
    body: dict[str, Any],
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    gw: LLMGateway = Depends(get_llm_gateway),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict[str, Any]:
    return svc.ask(db, settings, gw, limiter, project, user, conversation_id, body)
