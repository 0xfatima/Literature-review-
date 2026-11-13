"""Full-text bulk decisions and AI full-text screening of available PDFs (real PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.db.models import LlmConsent
from app.providers.llm import LLMGateway
from app.providers.llm.base import LLMResult
from app.services import consent_service, ft_ai_service
from tests.fixtures.pdfs import make_pdf

from .support import _claimed, _take, run_document_jobs, upload
from .test_prisma_extraction import searched


class ScreenLLM:
    name = "fake"
    model = "fake-model-1"

    def __init__(self, answer):
        self.answer = answer
        self.prompts: list[str] = []

    def generate_json(self, *, system, prompt, schema, temperature, max_output_tokens) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult("fake", self.model, self.answer(prompt), input_tokens=10, output_tokens=5, duration_ms=3)


def _consent(db, pid: str, user_id: str) -> None:
    for provider, model in (("fake", "fake-model-1"), ("gemini", "m")):
        db.add(
            LlmConsent(
                project_id=uuid.UUID(pid),
                user_id=uuid.UUID(user_id),
                provider=provider,
                model=model,
                data_class="private_document",
                terms_record_version="t1",
                notice_version=consent_service.NOTICE_VERSION,
            )
        )
    db.commit()


def _included(c, dispatcher, n=3):
    pid, _ = searched(c, dispatcher)
    papers = [p["id"] for p in c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]][:n]
    for p in papers:
        assert (
            c.post(f"/api/v1/projects/{pid}/screening/papers/{p}/decision", json={"decision": "include"}).status_code
            == 200
        )
    return pid, papers


def _queue(c, pid) -> dict[str, Any]:
    return {i["id"]: i for i in c.get(f"/api/v1/projects/{pid}/prisma/ft-queue").json()["items"]}


def test_bulk_full_text_decisions(user_client, dispatcher):
    c, _, _ = user_client()
    pid, papers = _included(c, dispatcher)
    r = c.post(f"/api/v1/projects/{pid}/prisma/ft-bulk", json={"paper_ids": papers, "decision": "exclude"})
    assert r.status_code == 422  # exclusion needs a reason
    r = c.post(f"/api/v1/projects/{pid}/prisma/ft-bulk", json={"paper_ids": papers, "decision": "include"})
    assert r.status_code == 200 and r.json()["changed"] == 3
    assert {i["status"] for i in _queue(c, pid).values()} == {"included"}


def test_ai_only_includes_reads_pdfs_ready_at_the_click_and_never_rereads_what_it_left(
    user_client, dispatcher, object_store, db, set_settings
):
    c, _, user_id = user_client()
    pid, (p1, p2, p3) = _included(c, dispatcher)
    upload(c, pid, make_pdf(["This randomised trial enrolled 120 adults with depression. " * 20]), paper_id=p1)
    upload(c, pid, make_pdf(["A case report of one child with asthma treated at home. " * 20]), paper_id=p2)
    assert run_document_jobs(dispatcher, object_store) == ["parsed", "parsed"]
    set_settings(
        llm_provider="gemini",
        llm_api_key="k",
        llm_model="m",
        llm_send_private_documents=True,
        llm_terms_record_version="t1",
        llm_terms_reviewed_on=datetime.now(UTC).date().isoformat(),
    )
    st = c.get(f"/api/v1/projects/{pid}/fulltext/ai-screen").json()
    assert st["readable"] == 2 and not st["consent"]["can_send_documents"]
    assert c.post(f"/api/v1/projects/{pid}/fulltext/ai-screen").status_code == 409  # no consent yet
    _consent(db, pid, str(user_id))
    r = c.post(f"/api/v1/projects/{pid}/fulltext/ai-screen")
    assert r.status_code == 202, r.text
    # a PDF that arrives after the click is not read by this run
    upload(c, pid, make_pdf(["A later randomised trial of adults with depression. " * 20]), paper_id=p3)
    assert run_document_jobs(dispatcher, object_store) == ["parsed"]

    def answer(prompt: str) -> dict[str, Any]:
        if "randomised trial" in prompt:
            return {"decision": "include", "reason": "Adults with depression in an RCT."}
        return {"decision": "not_included", "reason": "A case report."}

    llm = ScreenLLM(answer)
    gw = LLMGateway(llm, send_private_documents=True, terms_reviewed=True)
    [jid] = _take(dispatcher, "fulltext.ai_screen")
    s, job = _claimed(jid)
    assert ft_ai_service.execute(s, job, get_settings(), gateway=gw) == "ok"
    s.close()
    assert len(llm.prompts) == 2  # p3's PDF arrived after the click: not read by this run
    q = _queue(c, pid)
    # The AI only includes: what it does not include stays awaiting the person's decision.
    assert q[p1]["status"] == "included" and q[p2]["status"] == "awaiting" and q[p3]["status"] == "awaiting"
    st = c.get(f"/api/v1/projects/{pid}/fulltext/ai-screen").json()
    assert [x["paper_id"] for x in st["chosen"]] == [p1] and st["left_for_you"] == 1
    # The next click reads only the PDF that arrived later (p3), never p2 again (already read and left).
    st = c.get(f"/api/v1/projects/{pid}/fulltext/ai-screen").json()
    assert st["readable"] == 1
    assert c.post(f"/api/v1/projects/{pid}/fulltext/ai-screen").status_code == 202
    [jid] = _take(dispatcher, "fulltext.ai_screen")
    s, job = _claimed(jid)
    llm.prompts.clear()
    assert ft_ai_service.execute(s, job, get_settings(), gateway=gw) == "ok"
    s.close()
    assert len(llm.prompts) == 1 and "later randomised trial" in llm.prompts[0]
    q = _queue(c, pid)
    assert q[p3]["status"] == "included" and q[p2]["status"] == "awaiting"
