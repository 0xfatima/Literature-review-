"""Open-access tags, fetching open-access PDFs for papers past title/abstract screening, and search record limits."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import PaperDocument, PaperRecord
from app.db.session import get_sessionmaker
from app.repositories import jobs as job_repo
from app.services import oa_fetch_service
from tests.fixtures import pdfs

from .support import OPEN_ACCESS, _take, start_search
from .test_prisma_extraction import searched, ta_decide


def _papers(c, pid):
    return [p["id"] for p in c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]]


def _set(db, paper_id, **values):
    p = db.get(PaperRecord, uuid.UUID(paper_id))
    for k, v in values.items():
        setattr(p, k, v)
    db.commit()


def test_open_access_pdfs_are_fetched_and_the_rest_need_the_persons_pdf(
    user_client, dispatcher, object_store, monkeypatch, db
):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    p_arxiv, p_closed, p_dead, p_out = _papers(c, pid)[:4]
    for p in (p_arxiv, p_closed, p_dead):
        assert ta_decide(c, pid, p, "include").status_code == 200
    _set(db, p_arxiv, arxiv_id="2401.01234", oa_status="unknown", pdf_url=None, oa_url=None)
    _set(db, p_closed, arxiv_id=None, pmcid=None, oa_status="closed", pdf_url=None, oa_url=None)
    _set(db, p_dead, arxiv_id=None, pmcid=None, oa_status="open", pdf_url="https://example.org/a.pdf", oa_url=None)

    q = c.get(f"/api/v1/projects/{pid}/prisma/ft-queue").json()["items"]
    tags = {i["id"]: i["open_access"] for i in q}
    assert tags[p_arxiv] and tags[p_dead] and not tags[p_closed] and p_out not in tags
    summary = c.get(f"/api/v1/projects/{pid}/fulltext/open-access").json()
    assert summary["needs_your_pdf"] == 1 and summary["fetchable"] == 2

    pdf = pdfs.paper_pdf("An arXiv paper", "Doe J", 2024, None)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "arxiv.org":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        return httpx.Response(200, content=b"<html>login</html>")  # not a PDF: refused

    monkeypatch.setattr(oa_fetch_service, "_public_host", lambda url: None)
    r = c.post(f"/api/v1/projects/{pid}/fulltext/fetch-open-access")
    assert r.status_code == 202, r.text
    [jid] = _take(dispatcher, "fulltext.fetch_oa")
    s = get_sessionmaker()()
    assert job_repo.claim(s, uuid.UUID(jid))
    s.commit()
    job = job_repo.get_by_id(s, uuid.UUID(jid))
    out = oa_fetch_service.execute(
        s,
        job,
        get_settings(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        dispatch=dispatcher,
        store=object_store,
    )
    assert out == "ok"
    s.refresh(job)
    assert job.result["fetched"] == 1 and job.result["failed"] == 1
    assert "not return a PDF" in job.result["failures"][0]["reason"]
    s.close()
    assert any("arxiv.org/pdf/2401.01234" in u for u in seen)
    docs = db.scalars(select(PaperDocument).where(PaperDocument.paper_id == uuid.UUID(p_arxiv))).all()
    assert len(docs) == 1
    assert _take(dispatcher, "document.process")  # the fetched PDF is parsed like any upload


def test_private_addresses_are_never_fetched():
    import pytest

    with pytest.raises(oa_fetch_service.FetchError):
        oa_fetch_service._public_host("http://127.0.0.1/secret.pdf")
    with pytest.raises(oa_fetch_service.FetchError):
        oa_fetch_service._public_host("file:///etc/passwd")


def test_search_record_limits_cap_each_source_and_are_kept_on_the_run(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    plan = c.post(
        f"/api/v1/projects/{pid}/search/plan",
        json={"providers": OPEN_ACCESS, "max_records": 90, "min_records": 20},
    ).json()
    assert plan["max_records_per_provider"] == 30
    assert plan["record_limits"] == {"max_total": 90, "per_provider": 30, "min_total": 20}
    bad = c.post(f"/api/v1/projects/{pid}/search/plan", json={"max_records": 50, "min_records": 80})
    assert bad.status_code == 422
    run = start_search(c, pid, providers=OPEN_ACCESS, max_records=90, min_records=20)
    assert run["filters"]["record_limits"]["per_provider"] == 30
