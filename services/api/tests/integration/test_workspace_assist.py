"""Phase 12 integration tests: contextual workspace actions are real, permission-scoped backend operations —
explain an exclusion (and post it to chat), find supporting passages in stored full text, reassess one paper's
relevance in the background, and ask the workspace assistant (advisory answer stored with a model run)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.api.deps import get_llm_gateway
from app.db.models import ModelRun, RelevanceAssessment
from app.providers.llm.base import LLMGateway, LLMResult

from .support import run_relevance_jobs, searched
from .test_evidence_extraction import ready


def _thread(c, pid) -> str:
    return c.post(f"/api/v1/projects/{pid}/conversations", json={}).json()["id"]


def test_explain_exclusion_uses_the_recorded_decision_and_posts_to_chat(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper = c.get(f"/api/v1/projects/{pid}/papers").json()["items"][0]["id"]
    reasons = c.get(f"/api/v1/projects/{pid}/screening/reasons").json()["items"]
    code = next(r["code"] for r in reasons if r["group"] == "exclusion_criterion")
    r = c.post(
        f"/api/v1/projects/{pid}/screening/papers/{paper}/decision",
        json={"decision": "exclude", "reason_code": code, "note": "Out of scope."},
    )
    assert r.status_code == 200, r.text
    cid = _thread(c, pid)
    out = c.post(f"/api/v1/projects/{pid}/papers/{paper}/explain-exclusion", json={"conversation_id": cid}).json()
    assert out["status"] == "excluded" and out["stages"][0]["criterion_kind"] == "exclusion"
    assert out["stages"][0]["criterion_text"] and "Reviewer's note: Out of scope." in out["lines"]
    msgs = c.get(f"/api/v1/projects/{pid}/conversations/{cid}/messages").json()["items"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["payload"]["action"] == "explain_exclusion"
    assert msgs[-1]["payload"]["paper_id"] == paper and "from the review record" in msgs[-1]["content"]
    other = c.get(f"/api/v1/projects/{pid}/papers").json()["items"][1]["id"]
    undecided = c.post(f"/api/v1/projects/{pid}/papers/{other}/explain-exclusion", json={}).json()
    assert undecided["status"] == "undecided" and "message_id" not in undecided
    bad = c.post(f"/api/v1/projects/{pid}/papers/{paper}/explain-exclusion", json={"conversation_id": "nope"})
    assert bad.status_code == 422


def test_find_evidence_returns_verbatim_passages_that_can_be_saved(user_client, dispatcher, object_store, db):
    c, pid, sid, p1, p2 = ready(user_client, dispatcher, object_store, db, consented=False)
    url = f"/api/v1/projects/{pid}/papers"
    out = c.post(f"{url}/{p1}/find-evidence", json={"query": "adult participants enrolled", "field_key": "n"}).json()
    assert out["status"] == "ok" and out["hits"][0]["page_no"] == 1
    hit = out["hits"][0]
    assert {"adult", "participants", "enrolled"} <= set(hit["matched_terms"])
    # the passage is verbatim, so it is accepted as a cited excerpt
    r = c.put(
        f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{p1}/n",
        json={
            "status": "found",
            "value": 120,
            "excerpt": hit["excerpt"],
            "document_id": out["document_id"],
            "page_no": hit["page_no"],
        },
    )
    assert r.status_code == 200, r.text
    assert c.post(f"{url}/{p2}/find-evidence", json={"field_key": "n"}).json()["status"] == "no_full_text"
    none = c.post(f"{url}/{p1}/find-evidence", json={"query": "zebrafish"}).json()
    assert none["status"] == "no_match" and "not evidence" in none["message"]
    assert c.post(f"{url}/{p1}/find-evidence", json={"field_key": "nope"}).status_code == 422
    assert c.post(f"{url}/{p1}/find-evidence", json={}).status_code == 422


def test_reassess_relevance_runs_only_for_the_chosen_paper(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper = c.get(f"/api/v1/projects/{pid}/papers").json()["items"][0]["id"]
    r = c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"paper_ids": [paper]})
    assert r.status_code == 202, r.text
    run_relevance_jobs(dispatcher)
    assessed = set(
        db.scalars(select(RelevanceAssessment.paper_id).where(RelevanceAssessment.project_id == uuid.UUID(pid)))
    )
    assert {str(p) for p in assessed} == {paper}
    foreign = c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"paper_ids": [str(uuid.uuid4())]})
    assert foreign.status_code == 404


class Answerer:
    name = "fake"
    model = "fake-model-1"

    def __init__(self):
        self.prompts: list[str] = []

    def generate_json(self, *, system, prompt, schema, temperature, max_output_tokens) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            "fake",
            self.model,
            {"answer": "Use the eligibility criteria in the protocol.", "answerable_from_context": True},
            3,
            3,
            1,
        )


def test_assistant_answers_are_grounded_advisory_and_recorded(app, user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    cid = _thread(c, pid)
    url = f"/api/v1/projects/{pid}/conversations/{cid}/ask"
    r = c.post(url, json={"question": "What decides inclusion?"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "llm_not_configured"
    llm = Answerer()
    app.dependency_overrides[get_llm_gateway] = lambda: LLMGateway(llm)
    try:
        paper = c.get(f"/api/v1/projects/{pid}/papers").json()["items"][0]
        r = c.post(url, json={"question": "Is this paper in scope?", "paper_id": paper["id"]})
        assert r.status_code == 201, r.text
        assert "Protocol version" in llm.prompts[0] and paper["title"] in llm.prompts[0]
        msgs = c.get(f"/api/v1/projects/{pid}/conversations/{cid}/messages").json()["items"]
        assert [m["role"] for m in msgs[-2:]] == ["user", "assistant"]
        assert msgs[-1]["payload"]["type"] == "ai_answer" and msgs[-1]["payload"]["advisory"] is True
        run = db.scalar(select(ModelRun).where(ModelRun.purpose == "assistant_answer"))
        assert run is not None and run.data_class == "public_metadata"
        assert c.post(url, json={"question": ""}).status_code == 422
        assert c.get(f"/api/v1/projects/{pid}/assist/status").json()["assistant_available"] is False
    finally:
        app.dependency_overrides.pop(get_llm_gateway, None)


def test_workspace_overview_reflects_stored_stage_state(user_client, dispatcher):
    c, _, _ = user_client()
    fresh = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    empty = c.get(f"/api/v1/projects/{fresh}/workspace/overview").json()
    assert empty["current"] == "protocol" and empty["stages"][0]["status"] == "not_started"
    pid, _ = searched(c, dispatcher)
    ov = c.get(f"/api/v1/projects/{pid}/workspace/overview").json()
    keys = [s["key"] for s in ov["stages"]]
    assert keys == ["protocol", "search", "screening", "full_text", "extraction", "appraisal", "synthesis", "report"]
    by = {s["key"]: s for s in ov["stages"]}
    assert by["protocol"]["status"] == "done" and by["search"]["status"] == "done"
    assert by["report"]["status"] == "not_started" and by["screening"]["tab"] == "screening"
    assert ov["current"] in {"screening", "full_text"}
