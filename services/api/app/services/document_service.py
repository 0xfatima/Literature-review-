"""Uploaded documents, API side: register an upload, list/inspect, link to papers, retry, remove, download.
Parsing, OCR and matching run in the worker (app.services.document_executor); nothing here parses a PDF.

Rules enforced here:
  * every document belongs to one project and is only reachable through that project's owner (get_owned_project);
  * the stored key is generated; the user's filename is display text only;
  * duplicate files (same SHA-256) are refused per project; per-project and per-user quotas are checked BEFORE storing;
  * a document is linked to a paper only when the user chose the paper, the match is reliable, or the user confirmed it;
  * a paper's `full_text_status` is derived from its documents, never set from provider claims.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, BinaryIO

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    Conflict,
    NotFound,
    PayloadTooLarge,
    RateLimited,
    ServiceUnavailable,
    UnsupportedMediaType,
    ValidationFailed,
)
from app.core.rate_limit import RateLimiter
from app.db.models import DOCUMENT_ACTIVE_PARSE, PaperDocument, PaperRecord, Project, User
from app.documents.intake import ReceivedUpload
from app.documents.validation import UploadRejection, sanitize_filename
from app.providers.storage import ObjectNotFound, ObjectStore, StorageError, document_key, project_prefix
from app.repositories import documents as repo
from app.services import audit_service, job_service
from app.services.job_service import Dispatcher

log = logging.getLogger(__name__)
JOB_TYPE = "document.process"
NON_RETRYABLE_HINT = "Retrying cannot fix this. Upload a corrected copy of the file instead."


def _now() -> datetime:
    return datetime.now(UTC)


def rejection_to_error(r: UploadRejection) -> Exception:
    """Map a validation rejection to the application's standard error envelope."""
    if r.status_code == 413:
        return PayloadTooLarge(r.message, code=r.code)
    if r.status_code == 415:
        return UnsupportedMediaType(r.message, code=r.code)
    return ValidationFailed(r.message, code=r.code)


# ---------- derived paper status ----------


def recompute_full_text_status(db: Session, paper_id: uuid.UUID | None) -> None:
    """parsed if any live document is parsed; uploaded while one is still being processed; failed if only failures
    remain; otherwise not_obtained. Derived from the documents so it can never drift from them."""
    if paper_id is None:
        return
    statuses = set(
        db.scalars(
            select(PaperDocument.parse_status).where(
                PaperDocument.paper_id == paper_id, PaperDocument.removed_at.is_(None)
            )
        )
    )
    if "parsed" in statuses:
        value = "parsed"
    elif statuses & set(DOCUMENT_ACTIVE_PARSE):
        value = "uploaded"
    elif "failed" in statuses:
        value = "failed"
    else:
        value = "not_obtained"
    db.execute(
        update(PaperRecord)
        .where(PaperRecord.id == paper_id, PaperRecord.full_text_status != value)
        .values(full_text_status=value)
    )


# ---------- views ----------


def _candidate_view(c: dict[str, Any], papers: dict[uuid.UUID, PaperRecord]) -> dict[str, Any]:
    try:
        p = papers.get(uuid.UUID(str(c["paper_id"])))
    except ValueError:
        p = None
    return {
        "paper_id": c["paper_id"],
        "score": c.get("score"),
        "reasons": c.get("reasons", []),
        "title": p.title if p else None,
        "year": p.year if p else None,
        "doi": p.doi if p else None,
    }


def view(doc: PaperDocument, papers: dict[uuid.UUID, PaperRecord], settings: Settings) -> dict[str, Any]:
    paper = papers.get(doc.paper_id) if doc.paper_id else None
    pages_read = doc.pages_text + doc.pages_ocr
    active = doc.parse_status in DOCUMENT_ACTIVE_PARSE
    reprocess = doc.parse_status == "parsed" and doc.pages_unreadable > 0 and settings.feature_ocr
    return {
        "id": doc.id,
        "paper_id": doc.paper_id,
        "paper_title": paper.title if paper else None,
        "original_filename": doc.original_filename,
        "size_bytes": doc.size_bytes,
        "sha256": doc.sha256,
        "parse_status": doc.parse_status,
        "error_code": doc.error_code,
        "error_message": doc.error_message,
        "page_count": doc.page_count,
        "text_version": doc.text_version,
        "pages_text": doc.pages_text,
        "pages_ocr": doc.pages_ocr,
        "pages_blank": doc.pages_blank,
        "pages_unreadable": doc.pages_unreadable,
        "text_coverage": round(pages_read / doc.page_count, 3) if doc.page_count else None,
        "pdf_metadata": doc.pdf_metadata,
        "warnings": doc.warnings,
        "scan_status": doc.scan_status,
        "scan_detail": doc.scan_detail,
        "match_status": doc.match_status,
        "match_method": doc.match_method,
        "match_score": doc.match_score,
        "candidates": [_candidate_view(c, papers) for c in doc.match_candidates],
        "needs_confirmation": doc.match_status == "needs_confirmation",
        "matched_at": doc.matched_at,
        "can_retry": not active
        and doc.storage_key is not None
        and ((doc.parse_status == "failed" and doc.error_retryable) or reprocess),
        "can_download": doc.storage_key is not None and doc.scan_status != "infected",
        "created_at": doc.created_at,
        "processed_at": doc.processed_at,
        "row_version": doc.row_version,
    }


def _papers_for(db: Session, docs: list[PaperDocument]) -> dict[uuid.UUID, PaperRecord]:
    ids: set[uuid.UUID] = set()
    for d in docs:
        if d.paper_id:
            ids.add(d.paper_id)
        for c in d.match_candidates:
            try:
                ids.add(uuid.UUID(str(c["paper_id"])))
            except (KeyError, ValueError):
                continue
    return repo.titles(db, list(ids))


def views(db: Session, settings: Settings, docs: list[PaperDocument]) -> list[dict[str, Any]]:
    papers = _papers_for(db, docs)
    return [view(d, papers, settings) for d in docs]


def documents_for_paper_id(db: Session, settings: Settings, paper_id: uuid.UUID) -> list[dict[str, Any]]:
    return views(db, settings, repo.for_paper(db, paper_id))


# ---------- limits and summary ----------


def limits(settings: Settings) -> dict[str, Any]:
    return {
        "max_bytes": settings.upload_max_bytes,
        "max_pages": settings.upload_max_pages,
        "quota_bytes_per_project": settings.upload_quota_bytes_per_project,
        "quota_files_per_project": settings.upload_quota_files_per_project,
        "quota_bytes_per_user": settings.upload_quota_bytes_per_user,
        "removed_retention_days": settings.document_removed_retention_days,
        "ocr_enabled": settings.feature_ocr,
        "ocr_languages": settings.ocr_languages if settings.feature_ocr else None,
        "malware_scanner": settings.malware_scanner,
        "storage_backend": settings.storage_backend,
    }


def summary(db: Session, settings: Settings, user: User, project: Project) -> dict[str, Any]:
    held, live = repo.stored_usage_project(db, project.id)
    counts = repo.status_counts(db, project.id)
    return {
        "limits": limits(settings),
        "usage": {
            "project_bytes": held,
            "project_files": live,
            "user_bytes": repo.stored_bytes_user(db, user.id),
        },
        "counts": counts,
    }


# ---------- upload ----------


def register_upload(
    db: Session,
    settings: Settings,
    store: ObjectStore,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    *,
    received: ReceivedUpload,
    filename: str | None,
    paper_id: uuid.UUID | None,
) -> PaperDocument:
    allowed, retry_after = limiter.hit(f"upload:{user.id}", settings.rate_limit_upload_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry_after, "Too many uploads. Try again later.")
    name = sanitize_filename(filename)
    paper = None
    if paper_id is not None:
        paper = repo.get_paper(db, project.id, paper_id)
        if paper is None:
            raise NotFound("Paper not found.")
    dup = repo.find_active_by_hash(db, project.id, received.sha256)
    if dup is not None:
        raise Conflict(
            "This exact file is already in the project.",
            code="duplicate_document",
            details=[{"document_id": str(dup.id), "filename": dup.original_filename}],
        )
    held, live = repo.stored_usage_project(db, project.id)
    if live + 1 > settings.upload_quota_files_per_project:
        raise PayloadTooLarge(
            f"This project already holds the maximum of {settings.upload_quota_files_per_project} documents. "
            "Remove documents you no longer need.",
            code="quota_files_exceeded",
        )
    if held + received.size > settings.upload_quota_bytes_per_project:
        raise PayloadTooLarge(
            "This upload would exceed the project's storage quota. Remove documents you no longer need.",
            code="quota_project_bytes_exceeded",
        )
    if repo.stored_bytes_user(db, user.id) + received.size > settings.upload_quota_bytes_per_user:
        raise PayloadTooLarge(
            "This upload would exceed your account's storage quota. Remove documents you no longer need.",
            code="quota_user_bytes_exceeded",
        )

    doc_id = uuid.uuid4()
    key = document_key(project.id, doc_id)
    try:
        store.put_file(key, received.path)
    except StorageError:
        log.exception("storing an upload failed", extra={"project_id": str(project.id)})
        raise ServiceUnavailable("The file could not be stored. Try again.", code="storage_unavailable") from None
    now = _now()
    doc = PaperDocument(
        id=doc_id,
        project_id=project.id,
        paper_id=paper.id if paper else None,
        uploader_id=user.id,
        storage_key=key,
        original_filename=name,
        size_bytes=received.size,
        sha256=received.sha256,
        match_status="user_linked" if paper else "unmatched",
        match_method="user_choice" if paper else None,
        matched_by=user.id if paper else None,
        matched_at=now if paper else None,
    )
    try:
        with db.begin_nested():  # savepoint: a unique violation must not poison the outer transaction
            repo.add(db, doc)
        recompute_full_text_status(db, doc.paper_id)
        audit_service.record(
            db,
            action="document.uploaded",
            entity_type="document",
            entity_id=doc.id,
            actor_id=user.id,
            project_id=project.id,
            after={"size_bytes": received.size, "sha256": received.sha256, "linked": paper is not None},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        _discard_object(store, key)
        existing = repo.find_active_by_hash(db, project.id, received.sha256)
        raise Conflict(
            "This exact file is already in the project.",
            code="duplicate_document",
            details=[{"document_id": str(existing.id) if existing else None}],
        ) from None
    except Exception:
        db.rollback()
        _discard_object(store, key)
        raise
    _start_processing(db, settings, dispatch, user, doc, text_version=1)
    db.refresh(doc)
    return doc


def _discard_object(store: ObjectStore, key: str) -> None:
    try:
        store.delete(key)
    except StorageError:
        log.warning("could not delete an unreferenced object; the orphan sweep will remove it")


def _start_processing(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, doc: PaperDocument, *, text_version: int
) -> None:
    try:
        job_service.enqueue(
            db,
            settings,
            dispatch,
            user,
            job_type=JOB_TYPE,
            project_id=doc.project_id,
            payload={"document_id": str(doc.id), "text_version": text_version},
            request_key=None,
        )
    except Conflict:
        raise
    except Exception:  # noqa: BLE001  the file is stored: say so and let the user retry, never lose it silently
        db.rollback()
        log.exception("could not enqueue document processing", extra={"document_id": str(doc.id)})
        repo.mark_failed(
            db,
            doc.id,
            code="enqueue_failed",
            message="The file was saved but processing could not be started. Use Retry.",
        )
        recompute_full_text_status(db, doc.paper_id)
        db.commit()


# ---------- reads ----------


def _live(db: Session, project: Project, doc_id: uuid.UUID, *, lock: bool = False) -> PaperDocument:
    doc = repo.get(db, project.id, doc_id, lock=lock)
    if doc is None or doc.removed_at is not None:
        raise NotFound("Document not found.")
    return doc


def list_(
    db: Session,
    settings: Settings,
    project: Project,
    *,
    parse_status: str | None,
    match_status: str | None,
    paper_id: uuid.UUID | None,
    text: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    from app.db.models import DOCUMENT_MATCH_STATUSES, DOCUMENT_PARSE_STATUSES

    parse_status, match_status, text = parse_status or None, match_status or None, text or None
    if parse_status and parse_status not in DOCUMENT_PARSE_STATUSES:
        raise ValidationFailed(
            "Unknown status filter.", details=[{"field": "status", "allowed": list(DOCUMENT_PARSE_STATUSES)}]
        )
    if match_status and match_status not in DOCUMENT_MATCH_STATUSES:
        raise ValidationFailed(
            "Unknown match filter.", details=[{"field": "match", "allowed": list(DOCUMENT_MATCH_STATUSES)}]
        )
    items, total = repo.list_(
        db,
        project.id,
        parse_status=parse_status,
        match_status=match_status,
        paper_id=paper_id,
        text=text,
        include_removed=False,
        offset=offset,
        limit=limit,
    )
    return {"items": views(db, settings, items), "total": total, "offset": offset, "limit": limit}


def detail(db: Session, settings: Settings, project: Project, doc_id: uuid.UUID) -> dict[str, Any]:
    return views(db, settings, [_live(db, project, doc_id)])[0]


def pages(db: Session, project: Project, doc_id: uuid.UUID, offset: int, limit: int) -> dict[str, Any]:
    doc = _live(db, project, doc_id)
    rows, total = repo.pages(db, doc, offset, limit)
    return {
        "document_id": doc.id,
        "parse_status": doc.parse_status,
        "text_version": doc.text_version,
        "page_count": doc.page_count,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "page_no": p.page_no,
                "text": p.text,
                "extraction": p.extraction,
                "char_count": p.char_count,
                "ocr_confidence": p.ocr_confidence,
                "warnings": p.warnings,
            }
            for p in rows
        ],
    }


# ---------- changes ----------


def retry(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project, doc_id: uuid.UUID
) -> PaperDocument:
    doc = _live(db, project, doc_id, lock=True)
    if doc.parse_status in DOCUMENT_ACTIVE_PARSE:
        db.rollback()
        raise Conflict("This document is already being processed.", code="already_processing")
    if doc.storage_key is None:
        db.rollback()
        raise Conflict("The stored file is no longer available. Upload it again.", code="file_missing")
    reprocess = doc.parse_status == "parsed" and doc.pages_unreadable > 0 and settings.feature_ocr
    if doc.parse_status == "failed" and not doc.error_retryable:
        db.rollback()
        raise Conflict(NON_RETRYABLE_HINT, code="not_retryable")
    if doc.parse_status == "parsed" and not reprocess:
        db.rollback()
        raise Conflict("Nothing to retry: every readable page has been extracted.", code="nothing_to_retry")
    target = doc.text_version + 1
    doc.parse_status = "queued"
    doc.error_code = doc.error_message = None
    doc.row_version += 1
    doc.updated_at = _now()
    recompute_full_text_status(db, doc.paper_id)
    audit_service.record(
        db,
        action="document.retry",
        entity_type="document",
        entity_id=doc.id,
        actor_id=user.id,
        project_id=project.id,
        after={"text_version": target},
    )
    db.commit()
    _start_processing(db, settings, dispatch, user, doc, text_version=target)
    db.refresh(doc)
    return doc


def set_link(
    db: Session,
    user: User,
    project: Project,
    doc_id: uuid.UUID,
    paper_id: uuid.UUID | None,
    expected_version: int | None,
) -> PaperDocument:
    """Link to a paper (confirming a suggestion when it was one), or unlink with paper_id=None."""
    doc = _live(db, project, doc_id, lock=True)
    if expected_version is not None and doc.row_version != expected_version:
        db.rollback()
        raise Conflict("This document was changed elsewhere. Reload and try again.", code="version_conflict")
    old_paper = doc.paper_id
    now = _now()
    if paper_id is None:
        if doc.paper_id is None and doc.match_status == "unmatched":
            db.rollback()
            return doc
        doc.paper_id, doc.match_status, doc.match_method, doc.match_score = None, "unmatched", None, None
        doc.match_candidates = []
        action = "document.unlinked"
    else:
        paper = repo.get_paper(db, project.id, paper_id)
        if paper is None:
            db.rollback()
            raise NotFound("Paper not found.")
        if doc.paper_id == paper.id and doc.match_status in ("user_linked", "confirmed"):
            db.rollback()
            return doc
        suggested = {str(c.get("paper_id")) for c in doc.match_candidates}
        was_suggested = doc.match_status in ("needs_confirmation", "auto_matched") and (
            str(paper.id) in suggested or doc.paper_id == paper.id
        )
        doc.paper_id = paper.id
        doc.match_status = "confirmed" if was_suggested else "user_linked"
        doc.match_method = doc.match_method if was_suggested and doc.match_method else "user_choice"
        doc.match_candidates = []
        action = "document.link_confirmed" if was_suggested else "document.linked"
    doc.matched_by, doc.matched_at = user.id, now
    doc.row_version += 1
    doc.updated_at = now
    db.flush()
    recompute_full_text_status(db, old_paper)
    recompute_full_text_status(db, doc.paper_id)
    audit_service.record(
        db,
        action=action,
        entity_type="document",
        entity_id=doc.id,
        actor_id=user.id,
        project_id=project.id,
        before={"paper_id": str(old_paper) if old_paper else None},
        after={"paper_id": str(doc.paper_id) if doc.paper_id else None, "match_status": doc.match_status},
    )
    db.commit()
    db.refresh(doc)
    return doc


def remove(db: Session, user: User, project: Project, doc_id: uuid.UUID) -> None:
    """Soft removal: hidden at once, file purged after DOCUMENT_REMOVED_RETENTION_DAYS (see purge())."""
    doc = _live(db, project, doc_id, lock=True)
    now = _now()
    doc.removed_at, doc.removed_by, doc.updated_at = now, user.id, now
    doc.row_version += 1
    db.flush()
    recompute_full_text_status(db, doc.paper_id)
    audit_service.record(
        db,
        action="document.removed",
        entity_type="document",
        entity_id=doc.id,
        actor_id=user.id,
        project_id=project.id,
        after={"paper_id": str(doc.paper_id) if doc.paper_id else None},
    )
    db.commit()


def open_for_download(
    db: Session, store: ObjectStore, user: User, project: Project, doc_id: uuid.UUID
) -> tuple[PaperDocument, BinaryIO]:
    doc = _live(db, project, doc_id)
    if doc.scan_status == "infected":
        raise Conflict("This file was blocked by the malware scanner and cannot be downloaded.", code="blocked")
    if doc.storage_key is None:
        raise NotFound("The stored file is no longer available.", code="file_missing")
    try:
        handle = store.open(doc.storage_key)
    except ObjectNotFound:
        raise NotFound("The stored file is missing. Upload it again.", code="file_missing") from None
    except StorageError:
        raise ServiceUnavailable("The file could not be read. Try again.", code="storage_unavailable") from None
    audit_service.record(
        db,
        action="document.downloaded",
        entity_type="document",
        entity_id=doc.id,
        actor_id=user.id,
        project_id=project.id,
    )
    db.commit()
    return doc, handle


# ---------- storage lifecycle ----------


def purge_project_objects(store: ObjectStore, project_id: uuid.UUID) -> int:
    """Delete every stored object of a (just deleted) project. Best effort: a failure is logged and the orphan sweep
    removes whatever is left."""
    try:
        return store.delete_prefix(project_prefix(project_id))
    except (StorageError, OSError):
        log.exception("could not purge a deleted project's files", extra={"project_id": str(project_id)})
        return 0


def purge(db: Session, settings: Settings, store: ObjectStore, now: datetime | None = None) -> dict[str, int]:
    """Retention and orphan sweep (scheduled; safe to run concurrently and repeatedly).
    1. Removed documents older than the retention period: delete the file and the page text; keep the row.
    2. Stored objects with no database row, older than the grace period: delete them (covers a crash between storing a
       file and committing its row, and a project deleted while its files could not be removed)."""
    from datetime import timedelta

    now = now or _now()
    purged = orphans = 0
    cutoff = now - timedelta(days=settings.document_removed_retention_days)
    for doc in repo.removed_due(db, cutoff):
        try:
            if doc.storage_key:
                store.delete(doc.storage_key)
        except StorageError:
            log.warning("purge could not delete a file; will retry", extra={"document_id": str(doc.id)})
            continue
        repo.delete_pages(db, doc.id)
        doc.storage_key, doc.purged_at = None, now
        audit_service.record(
            db,
            action="document.purged",
            entity_type="document",
            entity_id=doc.id,
            actor_id=None,
            project_id=doc.project_id,
        )
        purged += 1
    db.commit()
    grace = now - timedelta(seconds=settings.document_orphan_grace_s)
    old = [o for o in store.list_objects("projects/") if o.modified_at <= grace]
    known = repo.stored_keys(db, [o.key for o in old])
    for o in old:
        if o.key not in known:
            try:
                if store.delete(o.key):
                    orphans += 1
            except StorageError:
                log.warning("orphan sweep could not delete a file")
    return {"purged_documents": purged, "orphan_objects_deleted": orphans}


def sweep_reconcile(db: Session, job_input: dict[str, Any]) -> bool:
    """Called by the job sweeper when a document job gave up after worker loss: the document must not look busy
    forever. Returns True when a document was changed."""
    raw = job_input.get("document_id")
    try:
        doc_id = uuid.UUID(str(raw))
    except ValueError:
        return False
    doc = repo.get_by_id(db, doc_id)
    if doc is None:
        return False
    had_text = doc.text_version >= 1
    if had_text:
        # a failed REPROCESS must not hide the text that was already extracted
        changed = doc.parse_status in DOCUMENT_ACTIVE_PARSE
        if changed:
            doc.parse_status = "parsed"
            doc.warnings = [
                *doc.warnings,
                {
                    "code": "reprocess_failed",
                    "message": "Re-reading this document failed (the worker stopped). The previous text is kept.",
                },
            ]
            doc.row_version += 1
    else:
        changed = repo.mark_failed(
            db,
            doc.id,
            code="worker_lost",
            message="The background worker stopped while reading this file. Use Retry.",
        )
    if changed:
        recompute_full_text_status(db, doc.paper_id)
    return changed
