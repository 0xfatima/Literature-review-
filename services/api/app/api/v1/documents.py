"""Uploaded PDFs (full texts the user is authorised to use). Every route depends on get_owned_project (404 for
foreign projects); document ids are looked up inside that project only. Reading runs in the worker (`document.process`).

Upload contract (ADR A13.1): `POST /projects/{id}/documents[?paper_id=...]` with the file as the RAW request body,
`Content-Type: application/pdf`, and the original file name percent-encoded in the `X-Filename` header. Not multipart:
the size cap is enforced while streaming, and browsers can report real upload progress.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Any, BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dispatcher, get_owned_project, get_rate_limiter
from app.core.config import Settings, get_settings
from app.core.rate_limit import RateLimiter
from app.db.models import Project, User
from app.db.session import get_db
from app.documents import intake, validation
from app.providers.storage import ObjectStore, build_store
from app.schemas.documents import DocumentList, DocumentOut, DocumentSummary, LinkRequest, PageList
from app.services import document_service

router = APIRouter(prefix="/projects/{project_id}", tags=["documents"])
Dispatch = Callable[[str, str], None]
DOWNLOAD_CHUNK = 256 * 1024


@lru_cache
def _default_store() -> ObjectStore:
    return build_store(get_settings())


def get_object_store() -> ObjectStore:
    """Overridable in tests (app.dependency_overrides). The API and the worker must point at the same storage."""
    return _default_store()


def _out(db: Session, settings: Settings, doc: Any) -> DocumentOut:
    return DocumentOut(**document_service.views(db, settings, [doc])[0])


@router.get("/documents/summary", response_model=DocumentSummary)
def documents_summary(
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentSummary:
    return DocumentSummary(**document_service.summary(db, settings, user, project))


@router.post("/documents", response_model=DocumentOut, status_code=202)
async def upload_document(
    request: Request,
    paper_id: uuid.UUID | None = None,
    x_filename: str | None = Header(default=None),
    content_length: int | None = Header(default=None),
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store: ObjectStore = Depends(get_object_store),
    dispatch: Dispatch = Depends(get_dispatcher),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> DocumentOut:
    """Upload one PDF. Answers 202: the file is stored and queued; read `parse_status` for the outcome."""
    filename = validation.sanitize_filename(validation.decode_filename_header(x_filename))
    try:
        validation.check_extension(filename)
        validation.check_declared_type(request.headers.get("content-type"))
        received = await intake.receive(
            request.stream(), max_bytes=settings.upload_max_bytes, declared_length=content_length
        )
    except validation.UploadRejection as r:
        raise document_service.rejection_to_error(r) from None
    try:
        doc = await run_in_threadpool(
            document_service.register_upload,
            db,
            settings,
            store,
            dispatch,
            limiter,
            user,
            project,
            received=received,
            filename=filename,
            paper_id=paper_id,
        )
        return await run_in_threadpool(_out, db, settings, doc)
    finally:
        received.cleanup()


@router.get("/documents", response_model=DocumentList)
def list_documents(
    status: str | None = Query(None, max_length=16),
    match: str | None = Query(None, max_length=24),
    paper_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentList:
    return DocumentList(
        **document_service.list_(
            db,
            settings,
            project,
            parse_status=status,
            match_status=match,
            paper_id=paper_id,
            text=q,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
def read_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentOut:
    return DocumentOut(**document_service.detail(db, settings, project, document_id))


@router.get("/documents/{document_id}/pages", response_model=PageList)
def read_pages(
    document_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=50),
    project: Project = Depends(get_owned_project),
    db: Session = Depends(get_db),
) -> PageList:
    """Extracted text with page numbers (the document's current extraction)."""
    return PageList(**document_service.pages(db, project, document_id, offset, limit))


def _chunks(handle: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := handle.read(DOWNLOAD_CHUNK):
            yield chunk
    finally:
        handle.close()


def content_disposition(filename: str) -> str:
    """Attachment header with an ASCII fallback and the UTF-8 name (RFC 6266 / 5987). The name is already sanitised."""
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in filename) or "document.pdf"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: ObjectStore = Depends(get_object_store),
) -> StreamingResponse:
    """The original file, as an ATTACHMENT only (never rendered inline by this origin), for the owner."""
    doc, handle = document_service.open_for_download(db, store, user, project, document_id)
    return StreamingResponse(
        _chunks(handle),
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(doc.original_filename),
            "Content-Length": str(doc.size_bytes),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/documents/{document_id}/retry", response_model=DocumentOut, status_code=202)
def retry_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    dispatch: Dispatch = Depends(get_dispatcher),
) -> DocumentOut:
    """Read a failed document again (or a parsed one that has unreadable pages, once OCR is available)."""
    return _out(db, settings, document_service.retry(db, settings, dispatch, user, project, document_id))


@router.post("/documents/{document_id}/link", response_model=DocumentOut)
def link_document(
    document_id: uuid.UUID,
    body: LinkRequest,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentOut:
    """Link the document to a paper (this also confirms a suggested match) or, with paper_id null, unlink it."""
    return _out(
        db, settings, document_service.set_link(db, user, project, document_id, body.paper_id, body.expected_version)
    )


@router.delete("/documents/{document_id}", status_code=204)
def remove_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_owned_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Remove the document from the project. Its file is purged after the retention period."""
    document_service.remove(db, user, project, document_id)
    return Response(status_code=204)
