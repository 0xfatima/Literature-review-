"""Helpers for search and screening integration tests: run background jobs in-process (real task logic, real
PostgreSQL; only the broker hop is skipped) with provider HTTP mocked by tests/fixtures/scholarly.py."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.providers.llm.base import LLMGateway
from app.repositories import jobs as job_repo
from app.services import document_executor, relevance_service, search_executor
from tests.fakes import no_wait_fetcher
from tests.fixtures.scholarly import ScholarlyTransport

from .test_protocol_workflow import answer, project, started, walk_to_review

OPEN_ACCESS = ["openalex", "europepmc", "arxiv"]


def _take(dispatcher, task: str) -> list[str]:
    pending = [jid for t, jid in dispatcher.sent if t == task]
    dispatcher.sent = [s for s in dispatcher.sent if s[0] != task]
    return pending


def _claimed(jid: str):
    db = get_sessionmaker()()
    if not job_repo.claim(db, uuid.UUID(jid)):
        db.rollback()
        db.close()
        return None, None
    db.commit()
    return db, job_repo.get_by_id(db, uuid.UUID(jid))


def run_search_jobs(dispatcher, transport: ScholarlyTransport | None = None, settings: Settings | None = None):
    transport = transport or ScholarlyTransport()
    outcomes = []
    for jid in _take(dispatcher, "search.run"):
        db, job = _claimed(jid)
        if db is None:
            outcomes.append("ignored")
            continue
        try:
            http = no_wait_fetcher(transport.transport)
            outcomes.append(search_executor.execute(db, job, settings or get_settings(), http=http).status)
        finally:
            db.close()
    return outcomes


def run_relevance_jobs(dispatcher, gateway: LLMGateway | None = None, settings: Settings | None = None):
    outcomes = []
    for jid in _take(dispatcher, "relevance.assess"):
        db, job = _claimed(jid)
        if db is None:
            outcomes.append("ignored")
            continue
        try:
            outcomes.append(
                relevance_service.execute(db, job, settings or get_settings(), gateway=gateway or LLMGateway(None))
            )
        finally:
            db.close()
    return outcomes


def approved(c, dispatcher, title: str = "Exercise review") -> str:
    pid = project(c)
    if title != "Exercise review":
        c.patch(f"/api/v1/projects/{pid}", json={"title": title, "expected_version": 1})
    started(c, dispatcher, pid)
    walk_to_review(c, dispatcher, pid)
    answer(c, dispatcher, pid, "approve")
    return pid


def start_search(c, pid: str, expect: int = 202, **body: Any) -> dict[str, Any]:
    r = c.post(f"/api/v1/projects/{pid}/search-runs", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def searched(c, dispatcher, transport: ScholarlyTransport | None = None, providers=None) -> tuple[str, dict]:
    """An approved project with one completed open-access search (4 unique papers from 6 records)."""
    pid = approved(c, dispatcher)
    run = start_search(c, pid, providers=providers or OPEN_ACCESS)
    assert run_search_jobs(dispatcher, transport) == ["completed"]
    return pid, c.get(f"/api/v1/projects/{pid}/search-runs/{run['id']}").json()


def upload(
    c,
    pid: str,
    data: bytes,
    *,
    name: str = "paper.pdf",
    paper_id: str | None = None,
    content_type: str = "application/pdf",
    expect: int = 202,
) -> dict[str, Any]:
    """Upload a file the way the browser does: raw body, original name percent-encoded in X-Filename."""
    from urllib.parse import quote

    url = f"/api/v1/projects/{pid}/documents" + (f"?paper_id={paper_id}" if paper_id else "")
    r = c.post(url, content=data, headers={"Content-Type": content_type, "X-Filename": quote(name)})
    assert r.status_code == expect, r.text
    return r.json()


def run_document_jobs(dispatcher, store, *, ocr=None, scanner=None, settings: Settings | None = None) -> list[str]:
    """Run every dispatched `document.process` job in this process: real task logic, real PostgreSQL, real storage and
    real PDF parsing child processes. Only the broker hop is skipped."""
    from app.documents.ocr import NullOcr
    from app.providers.malware import NullScanner

    outcomes = []
    for jid in _take(dispatcher, "document.process"):
        db, job = _claimed(jid)
        if db is None:
            outcomes.append("ignored")
            continue
        try:
            outcomes.append(
                document_executor.execute(
                    db,
                    job,
                    settings or get_settings(),
                    store=store,
                    scanner=scanner or NullScanner(),
                    ocr=ocr or NullOcr(),
                )
            )
        finally:
            db.close()
    return outcomes


def papers_by_title(c, pid: str) -> dict[str, dict[str, Any]]:
    items = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    return {p["title"]: p for p in items}
