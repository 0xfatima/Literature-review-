"""Phase 10 integration tests: design-specific appraisal (AI suggestions kept apart from reviewer judgements,
approval rules, append-only history), comparison tables from reviewed evidence, versioned narrative synthesis with
cited statements, deterministic certainty, review before acceptance, stale detection and revision. Real PostgreSQL;
deterministic fake AI providers; the broker hop is skipped."""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.appraisal import service as appraisal_service
from app.core.config import get_settings
from app.providers.llm.base import LLMGateway, LLMResult
from app.synthesis import service as synthesis_service

from .support import _claimed, _take
from .test_evidence_extraction import ready

RCT_DOMAINS = ["randomisation", "deviations", "missing_outcome_data", "outcome_measurement", "selection_of_result"]


class ScriptedLLM:
    name = "fake"
    model = "fake-model-1"

    def __init__(self, answer):
        self.answer = answer
        self.prompts: list[str] = []

    def generate_json(self, *, system, prompt, schema, temperature, max_output_tokens) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult("fake", self.model, self.answer(prompt), input_tokens=5, output_tokens=5, duration_ms=1)


def gw(llm) -> LLMGateway:
    return LLMGateway(llm, send_private_documents=True, terms_reviewed=True)


def run(dispatcher, task: str, fn, gateway) -> list[str]:
    out = []
    for jid in _take(dispatcher, task):
        db, job = _claimed(jid)
        try:
            out.append(fn(db, job, get_settings(), gateway=gateway))
        finally:
            db.close()
    return out


def doc_id(c, pid, sid, paper) -> str:
    rows = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/table").json()["rows"]
    return next(r["paper"]["document_id"] for r in rows if r["paper"]["id"] == paper)


def reviewed_evidence(c, pid, sid, p1, p2) -> None:
    d = doc_id(c, pid, sid, p1)
    base = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence"
    for paper, key, body in (
        (
            p1,
            "design",
            {"status": "found", "value": "RCT", "excerpt": "This RCT enrolled", "document_id": d, "page_no": 1},
        ),
        (
            p1,
            "n",
            {"status": "found", "value": 120, "excerpt": "120 adult participants", "document_id": d, "page_no": 1},
        ),
        (p2, "design", {"status": "found", "value": "Cohort"}),
        (p2, "n", {"status": "unavailable"}),
    ):
        r = c.put(f"{base}/{paper}/{key}", json=body)
        assert r.status_code == 200, r.text


def approve_appraisals(c, pid, p1, p2) -> None:
    url = f"/api/v1/projects/{pid}/appraisal/papers"
    r = c.put(
        f"{url}/{p1}",
        json={
            "framework_key": "rct_rob",
            "overall_judgement": "low",
            "domains": {k: {"judgement": "low", "justification": "Reported adequately."} for k in RCT_DOMAINS},
        },
    )
    assert r.status_code == 200, r.text
    assert c.post(f"{url}/{p1}/approve", json={"framework_key": "rct_rob"}).status_code == 200
    doms = {"selection": "adequate", "comparability": "inadequate", "outcome_exposure": "adequate"}
    r = c.put(
        f"{url}/{p2}",
        json={
            "framework_key": "observational_nos",
            "overall_judgement": "high_concern",
            "domains": {k: {"judgement": v, "justification": "As reported."} for k, v in doms.items()},
        },
    )
    assert r.json()["suggested_overall"] == "high_concern"
    assert c.post(f"{url}/{p2}/approve", json={"framework_key": "observational_nos"}).status_code == 200


# ---------- appraisal ----------


def appraisal_answer(prompt: str) -> dict[str, Any]:
    doms = [
        {
            "domain": "randomisation",
            "judgement": "low",
            "justification": "Trial design stated.",
            "page": 1,
            "quote": "This RCT enrolled",
        }
    ]
    doms += [{"domain": k, "judgement": "low", "quote": "invented text not in paper"} for k in RCT_DOMAINS[1:]]
    return {"domains": doms}


def test_ai_suggestions_never_count_as_judgements_and_approval_needs_reviewer_decisions(
    user_client, dispatcher, object_store, db
):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db)
    reviewed_evidence(c, pid, sid, p1, p2)
    ov = c.get(f"/api/v1/projects/{pid}/appraisal").json()
    sugg = {r["paper"]["id"]: r["suggested_framework"] for r in ov["papers"]}
    assert sugg == {p1: "rct_rob", p2: "observational_nos"}
    assert {f["key"] for f in ov["frameworks"]} >= {"rct_rob", "qualitative_checklist"}

    assert c.post(f"/api/v1/projects/{pid}/appraisal/suggestions", json={}).status_code == 202
    llm = ScriptedLLM(appraisal_answer)
    assert run(dispatcher, "appraisal.suggest", appraisal_service.execute_suggestions, gw(llm)) == ["ok"]
    result = c.get(f"/api/v1/projects/{pid}/appraisal/suggestions").json()["last_result"]
    assert result["papers_suggested"] == 1 and result["papers_no_full_text"] == 1

    rec = c.get(f"/api/v1/projects/{pid}/appraisal/papers/{p1}").json()["records"][0]
    assert rec["framework_key"] == "rct_rob" and rec["domains_judged"] == 0 and rec["ai_suggestions"] == 5
    rand = rec["answers"]["randomisation"]["ai_suggestion"]
    assert rand["excerpt"] == "This RCT enrolled" and rand["supported"] and rand["model_run_id"]
    assert rec["answers"]["deviations"]["ai_suggestion"]["judgement"] == "no_information"  # unsupported quote
    assert "judgement" not in rec["answers"]["randomisation"]

    url = f"/api/v1/projects/{pid}/appraisal/papers/{p1}"
    r = c.post(f"{url}/approve", json={"framework_key": "rct_rob"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "domains_undecided"
    assert (
        c.put(url, json={"framework_key": "rct_rob", "domains": {"randomisation": {"judgement": "great"}}}).status_code
        == 422
    )
    assert (
        c.put(url, json={"framework_key": "rct_rob", "domains": {"randomisation": {"judgement": "low"}}}).status_code
        == 422
    )
    bad_quote = {
        "judgement": "low",
        "justification": "x",
        "excerpt": "made up",
        "document_id": doc_id(c, pid, sid, p1),
        "page_no": 1,
    }
    assert c.put(url, json={"framework_key": "rct_rob", "domains": {"randomisation": bad_quote}}).status_code == 422

    doms = {k: {"judgement": "low", "justification": "Adequate."} for k in RCT_DOMAINS}
    doms["deviations"] = {"judgement": "some_concerns", "justification": "Open label."}
    doms["randomisation"] = {
        **rand,
        "justification": "Accepted AI suggestion after checking the page.",
        "judgement": "low",
    }
    r = c.put(url, json={"framework_key": "rct_rob", "domains": doms, "overall_judgement": "low"})
    assert r.status_code == 200 and r.json()["suggested_overall"] == "some_concerns"
    assert r.json()["answers"]["randomisation"]["ai_suggestion"]["judgement"] == "low"  # suggestion kept apart
    r = c.post(f"{url}/approve", json={"framework_key": "rct_rob"})
    assert r.json()["error"]["code"] == "overall_justification_missing"  # departs from the suggestion
    c.put(url, json={"framework_key": "rct_rob", "overall_justification": "Open label unlikely to matter here."})
    r = c.post(f"{url}/approve", json={"framework_key": "rct_rob"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    assert c.put(url, json={"framework_key": "rct_rob", "overall_judgement": "some_concerns"}).status_code == 422
    r = c.put(url, json={"framework_key": "rct_rob", "overall_judgement": "some_concerns", "reason": "Second look"})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    actions = [x["action"] for x in c.get(f"{url}/history").json()["revisions"]]
    assert actions == ["ai_suggested", "reviewer_edit", "reviewer_edit", "approved", "reopened"]


def test_checklist_appraisal_requires_an_overall_justification(user_client, dispatcher, object_store, db):
    c, pid, _, p1, _ = ready(user_client, dispatcher, object_store, db, consented=False)
    url = f"/api/v1/projects/{pid}/appraisal/papers/{p1}"
    doms = {
        k: {"judgement": "yes", "justification": "Reported."}
        for k in [
            "aims",
            "methodology",
            "design",
            "recruitment",
            "data_collection",
            "reflexivity",
            "ethics",
            "analysis",
            "findings",
        ]
    }
    r = c.put(
        url, json={"framework_key": "qualitative_checklist", "domains": doms, "overall_judgement": "no_major_concerns"}
    )
    assert r.status_code == 200 and r.json()["suggested_overall"] is None
    assert c.post(f"{url}/approve", json={"framework_key": "qualitative_checklist"}).status_code == 409
    c.put(url, json={"framework_key": "qualitative_checklist", "overall_justification": "All items met."})
    assert c.post(f"{url}/approve", json={"framework_key": "qualitative_checklist"}).status_code == 200
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE appraisal_revisions SET action = 'tampered'"))
        db.commit()
    db.rollback()


# ---------- comparison and synthesis ----------


def test_comparison_uses_reviewed_evidence_and_groups_by_field(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    reviewed_evidence(c, pid, sid, p1, p2)
    approve_appraisals(c, pid, p1, p2)
    cmp_ = c.get(f"/api/v1/projects/{pid}/synthesis/comparison?group_by=design").json()
    assert [(g["label"], g["paper_ids"]) for g in cmp_["groups"]] == [("Cohort", [p2]), ("RCT", [p1])]
    rct = next(g for g in cmp_["groups"] if g["label"] == "RCT")
    n = next(f for f in rct["fields"] if f["field_key"] == "n")
    assert n["pattern"] == "single_study" and n["values"][0]["value"] == "120"
    allg = c.get(f"/api/v1/projects/{pid}/synthesis/comparison").json()["groups"][0]
    n_all = next(f for f in allg["fields"] if f["field_key"] == "n")
    assert n_all["gaps"]["unavailable"] == [p2] and n_all["gaps"]["not_located"] == []
    assert cmp_["limitations"]["serious_appraisal_concerns"] == [p2]
    assert cmp_["meta_analysis"]["in_scope"] is False
    assert c.get(f"/api/v1/projects/{pid}/synthesis/comparison?group_by=nope").status_code == 422


def _draft(c, pid, dispatcher, gateway, **body) -> dict[str, Any]:
    assert c.post(f"/api/v1/projects/{pid}/synthesis/drafts", json=body).status_code == 202
    assert run(dispatcher, "synthesis.draft", synthesis_service.execute_draft, gateway) == ["ok"]
    return next(v for v in c.get(f"/api/v1/projects/{pid}/synthesis/versions").json() if v["status"] == "draft")


def test_synthesis_review_acceptance_lineage_staleness_and_revision(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    reviewed_evidence(c, pid, sid, p1, p2)
    approve_appraisals(c, pid, p1, p2)
    v = _draft(c, pid, dispatcher, LLMGateway(None))
    kinds = {s["kind"]: s for s in v["statements"]}
    assert {"disagreement", "gap", "limitation", "method_difference"} <= set(kinds)
    assert kinds["disagreement"]["certainty"] == "conflicting"
    assert all(s["citations"] and s["origin"] == "system" and s["review"] == "unreviewed" for s in v["statements"])
    base = f"/api/v1/projects/{pid}/synthesis/versions/{v['id']}"
    r = c.post(f"{base}/accept", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "unreviewed_statements"

    n_item = next(
        c_["evidence_item_id"]
        for s in v["statements"]
        for c_ in s["citations"]
        if c_["field_key"] == "n" and c_["paper_id"] == p1
    )
    r = c.post(
        f"{base}/review",
        json={
            "action": "add",
            "kind": "conclusion",
            "text": "The trial enrolled 120 adults.",
            "citations": [n_item],
            "claimed_certainty": "supported",
        },
    )
    added = r.json()["statements"][-1]
    assert added["certainty"] == "uncertain" and added["warnings"][0]["code"] == "certainty_capped"
    assert c.post(f"{base}/review", json={"action": "add", "text": "No citation."}).status_code == 422

    current = r.json()
    for s in current["statements"]:
        if s["review"] == "unreviewed":
            action = "reject" if s["kind"] == "method_difference" else "accept"
            current = c.post(f"{base}/review", json={"action": action, "statement_id": s["id"]}).json()
    r = c.post(f"{base}/accept", json={"note": "Reviewed against sources."})
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["evidence_output_id"]
    hist = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n/history").json()
    assert [o["kind"] for o in hist["outputs"]] == ["synthesis"] and not hist["outputs"][0]["stale"]
    assert c.post(f"{base}/review", json={"action": "accept", "statement_id": added["id"]}).status_code == 409

    c.put(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n", json={"status": "not_located"})
    after = c.get(base).json()
    assert next(s for s in after["statements"] if s["id"] == added["id"])["stale"] is True
    r = c.post(f"{base}/revise")
    assert r.status_code == 201
    new = r.json()
    assert new["supersedes_id"] == v["id"] and all(s["review"] != "rejected" for s in new["statements"])
    revised = next(s for s in new["statements"] if s["id"] == added["id"])
    # the cited value is now "not located": the statement must be reviewed again and is no longer supported
    assert revised["review"] == "unreviewed" and revised["certainty"] == "insufficient"
    assert c.post(f"/api/v1/projects/{pid}/synthesis/drafts", json={}).status_code == 409  # draft exists


def synthesis_answer(prompt: str) -> dict[str, Any]:
    keys = re.findall(r"^\s+(E\d+) \[", prompt, re.M)
    return {
        "statements": [
            {
                "kind": "finding",
                "text": "One trial enrolled 120 adults.",
                "citations": keys[:2],
                "certainty": "supported",
            },
            {"kind": "conclusion", "text": "The pooled estimate favours the intervention.", "citations": keys[:1]},
            {"kind": "finding", "text": "Uncited.", "citations": ["E999"]},
        ]
    }


def test_ai_synthesis_statements_are_validated_and_gated(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db)
    reviewed_evidence(c, pid, sid, p1, p2)
    llm = ScriptedLLM(synthesis_answer)
    v = _draft(c, pid, dispatcher, gw(llm), use_ai=True)
    ai_st = [s for s in v["statements"] if s["origin"] == "ai"]
    assert len(ai_st) == 1 and ai_st[0]["certainty"] == "uncertain"  # not appraised -> capped
    assert "pooled" not in " ".join(s["text"] for s in v["statements"]).lower()
    status = c.get(f"/api/v1/projects/{pid}/synthesis/drafts").json()
    assert status["last_result"]["ai_statements"] == 1 and status["last_result"]["ai_dropped"] >= 2
    assert v["model_run_id"] and v["meta_analysis"]["in_scope"] is False


def test_ai_synthesis_without_consent_sends_nothing(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    reviewed_evidence(c, pid, sid, p1, p2)
    llm = ScriptedLLM(synthesis_answer)
    v = _draft(c, pid, dispatcher, gw(llm), use_ai=True)
    assert llm.prompts == [] and v["statements"] and all(s["origin"] == "system" for s in v["statements"])
    assert c.get(f"/api/v1/projects/{pid}/synthesis/drafts").json()["last_result"]["stop_reason"] == "refused"
