"""Phase 11 integration tests: report drafts generated from stored review data, citations linked to stored records,
validation (counts, citations, unsupported claims), immutable versions with edits and verification, exports
(DOCX, Markdown, supplementary ZIP, PRISMA JSON) recorded with hash and review state, and claim tracing."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from app.providers.llm.base import LLMGateway

from .test_appraisal_synthesis import _draft, approve_appraisals, reviewed_evidence
from .test_evidence_extraction import ready


def accepted_project(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    reviewed_evidence(c, pid, sid, p1, p2)
    approve_appraisals(c, pid, p1, p2)
    v = _draft(c, pid, dispatcher, LLMGateway(None))
    base = f"/api/v1/projects/{pid}/synthesis/versions/{v['id']}"
    for s in v["statements"]:
        c.post(f"{base}/review", json={"action": "accept", "statement_id": s["id"]})
    assert c.post(f"{base}/accept", json={}).status_code == 200
    return c, pid, sid, p1, p2


def blocks(report: dict[str, Any], section: str) -> list[dict[str, Any]]:
    return next(s for s in report["sections"] if s["key"] == section)["blocks"]


def table(report: dict[str, Any], key: str) -> dict[str, Any]:
    return next(b["table"] for s in report["sections"] for b in s["blocks"] if b.get("table", {}).get("key") == key)


def test_report_is_generated_from_stored_data_with_linked_citations(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = accepted_project(user_client, dispatcher, object_store, db)
    flow = c.get(f"/api/v1/projects/{pid}/prisma/flow").json()
    r = c.post(f"/api/v1/projects/{pid}/reports", json={"note": "First draft"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert [s["key"] for s in rep["sections"]] == [
        "title_abstract",
        "introduction",
        "methods",
        "eligibility",
        "results",
        "synthesis",
        "discussion",
        "conclusion",
        "references",
        "supplementary",
    ]
    assert {ref["paper_id"] for ref in rep["references"]} == {p1, p2}
    results_text = blocks(rep, "results")[0]["text"]
    assert f"{flow['included_studies']} studies were included" in results_text
    prisma = dict(table(rep, "prisma_flow")["rows"])
    assert prisma["Records screened"] == flow["screened"]
    chars = table(rep, "study_characteristics")
    assert len(chars["rows"]) == 2 and all(row[0].startswith("[") for row in chars["rows"])
    assert any("120 (p. 1)" in str(cell) for row in chars["rows"] for cell in row)
    assert table(rep, "search_history")["rows"]  # recorded search queries, not invented ones
    statements = [b for b in blocks(rep, "synthesis") + blocks(rep, "discussion") if b["type"] == "statement"]
    assert statements and all(b["evidence"] and "[@" in b["text"] for b in statements)
    assert rep["validation"]["summary"]["publication_ready"] is False
    assert rep["validation"]["summary"]["label"].startswith(("Unverified", "Draft with"))
    assert not rep["data_changed_since_generation"]
    codes = {i["code"] for i in rep["validation"]["issues"]}
    assert "placeholder" in codes and "unknown_citation" not in codes and "inconsistent_number" not in codes

    st = next(b for b in statements if any(e["field_key"] == "n" and e["paper_id"] == p1 for e in b["evidence"]))
    tr = c.get(f"/api/v1/projects/{pid}/reports/{rep['id']}/trace", params={"claim_id": st["id"]}).json()
    assert {x["paper_id"] for x in tr["references"]} >= {p1} and tr["source_explanation"].startswith(
        "From the accepted"
    )
    ev = next(e for e in tr["evidence"] if e["field_key"] == "n" and e["paper_id"] == p1)
    assert ev["page_no"] == 1 and "120 adult participants" in ev["excerpt"] and not ev["changed_since_cited"]
    counts = c.get(
        f"/api/v1/projects/{pid}/reports/{rep['id']}/trace", params={"claim_id": blocks(rep, "results")[0]["id"]}
    ).json()
    assert counts["facts"]["screened"] == flow["screened"]
    claims = c.get(f"/api/v1/projects/{pid}/reports/{rep['id']}/trace").json()["claims"]
    assert any(cl["evidence"] > 0 for cl in claims)


def test_edits_create_versions_and_are_validated(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, _ = accepted_project(user_client, dispatcher, object_store, db)
    v1 = c.post(f"/api/v1/projects/{pid}/reports", json={}).json()
    url = f"/api/v1/projects/{pid}/reports"
    res_block = blocks(v1, "results")[0]
    other = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    outsider = next(p["id"] for p in other if p["id"] not in {r["paper_id"] for r in v1["references"]})
    ops = [
        {"op": "set_text", "block_id": res_block["id"], "text": "In total 99 studies were included."},
        {"op": "add_paragraph", "section": "discussion", "text": "The intervention clearly works for everyone."},
        {"op": "add_paragraph", "section": "introduction", "text": "Earlier work [@nobody1999] suggested benefit."},
        {"op": "add_reference", "paper_id": outsider},
        {"op": "verify_section", "section": "eligibility", "verified": True},
    ]
    r = c.post(
        f"{url}/{v1['id']}/edits", json={"ops": ops, "change_summary": "Author edits", "expected_latest_version": 1}
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["supersedes_id"] == v1["id"] and v2["change_summary"] == "Author edits"
    codes = {i["code"] for i in v2["validation"]["issues"]}
    assert {"inconsistent_number", "unsupported_statement", "unknown_citation"} <= codes
    assert v2["validation"]["summary"]["errors"] >= 2
    assert any(ref["paper_id"] == outsider for ref in v2["references"])
    assert next(s for s in v2["sections"] if s["key"] == "eligibility")["verified_by"]
    versions = c.get(url).json()
    assert [(v["version"], v["status"]) for v in versions] == [(2, "current"), (1, "superseded")]
    assert blocks(c.get(f"{url}/{v1['id']}").json(), "results")[0]["text"] == res_block["text"]  # v1 preserved

    tbl = next(b for b in blocks(v2, "results") if b["type"] == "table")
    assert (
        c.post(
            f"{url}/{v2['id']}/edits", json={"ops": [{"op": "set_text", "block_id": tbl["id"], "text": "x"}]}
        ).status_code
        == 422
    )
    stale = c.post(
        f"{url}/{v2['id']}/edits", json={"ops": [{"op": "set_title", "text": "T"}], "expected_latest_version": 1}
    )
    assert stale.status_code == 409
    crit_ops = [{"op": "add_paragraph", "section": "eligibility", "text": "Note on criteria."}]
    v3 = c.post(f"{url}/{v2['id']}/edits", json={"ops": crit_ops}).json()
    assert next(s for s in v3["sections"] if s["key"] == "eligibility")["verified_by"] is None  # content change resets


def test_exports_are_recorded_with_hash_and_review_state(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, _ = accepted_project(user_client, dispatcher, object_store, db)
    rep = c.post(f"/api/v1/projects/{pid}/reports", json={}).json()
    url = f"/api/v1/projects/{pid}/reports/{rep['id']}/exports"
    r = c.post(url, json={"format": "docx"})
    assert r.status_code == 200 and r.headers["x-report-export-sha256"] == hashlib.sha256(r.content).hexdigest()
    doc = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    assert "not publication-ready" in doc
    md = c.post(url, json={"format": "markdown"})
    assert md.text.startswith("# ") and "not publication-ready" in md.text
    z = zipfile.ZipFile(io.BytesIO(c.post(url, json={"format": "supplementary_zip"}).content))
    names = set(z.namelist())
    assert {
        "manifest.json",
        "report.docx",
        "report.md",
        "prisma_flow.json",
        "references.csv",
        "tables/exclusion_log.csv",
        "tables/evidence_table.csv",
        "tables/search_strategies.csv",
    } <= names
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["draft_version"] == 1 and manifest["review_state_at_draft"]["fingerprint"]
    assert manifest["validation"]["publication_ready"] is False
    pj = c.post(url, json={"format": "prisma_json"}).json()
    assert pj["counts"]["included_studies"] == rep["review_state"]["prisma"]["included_studies"]
    assert c.post(url, json={"format": "pdf"}).status_code == 422
    # data changes after the draft are detected and recorded on later exports
    c.put(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n", json={"status": "not_located"})
    now = c.get(f"/api/v1/projects/{pid}/reports/{rep['id']}").json()
    assert now["data_changed_since_generation"] is True
    assert "stale_evidence" in {i["code"] for i in now["validation"]["issues"]}
    c.post(url, json={"format": "markdown"})
    exports = c.get(url).json()
    assert len(exports) == 5 and exports[0]["data_changed_since_draft"] is True
    assert exports[-1]["data_changed_since_draft"] is False


def test_report_requires_an_approved_protocol(user_client):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    r = c.post(f"/api/v1/projects/{pid}/reports", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "protocol_not_approved"
