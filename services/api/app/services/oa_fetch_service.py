"""Fetch the full texts of open-access papers that passed title/abstract screening.

For every paper awaiting its full-text decision that has no document yet and is open access (a provider said so, or
it is an arXiv preprint or in PubMed Central), the worker tries its PDF links in order (app.services.open_access) and
stores the first real PDF exactly like an upload linked to that paper (document_service.register_upload): the same
size limits, duplicate check, quotas, parsing and audit. Papers that are not open access are left for the person.

Safety: the URLs come from third-party metadata, so every request (and every redirect) must be http(s) to a host
that resolves only to public addresses; responses must start with the PDF signature and stay under the upload size
limit. Nothing is executed or rendered; the document goes through the normal parsing pipeline.

Cancellable between papers; work done so far is kept, and a new run skips papers that already have a document.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import shutil
import socket
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, Conflict
from app.db.models import Job, PaperRecord, Project, User
from app.documents.intake import ReceivedUpload
from app.documents.validation import UploadRejection, check_signature
from app.jobs import fsm
from app.repositories import jobs as job_repo
from app.services import audit_service, job_service, open_access
from app.services.job_service import Dispatcher

log = logging.getLogger(__name__)
JOB_TYPE = "fulltext.fetch_oa"
MAX_REDIRECTS = 4
TIMEOUT_S = 40.0


class FetchError(Exception):
    pass


def candidates(db: Session, project: Project) -> list[PaperRecord]:
    """Open-access papers awaiting a full-text decision (or marked uncertain) that have no document yet."""
    from app.prisma import service as prisma_service

    queue = prisma_service.ft_queue(db, project.id)
    ids = [uuid.UUID(i["id"]) for i in queue["items"] if i["status"] in ("awaiting", "uncertain")]
    if not ids:
        return []
    papers = db.query(PaperRecord).filter(PaperRecord.id.in_(ids)).all()
    return [p for p in papers if p.full_text_status == "not_obtained" and open_access.pdf_candidates(p)]


def summary(db: Session, project: Project) -> dict[str, Any]:
    from app.prisma import service as prisma_service

    items = [i for i in prisma_service.ft_queue(db, project.id)["items"] if i["status"] in ("awaiting", "uncertain")]
    no_pdf = [i for i in items if i["full_text_status"] == "not_obtained"]
    return {
        "awaiting": len(items),
        "without_pdf": len(no_pdf),
        "open_access_without_pdf": sum(1 for i in no_pdf if i.get("open_access")),
        "needs_your_pdf": sum(1 for i in no_pdf if not i.get("open_access")),
        "fetchable": len(candidates(db, project)),
    }


def start(db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project) -> Job:
    from app.services import protocol_service

    protocol_service.require_writable(project)
    if not candidates(db, project):
        raise Conflict(
            "There are no open-access papers without a PDF to fetch. Upload the remaining PDFs yourself.",
            code="nothing_to_fetch",
        )
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=project.id,
        payload={"nonce": str(uuid.uuid4())},
        request_key=None,
    )
    return job


# ---------- download ----------


def _public_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("not an http(s) link")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as e:
        raise FetchError("the host could not be found") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("the link points to a private network address")


def download(url: str, *, max_bytes: int, client: httpx.Client) -> ReceivedUpload:
    """Download one PDF to a private temp file, following redirects only to public hosts."""
    directory = Path(tempfile.mkdtemp(prefix="srs-oa-"))
    path = directory / "upload.pdf"
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            _public_host(current)
            with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"the server answered HTTP {resp.status_code}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("the PDF is larger than the upload limit")
                digest = hashlib.sha256()
                size, head, tail = 0, b"", b""
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    for chunk in resp.iter_bytes():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError("the PDF is larger than the upload limit")
                        if len(head) < 1024:
                            head = (head + chunk)[:1024]
                        tail = (tail + chunk)[-1024:]
                        digest.update(chunk)
                        out.write(chunk)
                if size == 0:
                    raise FetchError("the server sent an empty file")
                try:
                    check_signature(head)
                except UploadRejection as e:
                    raise FetchError("the link did not return a PDF (often a web page or a login wall)") from e
                return ReceivedUpload(directory, path, size, digest.hexdigest(), head, tail)
        raise FetchError("too many redirects")
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


class _NoLimit:
    """Fetched files are not user uploads: the per-user upload rate limit does not apply (quotas still do)."""

    def hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        return True, 0


def execute(
    db: Session,
    job: Job,
    settings: Settings,
    *,
    client: httpx.Client | None = None,
    dispatch: Dispatcher | None = None,
    store: Any = None,
) -> str:
    from app.providers.storage import build_store
    from app.services import document_service

    if dispatch is None:
        from app.jobs.celery_app import send_task

        dispatch = send_task

    project = db.get(Project, job.project_id)
    owner = db.get(User, job.owner_id)
    if project is None or owner is None:
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="The project no longer exists.")
        db.commit()
        return "failed"
    todo = candidates(db, project)
    store = store or build_store(settings)
    agent = "SystematicReviewScientist/1.0" + (
        f" (mailto:{settings.scholarly_contact_email})" if settings.scholarly_contact_email else ""
    )
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": agent, "Accept": "application/pdf"})
    fetched: list[str] = []
    failed: list[dict[str, str]] = []
    stop = None
    try:
        for i, paper in enumerate(todo):
            db.refresh(job)
            if job.cancel_requested:
                stop = "cancelled"
                break
            job_repo.set_progress(
                db, job.id, int(100 * i / max(len(todo), 1)), f"Fetching PDF {i + 1} of {len(todo)}: {paper.title[:80]}"
            )
            db.commit()
            reason = "no link worked"
            for url in open_access.pdf_candidates(paper):
                try:
                    received = download(url, max_bytes=settings.upload_max_bytes, client=http)
                except (FetchError, httpx.HTTPError) as e:
                    reason = str(e) if isinstance(e, FetchError) else f"network error ({type(e).__name__})"
                    continue
                try:
                    document_service.register_upload(
                        db,
                        settings,
                        store,
                        dispatch,
                        _NoLimit(),
                        owner,
                        project,
                        received=received,
                        filename=f"{(paper.title or 'paper')[:80]}.pdf",
                        paper_id=paper.id,
                    )
                    fetched.append(str(paper.id))
                    reason = ""
                    break
                except AppError as e:
                    db.rollback()
                    reason = e.message
                    break
                finally:
                    received.cleanup()
            if reason:
                failed.append({"paper_id": str(paper.id), "title": (paper.title or "")[:200], "reason": reason})
    finally:
        if own:
            http.close()
    result = {
        "fetched": len(fetched),
        "failed": len(failed),
        "failures": failed[:100],
        "message": (
            f"Fetched {len(fetched)} open-access PDF{'s' if len(fetched) != 1 else ''}."
            + (f" {len(failed)} could not be fetched automatically; upload those yourself." if failed else "")
        ),
    }
    audit_service.record(
        db,
        action="document.oa_fetched",
        entity_type="job",
        entity_id=job.id,
        actor_id=None,
        project_id=project.id,
        after={"fetched": len(fetched), "failed": len(failed)},
    )
    target = fsm.CANCELLED if stop == "cancelled" else fsm.SUCCEEDED
    job_repo.finish(db, job.id, fsm.RUNNING, target, result=result)
    db.commit()
    return "cancelled" if stop else "ok"
