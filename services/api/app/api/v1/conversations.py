"""Project chat threads. Authorization: every route depends on get_owned_project (404 for foreign/missing
projects); conversations are then resolved inside that project only (404 for foreign/missing threads)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_owned_project
from app.db.models import Conversation, Project, User
from app.db.session import get_db
from app.schemas.conversations import (
    ConversationCreate,
    ConversationList,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
    MessagePage,
    MessagePosted,
)
from app.services import conversation_service as svc

router = APIRouter(prefix="/projects/{project_id}/conversations", tags=["conversations"])


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(
    body: ConversationCreate,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    return svc.create(db, user, project, title=body.title)


@router.get("", response_model=ConversationList)
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> ConversationList:
    items, total = svc.list_(db, project, limit=limit, offset=offset)
    return ConversationList(
        items=[ConversationOut.model_validate(c) for c in items], total=total, limit=limit, offset=offset
    )


@router.get("/{conversation_id}", response_model=ConversationOut)
def read_conversation(
    conversation_id: uuid.UUID, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)
) -> Conversation:
    return svc.get(db, project, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Conversation:
    return svc.rename(db, user, project, conversation_id, expected_version=body.expected_version, title=body.title)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    svc.delete(db, user, project, conversation_id)
    return Response(status_code=204)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
def list_messages(
    conversation_id: uuid.UUID,
    after_seq: int | None = Query(None, ge=0),
    before_seq: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> MessagePage:
    conv, items, has_older = svc.list_messages(
        db, project, conversation_id, after_seq=after_seq, before_seq=before_seq, limit=limit
    )
    return MessagePage(
        conversation=ConversationOut.model_validate(conv),
        items=[MessageOut.model_validate(m) for m in items],
        has_older=has_older,
    )


@router.post("/{conversation_id}/messages", response_model=MessagePosted, status_code=201)
def post_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    response: Response,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MessagePosted:
    msg, created = svc.post_user_message(
        db, user, project, conversation_id, content=body.content, client_message_id=body.client_message_id
    )
    if not created:
        response.status_code = 200  # idempotent replay of the same client_message_id
    conv = svc.get(db, project, conversation_id)
    return MessagePosted(message=MessageOut.model_validate(msg), conversation=ConversationOut.model_validate(conv))
