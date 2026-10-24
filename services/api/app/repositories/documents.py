"""Persistence for uploaded documents and their page text. Every lookup is scoped by project_id (callers have done
the owner check through get_owned_project); `*_by_id` functions are for the worker only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import affected_rows
from app.db.models import (
    DOCUMENT_ACTIVE_PARSE,
    DocumentPage,
    PaperDocument,
    PaperRecord,
)


def add(db: Session, doc: PaperDocument) -> PaperDocument:
    db.add(doc)
    db.flush()
    return doc


def get(db: Session, project_id: uuid.UUID, doc_id: uuid.UUID, *, lock: bool = False) -> PaperDocument | None:
    q = select(PaperDocument).where(PaperDocument.project_id == project_id, PaperDocument.id == doc_id)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def get_by_id(db: Session, doc_id: uuid.UUID, *, lock: bool = False) -> PaperDocument | None:  # worker only
    q = select(PaperDocument).where(PaperDocument.id == doc_id)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def find_active_by_hash(db: Session, project_id: uuid.UUID, sha256: str) -> PaperDocument | None:
    return db.scalar(
        select(PaperDocument).where(
            PaperDocument.project_id == project_id,
            PaperDocument.sha256 == sha256,
            PaperDocument.removed_at.is_(None),
        )
    )


def list_(
    db: Session,
    project_id: uuid.UUID,
    *,
    parse_status: str | None,
    match_status: str | None,
    paper_id: uuid.UUID | None,
    text: str | None,
    include_removed: bool,
    offset: int,
    limit: int,
) -> tuple[list[PaperDocument], int]:
    cond: list[Any] = [PaperDocument.project_id == project_id]
    if not include_removed:
        cond.append(PaperDocument.removed_at.is_(None))
    if parse_status:
        cond.append(PaperDocument.parse_status == parse_status)
    if match_status:
        cond.append(PaperDocument.match_status == match_status)
    if paper_id:
        cond.append(PaperDocument.paper_id == paper_id)
    if text:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cond.append(PaperDocument.original_filename.ilike(like, escape="\\"))
    total = db.scalar(select(func.count()).select_from(PaperDocument).where(*cond)) or 0
    items = list(
        db.scalars(
            select(PaperDocument)
            .where(*cond)
            .order_by(PaperDocument.created_at.desc(), PaperDocument.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return items, int(total)


def status_counts(db: Session, project_id: uuid.UUID) -> dict[str, dict[str, int]]:
    base = (PaperDocument.project_id == project_id, PaperDocument.removed_at.is_(None))
    parse = db.execute(
        select(PaperDocument.parse_status, func.count()).where(*base).group_by(PaperDocument.parse_status)
    ).all()
    match = db.execute(
        select(PaperDocument.match_status, func.count()).where(*base).group_by(PaperDocument.match_status)
    ).all()
    return {"parse": {k: int(v) for k, v in parse}, "match": {k: int(v) for k, v in match}}


def stored_usage_project(db: Session, project_id: uuid.UUID) -> tuple[int, int]:
    """(bytes still held in storage, number of live documents). Removed documents count towards bytes until purged."""
    held = db.scalar(
        select(func.coalesce(func.sum(PaperDocument.size_bytes), 0)).where(
            PaperDocument.project_id == project_id, PaperDocument.purged_at.is_(None)
        )
    )
    live = db.scalar(
        select(func.count()).where(PaperDocument.project_id == project_id, PaperDocument.removed_at.is_(None))
    )
    return int(held or 0), int(live or 0)


def stored_bytes_user(db: Session, user_id: uuid.UUID) -> int:
    held = db.scalar(
        select(func.coalesce(func.sum(PaperDocument.size_bytes), 0)).where(
            PaperDocument.uploader_id == user_id, PaperDocument.purged_at.is_(None)
        )
    )
    return int(held or 0)


def for_paper(db: Session, paper_id: uuid.UUID) -> list[PaperDocument]:
    return list(
        db.scalars(
            select(PaperDocument)
            .where(PaperDocument.paper_id == paper_id, PaperDocument.removed_at.is_(None))
            .order_by(PaperDocument.created_at.desc())
        )
    )


def removed_due(db: Session, before: datetime, limit: int = 200) -> list[PaperDocument]:
    return list(
        db.scalars(
            select(PaperDocument)
            .where(
                PaperDocument.removed_at.is_not(None),
                PaperDocument.purged_at.is_(None),
                PaperDocument.removed_at <= before,
            )
            .order_by(PaperDocument.removed_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def stored_keys(db: Session, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    return set(db.scalars(select(PaperDocument.storage_key).where(PaperDocument.storage_key.in_(keys))))


def existing_project_ids(db: Session, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    from app.db.models import Project

    if not ids:
        return set()
    return set(db.scalars(select(Project.id).where(Project.id.in_(ids))))


def mark_failed(
    db: Session, doc_id: uuid.UUID, *, code: str, message: str, retryable: bool = True, only_if_active: bool = True
) -> bool:
    q = update(PaperDocument).where(PaperDocument.id == doc_id)
    if only_if_active:
        q = q.where(PaperDocument.parse_status.in_(DOCUMENT_ACTIVE_PARSE))
    res = db.execute(
        q.values(
            parse_status="failed",
            error_code=code,
            error_message=message,
            error_retryable=retryable,
            updated_at=func.now(),
            row_version=PaperDocument.row_version + 1,
        )
    )
    return affected_rows(res) == 1


# ---------- pages ----------


def pages(db: Session, doc: PaperDocument, offset: int, limit: int) -> tuple[list[DocumentPage], int]:
    if doc.text_version < 1:
        return [], 0
    cond = (DocumentPage.document_id == doc.id, DocumentPage.text_version == doc.text_version)
    total = db.scalar(select(func.count()).select_from(DocumentPage).where(*cond)) or 0
    rows = list(
        db.scalars(select(DocumentPage).where(*cond).order_by(DocumentPage.page_no).offset(offset).limit(limit))
    )
    return rows, int(total)


def first_pages_text(db: Session, doc_id: uuid.UUID, text_version: int, n: int = 2) -> str:
    rows = db.scalars(
        select(DocumentPage.text)
        .where(
            DocumentPage.document_id == doc_id,
            DocumentPage.text_version == text_version,
            DocumentPage.page_no <= n,
        )
        .order_by(DocumentPage.page_no)
    )
    return "\n".join(rows)


def insert_pages(db: Session, doc: PaperDocument, version: int, pages_: list[dict[str, Any]]) -> None:
    db.add_all(
        DocumentPage(id=uuid.uuid4(), document_id=doc.id, project_id=doc.project_id, text_version=version, **p)
        for p in pages_
    )
    db.flush()


def delete_pages(db: Session, doc_id: uuid.UUID) -> int:
    from sqlalchemy import delete

    return affected_rows(db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc_id)))


# ---------- papers ----------


def project_papers(db: Session, project_id: uuid.UUID) -> list[PaperRecord]:
    return list(db.scalars(select(PaperRecord).where(PaperRecord.project_id == project_id)))


def get_paper(db: Session, project_id: uuid.UUID, paper_id: uuid.UUID) -> PaperRecord | None:
    return db.scalar(select(PaperRecord).where(PaperRecord.project_id == project_id, PaperRecord.id == paper_id))


def titles(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, PaperRecord]:
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(PaperRecord).where(PaperRecord.id.in_(ids)))}
