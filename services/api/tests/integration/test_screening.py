"""Relevance and title/abstract screening on real PostgreSQL: decisions, revisions, undo, bulk, optimistic
concurrency, the append-only trigger, stage advance, and the relevance job with a fake LLM (no network)."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import ModelRun, RelevanceAssessment, ScreeningDecision, WorkflowEvent
from app.providers.llm.base import LLMError, LLMGateway
from tests.fakes import FakeLLM
from tests.fixtures import scholarly as fx

from .conftest import drain
from .support import approved, run_relevance_jobs, searched
from .test_protocol_workflow import project, started


def queue(c, pid, **params):
    r = c.get(f"/api/v1/projects/{pid}/screening/queue", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def item(c, pid, title):
    return next(i for i in queue(c, pid, decision="")["items"] if i["title"].startswith(title))


def decide(c, pid, it, decision, expect=200, **body):
    r = c.post(
        f"/api/v1/projects/{pid}/screening/papers/{it['paper_id']}/decision",
        json={"decision": decision, "expected_row_version": it["state_row_version"] or 0, **body},
    )
    assert r.status_code == expect, r.text
    return r.json()


def history(c, pid, paper_id):
    return c.get(f"/api/v1/projects/{pid}/screening/papers/{paper_id}/history").json()["decisions"]


def excl_reason(c, pid):
    return next(
        r["code"]
        for r in c.get(f"/api/v1/projects/{pid}/screening/reasons").json()["items"]
        if r["group"] == "exclusion_criterion"
    )


@pytest.fixture
def screened(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    return c, pid


# ---------- preconditions ----------


def test_screening_needs_an_approved_protocol(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    r = c.get(f"/api/v1/projects/{pid}/screening/queue")
    assert r.status_code == 409 and r.json()["error"]["code"] == "protocol_not_approved"


def test_the_queue_shows_every_discovered_record_with_progress(screened):
    c, pid = screened
    q = queue(c, pid)
    assert q["progress"] == {"total": 4, "undecided": 4, "include": 0, "exclude": 0, "uncertain": 0}
    assert len(q["items"]) == 4 and all(i["band"] == "not_assessed" and i["ai"] is None for i in q["items"])
    assert {"title", "authors", "year", "abstract", "providers"} <= set(q["items"][0])
    for bad in ({"decision": "nope"}, {"band": "purple"}, {"sort": "random"}, {"suggestion": "maybe"}):
        assert c.get(f"/api/v1/projects/{pid}/screening/queue", params=bad).status_code == 422


# ---------- decisions ----------


def test_decide_revise_and_recover_after_reopening(screened, db, make_client):
    c, pid = screened
    it = item(c, pid, fx.T_W1)
    decide(c, pid, it, "include", note="clearly on topic")
    q = queue(c, pid, decision="undecided")  # the UI's default "still to screen" view
    assert q["progress"]["include"] == 1 and q["progress"]["undecided"] == 3 and q["total"] == 3
    assert fx.T_W1 not in [i["title"] for i in q["items"]]

    it = item(c, pid, fx.T_W1)
    assert it["decision"] == "include" and it["decision_is_current_protocol"]
    reason = excl_reason(c, pid)
    decide(c, pid, it, "exclude", reason_code=reason, note="animals after all")
    h = history(c, pid, it["paper_id"])
    assert [d["decision"] for d in h] == ["include", "exclude"]
    assert h[1]["supersedes_id"] == h[0]["id"] and h[1]["reason_code"] == reason and h[1]["reviewer_id"]
    assert h[1]["reason_label"].startswith("Meets exclusion criterion")

    # project and workflow moved to screening; a stage event records it
    assert c.get(f"/api/v1/projects/{pid}").json()["status"] == "screening"
    assert c.get(f"/api/v1/projects/{pid}/workflow").json()["run"]["stage"] == "RECORD_SCREENING"
    assert (
        db.scalar(
            select(func.count())
            .select_from(WorkflowEvent)
            .where(WorkflowEvent.kind == "stage", WorkflowEvent.stage == "RECORD_SCREENING")
        )
        >= 1
    )

    # "reopening the project": a brand-new client session sees the same decisions
    fresh = queue(c, pid, decision="exclude")
    assert [i["title"] for i in fresh["items"]] == [it["title"]] and fresh["items"][0]["decision"] == "exclude"
    assert len(history(c, pid, it["paper_id"])) == 2


def test_invalid_decisions_are_refused(screened):
    c, pid = screened
    it = item(c, pid, fx.T_W2)
    r = decide(c, pid, it, "exclude", expect=422, reason_code="exclusion:" + str(uuid.uuid4()))
    assert r["error"]["details"][0]["field"] == "reason_code"
    assert decide(c, pid, it, "exclude", expect=422, reason_code="other")["error"]["details"][0]["field"] == "note"
    assert decide(c, pid, it, "include", expect=422, reason_code="off_topic")
    assert decide(c, pid, it, "cleared", expect=422)
    decide(c, pid, it, "exclude")  # a reason is recommended, not required, at title/abstract stage
    missing = c.post(f"/api/v1/projects/{pid}/screening/papers/{uuid.uuid4()}/decision", json={"decision": "include"})
    assert missing.status_code == 404


def test_a_required_reason_is_enforced_when_configured(screened, app):
    c, pid = screened
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"screening_ta_require_exclusion_reason": True}
    )
    assert c.get(f"/api/v1/projects/{pid}/screening/reasons").json()["require_reason"] is True
    decide(c, pid, item(c, pid, fx.T_W2), "exclude", expect=422)


def test_stale_row_version_conflicts(screened):
    c, pid = screened
    it = item(c, pid, fx.T_E2)
    decide(c, pid, it, "include")
    r = decide(c, pid, it, "uncertain", expect=409)  # `it` still carries the old row_version
    assert r["error"]["code"] == "version_conflict"
    assert decide(c, pid, item(c, pid, fx.T_E2), "include")["unchanged"] is True


def test_decisions_are_append_only_in_the_database(screened, db):
    c, pid = screened
    decide(c, pid, item(c, pid, fx.T_W1), "include")
    row = db.scalar(select(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(pid)))
    for sql in (
        "UPDATE screening_decisions SET decision='exclude' WHERE id=:id",
        "DELETE FROM screening_decisions WHERE id=:id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(sql), {"id": row.id})
        db.rollback()
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204  # deletion still cascades
    assert (
        db.scalar(
            select(func.count()).select_from(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(pid))
        )
        == 0
    )


# ---------- bulk and undo ----------


def test_bulk_and_safe_undo(screened):
    c, pid = screened
    items = queue(c, pid)["items"]
    first = items[0]
    decide(c, pid, first, "include")  # earlier decision on one of them
    ids = [i["paper_id"] for i in items[:3]]
    r = c.post(f"/api/v1/projects/{pid}/screening/bulk", json={"paper_ids": ids, "decision": "uncertain"}).json()
    assert r["changed"] == 3 and r["batch_id"]
    assert queue(c, pid, decision="uncertain")["total"] == 3

    changed_since = next(i for i in queue(c, pid, decision="uncertain")["items"] if i["paper_id"] == ids[2])
    decide(c, pid, changed_since, "include")  # changed after the bulk action: undo must leave it alone

    u = c.post(f"/api/v1/projects/{pid}/screening/undo", json={"batch_id": r["batch_id"]}).json()
    assert (u["undone"], u["skipped_changed_since"]) == (2, 1)
    states = {i["paper_id"]: i["decision"] for i in queue(c, pid, decision="")["items"]}
    assert states[ids[0]] == "include"  # restored to its earlier decision
    assert states[ids[1]] is None  # restored to "no decision"
    assert states[ids[2]] == "include"  # untouched
    assert [d["action"] for d in history(c, pid, ids[0])] == ["decide", "bulk", "undo"]

    again = c.post(f"/api/v1/projects/{pid}/screening/undo", json={"batch_id": r["batch_id"]}).json()
    assert again["undone"] == 0
    bad = c.post(
        f"/api/v1/projects/{pid}/screening/bulk", json={"paper_ids": [str(uuid.uuid4())], "decision": "include"}
    )
    assert bad.status_code == 404
    assert c.post(f"/api/v1/projects/{pid}/screening/undo", json={}).status_code == 422


def test_undo_a_single_decision(screened):
    c, pid = screened
    it = item(c, pid, fx.T_W2)
    decide(c, pid, it, "include")
    last = history(c, pid, it["paper_id"])[-1]
    assert c.post(f"/api/v1/projects/{pid}/screening/undo", json={"decision_id": last["id"]}).json()["undone"] == 1
    assert item(c, pid, fx.T_W2)["decision"] is None


def test_no_decisions_while_a_protocol_revision_is_in_progress(screened, dispatcher):
    c, pid = screened
    assert c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "Tighten criteria"}).status_code == 202
    drain(dispatcher)
    r = decide(c, pid, item(c, pid, fx.T_W1), "include", expect=409)
    assert r["error"]["code"] == "workflow_busy"


# ---------- relevance ----------


def test_word_overlap_relevance_orders_and_explains_the_queue(screened, dispatcher):
    c, pid = screened
    status = c.get(f"/api/v1/projects/{pid}/screening/relevance").json()
    assert status["llm"]["configured"] is False and status["total_papers"] == 4
    assert c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": True}).status_code == 409

    assert c.post(f"/api/v1/projects/{pid}/screening/relevance", json={}).status_code == 202
    again = c.post(f"/api/v1/projects/{pid}/screening/relevance", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_already_active"
    assert run_relevance_jobs(dispatcher) == ["ok"]

    items = queue(c, pid)["items"]
    assert all(i["lexical"] and i["band_source"] == "lexical" for i in items)
    scores = [i["lexical"]["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    top = items[0]
    assert top["lexical"]["explanation"]["concepts"] and "not eligibility" in top["lexical"]["explanation"]["summary"]
    counts = c.get(f"/api/v1/projects/{pid}/screening/relevance").json()["counts"]["lexical"]
    assert sum(counts.values()) == 4
    band = top["band"]
    assert all(i["band"] == band for i in queue(c, pid, band=band)["items"])


def llm_settings():
    return get_settings().model_copy(
        update={"llm_provider": "gemini", "llm_api_key": "test-only", "llm_model": "fake", "llm_batch_size": 3}
    )


def test_ai_assessment_is_stored_as_a_suggestion_validated_and_never_as_a_decision(screened, dispatcher, app, db):
    c, pid = screened
    app.dependency_overrides[get_settings] = llm_settings
    quote = "Older adults with depression exercised."

    def answer(key, block):
        if fx.T_W1 in block:  # exclusion backed by a verbatim quote: kept
            return {
                "paper": key,
                "relevance": 90,
                "relevance_reason": "on topic",
                "insufficient_information": False,
                "eligibility_suggestion": "exclude",
                "criteria": [{"criterion": "C1", "judgement": "not_met", "evidence_quote": quote}],
            }
        if fx.T_W2 in block:  # invented quote: removed, suggestion downgraded
            return {
                "paper": key,
                "relevance": 60,
                "relevance_reason": "partly",
                "insufficient_information": False,
                "eligibility_suggestion": "exclude",
                "criteria": [{"criterion": "C1", "judgement": "not_met", "evidence_quote": "an invented sentence"}],
            }
        return {
            "paper": key,
            "relevance": 30,
            "relevance_reason": "weak",
            "insufficient_information": False,
            "eligibility_suggestion": "include",
        }

    fake = FakeLLM(answer)
    r = c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": True})
    assert r.status_code == 202
    assert run_relevance_jobs(dispatcher, LLMGateway(fake), llm_settings()) == ["ok"]
    assert len(fake.prompts) == 2  # 4 records in batches of 3

    w1, w2 = item(c, pid, fx.T_W1), item(c, pid, fx.T_W2)
    assert w1["band_source"] == "ai" and w1["band"] == "high" and w1["ai"]["eligibility_suggestion"] == "exclude"
    assert w1["ai"]["suggestion_detail"]["criteria"][0]["quote_verified"] is True
    assert w2["ai"]["eligibility_suggestion"] == "uncertain" and w2["ai"]["warnings"]
    assert w1["decision"] is None  # a suggestion never becomes a decision
    assert (
        db.scalar(
            select(func.count()).select_from(ScreeningDecision).where(ScreeningDecision.project_id == uuid.UUID(pid))
        )
        == 0
    )
    assert queue(c, pid, suggestion="exclude")["total"] == 1

    runs = db.scalars(select(ModelRun).where(ModelRun.project_id == uuid.UUID(pid))).all()
    assert len(runs) == 2 and {m.status for m in runs} == {"succeeded"}
    assert all(m.data_class == "public_metadata" and m.prompt_version == "relevance-v1" for m in runs)
    assert runs[0].input_tokens == 100

    # the decision records which assessments were on screen
    decide(c, pid, w1, "include")
    shown = history(c, pid, w1["paper_id"])[-1]["assessment_ids"]
    ids = {
        str(a.id)
        for a in db.scalars(
            select(RelevanceAssessment).where(RelevanceAssessment.paper_id == uuid.UUID(w1["paper_id"]))
        )
    }
    assert set(shown) == ids and len(ids) == 2  # lexical + AI


def test_a_quota_error_stops_the_ai_part_cleanly_and_keeps_word_overlap(screened, dispatcher, app, db):
    c, pid = screened
    app.dependency_overrides[get_settings] = llm_settings
    fake = FakeLLM(error=LLMError("rate_limited", "quota reached"))
    c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": True})
    assert run_relevance_jobs(dispatcher, LLMGateway(fake), llm_settings()) == ["ok"]
    assert len(fake.prompts) == 1  # stopped after the first failed request
    s = c.get(f"/api/v1/projects/{pid}/screening/relevance").json()
    assert s["last_result"]["llm_stop_reason"] == "rate_limited" and "quota" in s["last_result"]["message"]
    assert sum(s["counts"]["lexical"].values()) == 4 and s["counts"]["llm"] == {}
    assert s["model_runs"][0]["status"] == "failed" and s["model_runs"][0]["error_kind"] == "rate_limited"

    # continuing later assesses the rest
    c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": True})
    assert run_relevance_jobs(dispatcher, LLMGateway(FakeLLM()), llm_settings()) == ["ok"]
    assert sum(c.get(f"/api/v1/projects/{pid}/screening/relevance").json()["counts"]["llm"].values()) == 4


def test_a_partial_ai_answer_leaves_missing_records_for_later(screened, dispatcher, app):
    c, pid = screened
    app.dependency_overrides[get_settings] = llm_settings
    fake = FakeLLM(
        lambda key, block: (
            None
            if key == "P1"
            else {
                "paper": key,
                "relevance": 50,
                "relevance_reason": "x",
                "insufficient_information": False,
                "eligibility_suggestion": "include",
            }
        )
    )
    c.post(f"/api/v1/projects/{pid}/screening/relevance", json={"use_llm": True})
    run_relevance_jobs(dispatcher, LLMGateway(fake), llm_settings())
    s = c.get(f"/api/v1/projects/{pid}/screening/relevance").json()
    assert s["last_result"]["llm_remaining"] == 2 and s["last_result"]["llm_not_assessed"] == 2
    assert any(m["error_kind"] == "partial_answer" for m in s["model_runs"])


def test_a_relevance_job_for_a_superseded_protocol_fails_visibly(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    c.post(f"/api/v1/projects/{pid}/screening/relevance", json={})
    c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "Change scope"})
    drain(dispatcher)
    from .test_protocol_workflow import answer as wf_answer

    wf_answer(c, dispatcher, pid, "approve")
    assert run_relevance_jobs(dispatcher) == ["failed"]
    assert "protocol changed" in c.get(f"/api/v1/projects/{pid}/screening/relevance").json()["last_error"]
