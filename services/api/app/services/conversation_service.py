"""Chat threads and message history.

Ownership: every function takes a Project the caller has ALREADY loaded through the owner-scoped lookup
(api.deps.get_owned_project); conversations are then looked up within that project only.

Audit: thread lifecycle (created / renamed / deleted) is audited without free text (titles are user content).
Individual messages are not audited: the transcript is itself the history, and research decisions will be
stored as typed records in later phases (spec §3.1: "the transcript alone is not the source of truth").
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Conversation, Message, Project, User
from app.repositories import conversations as repo
from app.services import audit_service

DEFAULT_TITLE = "New conversation"
AUTO_TITLE_MAX = 60
MAX_TITLE = 200
MAX_MESSAGE_CHARS = 20_000


# ---------- pure helpers (unit-tested) ----------


def derive_title(content: str, max_len: int = AUTO_TITLE_MAX) -> str:
    """Title from the first message: whitespace collapsed, cut at a word boundary, ellipsis if shortened."""
    text = re.sub(r"\s+", " ", content).strip()
    if not text:
        return DEFAULT_TITLE
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    space = cut.rfind(" ")
    if space >= max_len // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    if not t:
        raise ValidationFailed("Title cannot be empty.", details=[{"field": "title", "issue": "required"}])
    if len(t) > MAX_TITLE:
        raise ValidationFailed("Title is too long.", details=[{"field": "title", "issue": "too_long"}])
    return t


def clean_content(content: str) -> str:
    """Keep the user's formatting (inner newlines), drop only leading/trailing blank space."""
    c = content.strip()
    if not c:
        raise ValidationFailed("Message cannot be empty.", details=[{"field": "content", "issue": "required"}])
    if len(c) > MAX_MESSAGE_CHARS:
        raise ValidationFailed(
            f"Message is longer than {MAX_MESSAGE_CHARS} characters.",
            details=[{"field": "content", "issue": "too_long"}],
        )
    return c


# ---------- guards ----------


def _require_writable(project: Project) -> None:
    if project.archived_at is not None:
        raise Conflict("This project is archived. Unarchive it to continue the conversation.", code="project_archived")


def get(db: Session, project: Project, conversation_id: uuid.UUID) -> Conversation:
    c = repo.get_in_project(db, project.id, conversation_id)
    if c is None:
        raise NotFound("Conversation not found.")
    return c


# ---------- threads ----------


def create(db: Session, user: User, project: Project, *, title: str | None, commit: bool = True) -> Conversation:
    _require_writable(project)
    conv = Conversation(
        project_id=project.id,
        created_by=user.id,
        title=clean_title(title) if title is not None else DEFAULT_TITLE,
        title_source="user" if title is not None else "auto",
    )
    repo.add(db, conv)
    audit_service.record(
        db,
        action="conversation.created",
        entity_type="conversation",
        entity_id=conv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"title_source": conv.title_source},
    )
    if commit:
        db.commit()
        db.refresh(conv)
    return conv


def list_(db: Session, project: Project, *, limit: int, offset: int) -> tuple[list[Conversation], int]:
    return repo.list_for_project(db, project.id, limit, offset), repo.count_for_project(db, project.id)


def rename(
    db: Session, user: User, project: Project, conversation_id: uuid.UUID, *, expected_version: int, title: str
) -> Conversation:
    _require_writable(project)
    conv = repo.lock_in_project(db, project.id, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found.")
    if conv.row_version != expected_version:
        db.rollback()
        raise Conflict("This conversation was changed elsewhere. Reload and try again.", code="version_conflict")
    new_title = clean_title(title)
    if new_title == conv.title and conv.title_source == "user":
        db.rollback()
        return conv
    conv.title, conv.title_source = new_title, "user"
    conv.row_version += 1
    conv.updated_at = func.now()
    audit_service.record(
        db,
        action="conversation.renamed",
        entity_type="conversation",
        entity_id=conv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"changed_fields": ["title"]},
    )
    db.commit()
    db.refresh(conv)
    return conv


def delete(db: Session, user: User, project: Project, conversation_id: uuid.UUID) -> None:
    conv = get(db, project, conversation_id)
    count = conv.message_count
    repo.delete(db, conv)  # messages cascade
    audit_service.record(
        db,
        action="conversation.deleted",
        entity_type="conversation",
        entity_id=conversation_id,
        actor_id=user.id,
        project_id=project.id,
        before={"message_count": count},
    )
    db.commit()


# ---------- messages ----------


def list_messages(
    db: Session,
    project: Project,
    conversation_id: uuid.UUID,
    *,
    after_seq: int | None,
    before_seq: int | None,
    limit: int,
) -> tuple[Conversation, list[Message], bool]:
    """Returns (conversation, messages ascending, has_older)."""
    conv = get(db, project, conversation_id)
    items = repo.list_messages(db, conv.id, after_seq=after_seq, before_seq=before_seq, limit=limit)
    has_older = bool(items) and items[0].seq > 1
    return conv, items, has_older


def post_user_message(
    db: Session,
    user: User,
    project: Project,
    conversation_id: uuid.UUID,
    *,
    content: str,
    client_message_id: uuid.UUID | None,
) -> tuple[Message, bool]:
    """Append a user message. Returns (message, created). A repeated client_message_id returns the original
    message with created=False, so a client can safely retry a send whose response it never received."""
    _require_writable(project)
    text = clean_content(content)
    get(db, project, conversation_id)  # 404 unless the thread belongs to this (already owner-checked) project
    if client_message_id is not None:
        existing = repo.get_by_client_id(db, conversation_id, client_message_id)
        if existing is not None:
            return existing, False
    conv = repo.lock_in_project(db, project.id, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found.")
    conv.message_count += 1
    msg = Message(
        conversation_id=conv.id,
        seq=conv.message_count,
        author_id=user.id,
        role="user",
        content=text,
        client_message_id=client_message_id,
    )
    if conv.title_source == "auto" and conv.message_count == 1:
        conv.title = derive_title(text)
    conv.last_message_at = func.now()
    conv.updated_at = func.now()
    try:
        repo.add(db, msg)
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent retry carrying the same client_message_id: return the winner.
        db.rollback()
        if client_message_id is not None:
            existing = repo.get_by_client_id(db, conversation_id, client_message_id)
            if existing is not None:
                return existing, False
        raise
    db.refresh(msg)
    return msg, True


def append_workflow_message(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    role: str,
    author_id: uuid.UUID | None,
    content: str,
    payload: dict[str, Any],
    client_message_id: uuid.UUID,
) -> Message:
    """Messages written by the review workflow (its questions, and the user's answers as recorded). Idempotent
    by client_message_id, which the workflow derives from the question id; does not commit. Archived projects are
    checked by the caller (the workflow refuses to act on them), so the worker can still finish a step."""
    existing = repo.get_by_client_id(db, conversation_id, client_message_id)
    if existing is not None:
        return existing
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    if conv is None:
        raise NotFound("Conversation not found.")
    existing = repo.get_by_client_id(db, conversation_id, client_message_id)  # re-check under the lock
    if existing is not None:
        return existing
    conv.message_count += 1
    msg = Message(
        conversation_id=conv.id,
        seq=conv.message_count,
        author_id=author_id,
        role=role,
        content=clean_content(content or "(no text)")[:MAX_MESSAGE_CHARS],
        payload=payload,
        client_message_id=client_message_id,
    )
    conv.last_message_at = func.now()
    conv.updated_at = func.now()
    repo.add(db, msg)
    return msg
