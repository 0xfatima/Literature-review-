"""Conversation/message persistence. Every lookup is scoped by project_id; callers must already have
verified that the project belongs to the current user (api.deps.get_owned_project). There is deliberately no
get-by-id-only function, so a conversation from another project is indistinguishable from a missing one."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message


def get_in_project(db: Session, project_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.project_id == project_id)
    )


def lock_in_project(db: Session, project_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation | None:
    """SELECT ... FOR UPDATE: serialises concurrent sends so `seq` stays gap-free and unique."""
    return db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.project_id == project_id)
        .with_for_update()
    )


def list_for_project(db: Session, project_id: uuid.UUID, limit: int, offset: int) -> list[Conversation]:
    q = (
        select(Conversation)
        .where(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(q))


def count_for_project(db: Session, project_id: uuid.UUID) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Conversation).where(Conversation.project_id == project_id)) or 0
    )


def add(db: Session, obj: Conversation | Message) -> None:
    db.add(obj)
    db.flush()


def delete(db: Session, conversation: Conversation) -> None:
    db.delete(conversation)
    db.flush()


def get_by_client_id(db: Session, conversation_id: uuid.UUID, client_message_id: uuid.UUID) -> Message | None:
    return db.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id, Message.client_message_id == client_message_id
        )
    )


def list_messages(
    db: Session, conversation_id: uuid.UUID, *, after_seq: int | None, before_seq: int | None, limit: int
) -> list[Message]:
    """Ascending by seq. after_seq: messages newer than it (polling). before_seq: the `limit` messages just
    older than it (scroll-back). Neither: the most recent `limit` messages."""
    q = select(Message).where(Message.conversation_id == conversation_id)
    if after_seq is not None:
        return list(db.scalars(q.where(Message.seq > after_seq).order_by(Message.seq).limit(limit)))
    if before_seq is not None:
        q = q.where(Message.seq < before_seq)
    newest_first = list(db.scalars(q.order_by(Message.seq.desc()).limit(limit)))
    return list(reversed(newest_first))
