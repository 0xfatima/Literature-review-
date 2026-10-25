"""Phase 9 integration tests: AI evidence extraction with provenance, the consent gates, human review, revisions,
the evidence table, CSV exports and stale-output tracking. Real PostgreSQL and real PDF parsing; the AI provider is a
deterministic fake and the broker hop is skipped."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import DocumentPage, LlmConsent, ModelRun, PaperDocument
from app.providers.llm.base import LLMGateway, LLMResult
from app.services import consent_service, extraction_service
from tests.fixtures.pdfs import PARA, make_pdf

from .support import _claimed, _take, run_document_jobs, searched, upload

FIELDS = [
    {"field_key": "n", "label": "Sample size", "type": "number"},
    {"field_key": "design", "label": "Design", "type": "choice", "options": ["RCT", "Cohort"]},
    {"field_key": "funding", "label": "Funding", "type": "text"},
]
GOOD = {
    "n": {"status": "found", "value": "120", "page": 1, "quote": "enrolled 120 adult participants"},
    "design": {"status": "found", "value": "RCT", "page": 1, "quote": "This RCT enrolled"},
    # quote does not occur in the paper: must be discarded, never stored as evidence
    "funding": {"status": "found", "value": "NIH", "page": 1, "quote": "funded by the NIH"},
}


class DocLLM:
    name = "fake"
    model = "fake-model-1"

    def __init__(self, answers: dict[str, dict[str, Any]]):
        self.answers = answers
        self.prompts: list[str] = []

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        self.prompts.append(prompt)
        fields = [{"field_key": k, **v} for k, v in self.answers.items() if f"- {k}:" in prompt]
        return LLMResult("fake", self.model, {"fields": fields}, input_tokens=10, output_tokens=5, duration_ms=3)


def gateway(llm: DocLLM) -> LLMGateway:
    return LLMGateway(llm, send_private_documents=True, terms_reviewed=True)


def run_extraction(dispatcher, gw: LLMGateway) -> list[str]:
    outcomes = []
    for jid in _take(dispatcher, "extraction.run"):
        db, job = _claimed(jid)
        if db is None:
            outcomes.append("ignored")
            continue
        try:
            outcomes.append(extraction_service.execute(db, job, get_settings(), gateway=gw))
        finally:
            db.close()
    return outcomes


def consent(db, pid: str, user_id: str) -> None:
    db.add(
        LlmConsent(
            project_id=uuid.UUID(pid),
            user_id=uuid.UUID(user_id),
            provider="fake",
            model="fake-model-1",
            data_class="private_document",
            terms_record_version="test",
            notice_version=consent_service.NOTICE_VERSION,
        )
    )
    db.commit()


def ready(user_client, dispatcher, object_store, db, *, consented: bool = True):
    """Project with two included papers; p1 has a linked, parsed PDF, p2 has no full text; approved schema."""
    c, _, user_id = user_client()
    pid, _ = searched(c, dispatcher)
    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    p1, p2 = papers[0]["id"], papers[1]["id"]
    for p in (p1, p2):
        r = c.post(f"/api/v1/projects/{pid}/screening/papers/{p}/decision", json={"decision": "include"})
        assert r.status_code == 200, r.text
    upload(c, pid, make_pdf(["This RCT enrolled 120 adult participants. " + PARA.format(n=50) * 3]), paper_id=p1)
    assert run_document_jobs(dispatcher, object_store) == ["parsed"]
    sid = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": FIELDS}).json()["id"]
    assert c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={}).status_code == 200
    if consented:
        consent(db, pid, str(user_id))
    return c, pid, sid, p1, p2


def table(c, pid, sid) -> dict[str, dict[str, Any]]:
    rows = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/table").json()["rows"]
    return {r["paper"]["id"]: r["cells"] for r in rows}


def start(c, pid, sid, **body) -> dict[str, Any]:
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/runs", json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_extraction_stores_verified_values_marks_unavailable_and_discards_unverified(
    user_client, dispatcher, object_store, db
):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db)
    start(c, pid, sid)
    llm = DocLLM(GOOD)
    assert run_extraction(dispatcher, gateway(llm)) == ["ok"]
    assert len(llm.prompts) == 1 and "=== PAGE 1 ===" in llm.prompts[0]

    cells = table(c, pid, sid)
    n = cells[p1]["n"]
    assert n["status"] == "found" and n["value"] == 120 and n["page_no"] == 1
    assert n["origin"] == "ai" and n["review_status"] == "unreviewed" and n["model_run_id"]
    assert "120 adult participants" in n["excerpt"] and n["pages_inspected"] == [1]
    assert cells[p1]["design"]["value"] == "RCT"
    funding = cells[p1]["funding"]
    assert funding["status"] == "pending" and funding["value"] is None and funding["excerpt"] is None
    assert funding["validation"][0]["code"] == "quote_not_in_source"
    assert {k: v["status"] for k, v in cells[p2].items()} == dict.fromkeys(("n", "design", "funding"), "unavailable")
    assert all(v["origin"] == "system" for v in cells[p2].values())

    runs = list(db.scalars(select(ModelRun).where(ModelRun.project_id == uuid.UUID(pid))))
    assert [r.data_class for r in runs if r.purpose == "evidence_extraction"] == ["private_document"]

    h = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n/history").json()
    assert [r["action"] for r in h["revisions"]] == ["ai_extracted"]
    assert h["source_page"]["page_no"] == 1 and "120 adult participants" in h["source_page"]["text"]
    assert h["source_page"]["is_current_text"] and h["model_run"]["model"] == "fake-model-1"

    status = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/runs").json()
    assert status["last_result"]["found"] == 2 and status["last_result"]["discarded"] == 1
    cov = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/coverage").json()
    by_key = {f["field_key"]: f for f in cov["unmapped_fields"]}
    assert by_key["n"]["found"] == 1 and by_key["n"]["unavailable"] == 1


def test_no_document_text_is_sent_without_consent(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    start(c, pid, sid)
    llm = DocLLM(GOOD)
    assert run_extraction(dispatcher, gateway(llm)) == ["ok"]
    assert llm.prompts == []
    status = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/runs").json()
    assert status["last_result"]["stop_reason"] == "refused" and status["last_result"]["papers_blocked"] == 1
    cells = table(c, pid, sid)
    assert cells[p1] == {}  # nothing guessed
    assert cells[p2]["n"]["status"] == "unavailable"  # deterministic marking still happens


def test_operator_gates_refuse_documents_even_with_consent(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, _ = ready(user_client, dispatcher, object_store, db)
    start(c, pid, sid)
    llm = DocLLM(GOOD)
    run_extraction(dispatcher, LLMGateway(llm, send_private_documents=True, terms_reviewed=False))
    assert llm.prompts == [] and table(c, pid, sid)[p1] == {}


def test_reviewed_values_are_never_overwritten_and_rerun_replaces_unreviewed_ones(
    user_client, dispatcher, object_store, db
):
    c, pid, sid, p1, _ = ready(user_client, dispatcher, object_store, db)
    base = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}"
    start(c, pid, sid)
    run_extraction(dispatcher, gateway(DocLLM(GOOD)))
    r = c.post(f"{base}/n/approve", json={})
    assert r.status_code == 200 and r.json()["review_status"] == "approved"
    r = c.put(f"{base}/design", json={"status": "not_located", "reason": "Design not stated in methods"})
    assert r.status_code == 200 and r.json()["origin"] == "manual" and r.json()["review_status"] == "edited"

    other = {
        "n": {"status": "found", "value": "50", "page": 1, "quote": "randomised 50 participants"},
        "design": {"status": "found", "value": "Cohort", "page": 1, "quote": "This RCT enrolled"},
        "funding": {"status": "not_located"},
    }
    start(c, pid, sid, force=True)
    llm = DocLLM(other)
    assert run_extraction(dispatcher, gateway(llm)) == ["ok"]
    assert "- funding:" in llm.prompts[0] and "- n:" not in llm.prompts[0] and "- design:" not in llm.prompts[0]
    cells = table(c, pid, sid)[p1]
    assert cells["n"]["value"] == 120 and cells["n"]["review_status"] == "approved"
    assert cells["design"]["status"] == "not_located" and cells["design"]["origin"] == "manual"
    assert cells["funding"]["status"] == "not_located" and cells["funding"]["pages_inspected"] == [1]
    actions = [r["action"] for r in c.get(f"{base}/n/history").json()["revisions"]]
    assert actions == ["ai_extracted", "approved"]
    design_hist = c.get(f"{base}/design/history").json()["revisions"]
    assert design_hist[-1]["before"]["value"] == "RCT" and design_hist[-1]["reason"] == "Design not stated in methods"


def test_manual_citation_must_be_a_page_of_this_papers_document(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    doc = db.scalar(select(PaperDocument).where(PaperDocument.paper_id == uuid.UUID(p1)))
    page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.page_no == 1))
    base = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence"
    body = {"status": "found", "value": 120, "excerpt": "120 adult participants"}
    r = c.put(f"{base}/{p2}/n", json={**body, "document_page_id": str(page.id)})
    assert r.status_code == 422 and "not linked to this paper" in r.text
    r = c.put(f"{base}/{p1}/n", json={**body, "document_id": str(doc.id), "page_no": 1})
    assert r.status_code == 200, r.text
    assert r.json()["page_no"] == 1 and r.json()["document_id"] == str(doc.id) and r.json()["validation"] == []
    r = c.put(f"{base}/{p1}/design", json={"status": "ambiguous"})
    assert r.status_code == 422  # ambiguity must be explained
    r = c.post(f"{base}/{p1}/funding/approve", json={})
    assert r.status_code == 404


def test_export_records_claim_revisions_and_later_edits_make_it_stale(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, _ = ready(user_client, dispatcher, object_store, db)
    start(c, pid, sid)
    run_extraction(dispatcher, gateway(DocLLM(GOOD)))
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/exports")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    output_id = r.headers["x-evidence-output-id"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    row = next(x for x in rows if x["paper_id"] == p1)
    assert row["Sample size"] == "120" and row["Sample size [page]"] == "1" and row["Funding [status]"] == "pending"

    base = f"/api/v1/projects/{pid}/extraction/schemas/{sid}"
    h = c.get(f"{base}/evidence/{p1}/n/history").json()
    assert h["outputs"] == [{**h["outputs"][0], "id": output_id, "stale": False}]
    assert c.get(f"{base}/affected").json()["stale_outputs"] == []
    c.put(f"{base}/evidence/{p1}/n", json={"status": "not_located"})
    assert c.get(f"{base}/evidence/{p1}/n/history").json()["outputs"][0]["stale"] is True
    stale = c.get(f"{base}/affected").json()["stale_outputs"]
    assert [(s["id"], s["changed_items"]) for s in stale] == [(output_id, 1)]


def test_draft_schema_cannot_be_extracted_or_exported_and_new_version_reports_diff(
    user_client, dispatcher, object_store, db
):
    c, pid, sid, _, _ = ready(user_client, dispatcher, object_store, db, consented=False)
    fields2 = [*FIELDS[:2], {"field_key": "outcome", "label": "Outcome", "type": "text"}]
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": fields2})
    sid2 = r.json()["id"]
    assert c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid2}/runs", json={}).status_code == 409
    assert c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid2}/exports").status_code == 409
    c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid2}/approve", json={})
    sup = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid2}/affected").json()["superseded_schema"]
    assert sup["schema_id"] == sid and sup["diff"]["added"] == ["outcome"] and sup["diff"]["removed"] == ["funding"]
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid2}/runs", json={"paper_ids": [str(uuid.uuid4())]})
    assert r.status_code == 422


def test_revisions_are_append_only_at_the_database_level(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, _ = ready(user_client, dispatcher, object_store, db, consented=False)
    c.put(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n", json={"status": "not_located"})
    for sql in ("UPDATE evidence_item_revisions SET action = 'tampered'", "DELETE FROM evidence_item_revisions"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(sql))
            db.commit()
        db.rollback()


def test_consent_requires_operator_gates_and_current_notice(user_client, set_settings):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    url = f"/api/v1/projects/{pid}/llm-consent"
    set_settings(llm_provider="gemini", llm_api_key="k", llm_model="m", llm_send_private_documents=True)
    assert c.post(url, json={"notice_version": consent_service.NOTICE_VERSION}).status_code == 409  # no terms record
    set_settings(
        llm_provider="gemini",
        llm_api_key="k",
        llm_model="m",
        llm_send_private_documents=True,
        llm_terms_record_version="gemini-terms-1",
        llm_terms_reviewed_on=datetime.now(UTC).date().isoformat(),
    )
    s = c.get(url).json()
    assert not s["can_send_documents"] and "consented" in s["blocked_reason"] and s["terms_reviewed"]
    assert c.post(url, json={"notice_version": "old"}).status_code == 422
    s = c.post(url, json={"notice_version": consent_service.NOTICE_VERSION}).json()
    assert s["can_send_documents"] and s["consent"]["terms_record_version"] == "gemini-terms-1"
    s = c.delete(url).json()
    assert s["consent"] is None and not s["can_send_documents"]
