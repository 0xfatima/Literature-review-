"""Document processing, worker side (job type `document.process`).

Steps, each reporting progress and refreshing the job heartbeat (the sweeper presumes a job without a heartbeat for
JOB_STALE_AFTER_S has lost its worker):
  1. fetch the stored file and verify its SHA-256 (detects a missing, damaged or swapped object);
  2. malware scan, when a scanner is configured (an infected file is deleted at once);
  3. extract page text in a resource-limited child process; OCR only the pages that need it;
  4. match the PDF to the project's papers;
  5. ONE transaction: write the pages, counts, warnings, match result and paper status, so a crash can never leave a
     half-processed document. A redelivered job for an already processed document does nothing.
An expected failure (password-protected, scanned with OCR off, damaged, ...) marks the document `failed` with a code
and an actionable message. A failed RE-processing never hides text that was already extracted.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Job, PaperDocument, PaperRecord
from app.documents import matching, pipeline
from app.documents.ocr import OcrEngine, build_ocr
from app.jobs import fsm
from app.providers.malware import MalwareScanner, build_scanner
from app.providers.storage import ObjectNotFound, ObjectStore, StorageError, build_store
from app.repositories import documents as repo
from app.repositories import jobs as job_repo
from app.search.records import Author, author_key
from app.services import audit_service, document_service

log = logging.getLogger(__name__)
HASH_CHUNK = 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def paper_facts(papers: list[PaperRecord]) -> list[matching.PaperFacts]:
    out = []
    for p in papers:
        first = (p.authors or [{}])[0] if p.authors else {}
        surname = author_key(Author(name=str(first.get("name") or ""), family=first.get("family"))) if first else None
        out.append(matching.PaperFacts(str(p.id), p.title, p.doi, p.arxiv_id, p.year, surname))
    return out


def document_facts(ex: pipeline.Extraction) -> matching.DocumentFacts:
    meta = ex.metadata
    return matching.DocumentFacts(
        first_pages_text="\n".join(p.text for p in ex.pages[:2]),
        metadata_title=meta.get("title", ""),
        metadata_doi=" ".join(filter(None, (meta.get("subject"), meta.get("keywords")))),
        metadata_subject=meta.get("subject", ""),
    )


class _Progress:
    def __init__(self, db: Session, job: Job) -> None:
        self.db, self.job_id = db, job.id
        self.value, self.message = 5, "Starting"

    def set(self, value: int, message: str) -> None:
        self.value, self.message = value, message
        self.tick()

    def tick(self) -> None:
        job_repo.set_progress(self.db, self.job_id, self.value, self.message)
        self.db.commit()


def execute(
    db: Session,
    job: Job,
    settings: Settings,
    *,
    store: ObjectStore | None = None,
    scanner: MalwareScanner | None = None,
    ocr: OcrEngine | None = None,
) -> str:
    """Process one document. Returns "parsed", "failed" or "skipped". The job row is finished here."""
    store = store or build_store(settings)
    scanner = scanner or build_scanner(settings)
    ocr = ocr or build_ocr(settings)
    payload: dict[str, Any] = job.input or {}
    try:
        doc_id = uuid.UUID(str(payload["document_id"]))
    except (KeyError, ValueError):
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="The job has no valid document.")
        db.commit()
        return "failed"
    target_version = int(payload.get("text_version", 1))

    doc = repo.get_by_id(db, doc_id)
    if doc is None or doc.removed_at is not None or doc.storage_key is None:
        return _finish_skipped(db, job, "document_gone")
    if doc.parse_status == "parsed" and doc.text_version >= target_version:
        return _finish_skipped(db, job, "already_processed")  # redelivery after a successful run

    doc.parse_status, doc.processing_job_id = "processing", job.id
    doc.row_version += 1
    document_service.recompute_full_text_status(db, doc.paper_id)
    db.commit()
    progress = _Progress(db, job)
    progress.set(10, "Fetching the file")

    try:
        with tempfile.TemporaryDirectory(prefix="srs-doc-") as tmp:
            path = Path(tmp) / "document.pdf"
            _fetch_and_verify(store, doc, path)
            scan_warning = _scan(db, store, scanner, settings, doc, path, progress)
            progress.set(30, "Reading pages")
            extraction = pipeline.process(path, settings, ocr, on_tick=progress.tick)
            progress.set(85, "Matching to papers")
            outcome, mismatch = _match(db, doc, extraction)
    except pipeline.ProcessingFailure as f:
        return _fail(db, job, doc_id, f)

    progress.set(95, "Saving")
    return _commit_result(db, job, doc_id, target_version, extraction, outcome, mismatch, scan_warning)


def _finish_skipped(db: Session, job: Job, why: str) -> str:
    job_repo.finish(db, job.id, fsm.RUNNING, fsm.SUCCEEDED, result={"outcome": "skipped", "reason": why})
    db.commit()
    return "skipped"


def _fetch_and_verify(store: ObjectStore, doc: PaperDocument, path: Path) -> None:
    assert doc.storage_key is not None
    try:
        store.download_to(doc.storage_key, path)
    except ObjectNotFound:
        raise pipeline.ProcessingFailure(
            "file_missing", "The stored file is missing. Upload it again.", retryable=False
        ) from None
    except StorageError:
        raise pipeline.ProcessingFailure(
            "storage_unavailable", "The stored file could not be read just now. Use Retry in a moment."
        ) from None
    if _sha256(path) != doc.sha256:
        raise pipeline.ProcessingFailure(
            "integrity_mismatch",
            "The stored file does not match the checksum recorded at upload, so it was not read. Upload it again.",
            retryable=False,
        )


def _scan(
    db: Session,
    store: ObjectStore,
    scanner: MalwareScanner,
    settings: Settings,
    doc: PaperDocument,
    path: Path,
    progress: _Progress,
) -> dict[str, Any] | None:
    progress.set(15, "Scanning for malware")
    result = scanner.scan(path)
    doc.scan_status, doc.scan_detail = result.status, (result.detail or None)
    if result.status == "infected":
        key, doc.storage_key, doc.purged_at = doc.storage_key, None, _now()
        db.commit()
        if key:
            try:
                store.delete(key)
            except StorageError:
                log.warning("could not delete an infected file; the orphan sweep will remove it")
        raise pipeline.ProcessingFailure(
            "malware_detected",
            "The malware scanner flagged this file, so it was deleted. Do not open it.",
            retryable=False,
        )
    if result.status == "error":
        if settings.malware_scan_required:
            raise pipeline.ProcessingFailure(
                "scan_unavailable", "The malware scanner could not check this file, so it was not read. Use Retry."
            )
        return {"code": "scan_failed", "message": "The malware scan could not be completed for this file."}
    db.commit()
    return None


def _match(db: Session, doc: PaperDocument, ex: pipeline.Extraction) -> tuple[matching.MatchOutcome | None, str | None]:
    """Matching only for documents the user has not already linked. For a user-linked one, only a consistency check."""
    if doc.match_status not in ("unmatched", "needs_confirmation", "user_linked"):
        return None, None
    papers = repo.project_papers(db, doc.project_id)
    facts = paper_facts(papers)
    dfacts = document_facts(ex)
    if doc.match_status == "user_linked":
        linked = next((f for f in facts if f.id == str(doc.paper_id)), None)
        return None, matching.consistency_warning(dfacts, linked, facts) if linked else None
    return matching.match(dfacts, facts), None


def _apply_match(doc: PaperDocument, outcome: matching.MatchOutcome) -> None:
    now = _now()
    doc.match_method, doc.match_score = outcome.method, outcome.score
    doc.match_candidates = outcome.candidates
    if outcome.status == "auto_matched" and outcome.paper_id:
        doc.paper_id, doc.match_status = uuid.UUID(outcome.paper_id), "auto_matched"
        doc.matched_by, doc.matched_at = None, now
    elif outcome.status == "needs_confirmation":
        doc.paper_id, doc.match_status = None, "needs_confirmation"
    else:
        doc.paper_id, doc.match_status = None, "unmatched"


def _commit_result(
    db: Session,
    job: Job,
    doc_id: uuid.UUID,
    target_version: int,
    ex: pipeline.Extraction,
    outcome: matching.MatchOutcome | None,
    mismatch: str | None,
    scan_warning: dict[str, Any] | None,
) -> str:
    doc = repo.get_by_id(db, doc_id, lock=True)
    if doc is None or doc.removed_at is not None:
        db.rollback()
        return _finish_skipped(db, job, "document_removed_meanwhile")
    old_paper = doc.paper_id
    repo.insert_pages(
        db,
        doc,
        target_version,
        [
            {
                "page_no": p.page_no,
                "text": p.text,
                "extraction": p.extraction,
                "char_count": p.char_count,
                "ocr_confidence": p.ocr_confidence,
                "warnings": p.warnings,
            }
            for p in ex.pages
        ],
    )
    warnings = list(ex.warnings)
    if scan_warning:
        warnings.append(scan_warning)
    if mismatch:
        warnings.append({"code": "content_may_not_match", "message": mismatch})
    if outcome is not None:
        _apply_match(doc, outcome)
    doc.parse_status, doc.text_version = "parsed", target_version
    doc.error_code = doc.error_message = None
    doc.error_retryable = True
    doc.page_count = ex.page_count
    doc.pages_text, doc.pages_ocr = ex.count("text"), ex.count("ocr")
    doc.pages_blank, doc.pages_unreadable = ex.count("blank"), ex.count("unreadable")
    doc.pdf_metadata, doc.warnings = ex.metadata, warnings
    doc.processed_at = doc.updated_at = _now()
    doc.row_version += 1
    db.flush()
    document_service.recompute_full_text_status(db, old_paper)
    document_service.recompute_full_text_status(db, doc.paper_id)
    audit_service.record(
        db,
        action="document.processed",
        entity_type="document",
        entity_id=doc.id,
        actor_id=None,
        project_id=doc.project_id,
        after={
            "page_count": ex.page_count,
            "pages_ocr": doc.pages_ocr,
            "pages_unreadable": doc.pages_unreadable,
            "match_status": doc.match_status,
            "text_version": target_version,
        },
    )
    job_repo.finish(
        db,
        job.id,
        fsm.RUNNING,
        fsm.SUCCEEDED,
        result={"outcome": "parsed", "pages": ex.page_count, "match": doc.match_status},
    )
    db.commit()
    return "parsed"


def _fail(db: Session, job: Job, doc_id: uuid.UUID, f: pipeline.ProcessingFailure) -> str:
    db.rollback()
    doc = repo.get_by_id(db, doc_id, lock=True)
    if doc is not None:
        if doc.text_version >= 1 and doc.removed_at is None and f.code not in {"malware_detected"}:
            # a failed re-processing keeps the text that was extracted before
            doc.parse_status = "parsed"
            doc.warnings = [w for w in doc.warnings if w.get("code") != "reprocess_failed"] + [
                {"code": "reprocess_failed", "message": f"Re-reading this document failed: {f.message}"}
            ]
        else:
            doc.parse_status = "failed"
            doc.error_code, doc.error_message, doc.error_retryable = f.code, f.message, f.retryable
        doc.row_version += 1
        doc.updated_at = _now()
        db.flush()
        document_service.recompute_full_text_status(db, doc.paper_id)
        audit_service.record(
            db,
            action="document.processing_failed",
            entity_type="document",
            entity_id=doc.id,
            actor_id=None,
            project_id=doc.project_id,
            after={"code": f.code},
        )
    job_repo.finish(
        db, job.id, fsm.RUNNING, fsm.FAILED, result={"outcome": "failed", "code": f.code}, error=f.message[:200]
    )
    db.commit()
    return "failed"


def on_crash(db: Session, job: Job, error: Exception) -> None:
    """The task wrapper calls this after an UNEXPECTED exception (a bug, not a bad file): the document must not stay
    'processing'. Called after db.rollback()."""
    payload = job.input or {}
    try:
        doc_id = uuid.UUID(str(payload.get("document_id")))
    except ValueError:
        return
    doc = repo.get_by_id(db, doc_id)
    if doc is None:
        return
    if doc.text_version >= 1:
        document_service.sweep_reconcile(db, {"document_id": str(doc_id)})  # keeps the earlier text
    else:
        repo.mark_failed(
            db,
            doc_id,
            code="internal_error",
            message=f"Processing failed unexpectedly ({type(error).__name__}). Use Retry; report it if it persists.",
        )
    document_service.recompute_full_text_status(db, doc.paper_id)
    db.commit()
