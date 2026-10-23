"""Protocol setup through the review workflow, on real PostgreSQL with the real LangGraph checkpointer.

Workflow jobs are executed in-process by `drain` (the real Celery task body); the broker hop and a separate worker
process are covered by tests/workflow/test_langgraph_gate.py.
"""

import uuid

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models import Message, ProtocolVersion, WorkflowEvent, WorkflowRun

from .conftest import drain

TOPIC = "Effectiveness of aerobic exercise compared with usual care for depression in adults over 60"


def project(c, topic=TOPIC) -> str:
    r = c.post("/api/v1/projects", json={"title": "Exercise review", "topic": topic})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def started(c, dispatcher, pid) -> dict:
    r = c.post(f"/api/v1/projects/{pid}/workflow")
    assert r.status_code == 202, r.text
    drain(dispatcher)
    return state(c, pid)


def state(c, pid) -> dict:
    r = c.get(f"/api/v1/projects/{pid}/workflow")
    assert r.status_code == 200, r.text
    return r.json()["run"]


def draft(c, pid) -> dict:
    return c.get(f"/api/v1/projects/{pid}/protocol").json()["draft"]


def answer(c, dispatcher, pid, action, values=None, *, version=True, expect=202) -> dict:
    run = state(c, pid)
    assert run["status"] == "awaiting_input", run
    body = {"question_id": run["pending_question_id"], "action": action, "values": values or {}}
    if version:
        d = draft(c, pid)
        body["expected_row_version"] = d["row_version"] if d else None
    r = c.post(f"/api/v1/projects/{pid}/workflow/resume", json=body)
    assert r.status_code == expect, r.text
    drain(dispatcher)
    return state(c, pid)


def kind(run) -> str:
    return run["pending_question"]["kind"]


def walk_to_review(c, dispatcher, pid) -> dict:
    """Answer every protocol question with real content and accept every suggested criterion."""
    run = state(c, pid)
    assert kind(run) == "objective"
    run = answer(c, dispatcher, pid, "save", {"objective": "Estimate the effect of aerobic exercise on depression."})
    assert kind(run) == "framework"
    sug = run["pending_question"]["data"]["suggestion"]
    assert sug["framework"] == "pico" and "Not an AI judgement" in sug["method_note"]
    run = answer(c, dispatcher, pid, "accept")
    assert kind(run) == "elements"
    els = [
        {"key": "population", "value": "adults over 60 with depression"},
        {"key": "intervention", "value": "aerobic exercise"},
        {"key": "comparison", "value": "usual care"},
        {"key": "outcome", "value": "depressive symptoms"},
    ]
    run = answer(c, dispatcher, pid, "save", {"framework_elements": els})
    assert kind(run) == "questions"
    suggested = run["pending_question"]["data"]["suggested_question"]
    assert suggested.startswith("In adults over 60 with depression, what is the effect of aerobic exercise")
    run = answer(c, dispatcher, pid, "save", {"questions": [{"text": suggested}]})
    assert kind(run) == "scope"
    run = answer(
        c,
        dispatcher,
        pid,
        "save",
        {"date_from": 2000, "date_to": 2025, "languages": ["English"], "sources": ["OpenAlex", "Europe PMC"]},
    )
    assert kind(run) == "criteria"
    suggestions = run["pending_question"]["data"]["suggestions"]
    assert suggestions and all(s["state"] == "proposed" for s in suggestions)
    decided = [{**s, "state": "rejected" if s["kind"] == "exclusion" else "accepted"} for s in suggestions]
    decided.append({"kind": "exclusion", "text": "Animal studies", "origin": "user"})
    run = answer(c, dispatcher, pid, "save", {"criteria": decided})
    assert kind(run) == "review" and run["stage"] == "PROTOCOL_REVIEW"
    return run


# ---------- the main path ----------


def test_start_creates_draft_conversation_and_first_question(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    assert run["status"] == "awaiting_input" and run["stage"] == "DRAFT" and kind(run) == "objective"
    d = draft(c, pid)
    assert d["version"] == 1 and d["status"] == "draft" and not d["can_approve"]
    msgs = c.get(f"/api/v1/projects/{pid}/conversations/{run['conversation_id']}/messages").json()["items"]
    assert [m["role"] for m in msgs] == ["assistant"]
    assert msgs[0]["payload"] == {
        "type": "workflow_question",
        "question_id": run["pending_question_id"],
        "kind": "objective",
    }
    # starting again is idempotent
    assert c.post(f"/api/v1/projects/{pid}/workflow").status_code == 200
    assert db.scalar(select(func.count()).select_from(WorkflowRun).where(WorkflowRun.project_id == uuid.UUID(pid))) == 1


def test_full_protocol_journey_to_approval(user_client, dispatcher, db):
    c, _, uid = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    walk_to_review(c, dispatcher, pid)
    d = draft(c, pid)
    assert d["can_approve"], d["issues"]
    assert "Inclusion criteria (" in "\n".join(d["summary"])
    run = answer(c, dispatcher, pid, "approve")
    assert run["status"] == "idle" and run["stage"] == "SEARCH" and run["pending_question"] is None

    proto = c.get(f"/api/v1/projects/{pid}/protocol").json()
    assert proto["draft"] is None
    a = proto["approved"]
    assert a["version"] == 1 and a["approved_by"] == uid and a["approved_at"] and len(a["content_hash"]) == 64
    assert a["impact"] is None  # first version: nothing to compare with
    texts = [x["text"] for x in a["content"]["criteria"] if x["state"] == "accepted"]
    assert "Population: adults over 60 with depression" in texts and "Animal studies" in texts

    msgs = c.get(f"/api/v1/projects/{pid}/conversations/{run['conversation_id']}/messages?limit=200").json()["items"]
    assert [m["role"] for m in msgs] == ["assistant", "user"] * 7  # 7 questions, 7 answers, none duplicated
    assert msgs[-1]["content"] == "Approved protocol version 1."
    kinds = [e["kind"] for e in c.get(f"/api/v1/projects/{pid}/workflow/events").json()["items"]]
    assert kinds.count("question") == 7 and kinds.count("answer") == 7 and kinds[-1] == "stage"


def test_skipping_the_framework_skips_its_elements_step(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c, topic="Graph neural networks for molecules")
    started(c, dispatcher, pid)
    run = answer(c, dispatcher, pid, "skip")
    assert kind(run) == "framework" and run["pending_question"]["data"]["suggestion"]["framework"] == "custom"
    run = answer(c, dispatcher, pid, "skip")
    assert kind(run) == "questions"  # no elements question for 'not applicable'
    assert draft(c, pid)["content"]["framework"] == "none"


def test_review_can_send_the_user_back_to_one_section_and_returns_to_review(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    walk_to_review(c, dispatcher, pid)
    run = answer(c, dispatcher, pid, "edit", {"section": "scope"}, version=False)
    assert kind(run) == "scope" and run["stage"] == "DRAFT"
    run = answer(c, dispatcher, pid, "save", {"date_from": 2010})
    assert kind(run) == "review"  # back to the review, not on to criteria
    assert draft(c, pid)["content"]["date_from"] == 2010


# ---------- validation and conflicts ----------


def test_incomplete_protocol_cannot_be_approved_and_the_run_keeps_waiting(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    run = state(c, pid)
    while kind(run) != "review":  # objective, framework (skipped => no elements step), questions, scope, criteria
        run = answer(c, dispatcher, pid, "skip")
    assert kind(run) == "review"
    before = run["pending_question_id"]
    r = c.post(
        f"/api/v1/projects/{pid}/workflow/resume",
        json={"question_id": before, "action": "approve", "expected_row_version": draft(c, pid)["row_version"]},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert {d["field"] for d in r.json()["error"]["details"]} >= {"objective", "questions", "criteria"}
    after = state(c, pid)
    assert after["status"] == "awaiting_input" and after["pending_question_id"] == before
    assert dispatcher.sent == []  # nothing was queued


def test_invalid_values_are_rejected_without_moving_the_workflow(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    v = draft(c, pid)["row_version"]
    bad = {"question_id": run["pending_question_id"], "action": "save", "expected_row_version": v}
    r = c.post(f"/api/v1/projects/{pid}/workflow/resume", json={**bad, "values": {"objective": "x" * 5000}})
    assert r.status_code == 422
    r = c.post(f"/api/v1/projects/{pid}/workflow/resume", json={**bad, "action": "approve"})
    assert r.status_code == 422  # not a valid action for this question
    r = c.post(f"/api/v1/projects/{pid}/workflow/resume", json={**bad, "expected_row_version": v + 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    assert state(c, pid)["pending_question_id"] == run["pending_question_id"]


def test_duplicate_resume_is_a_noop_and_a_different_one_conflicts(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    body = {
        "question_id": run["pending_question_id"],
        "action": "save",
        "values": {"objective": "Objective A"},
        "expected_row_version": draft(c, pid)["row_version"],
    }
    assert c.post(f"/api/v1/projects/{pid}/workflow/resume", json=body).status_code == 202
    assert c.post(f"/api/v1/projects/{pid}/workflow/resume", json=body).status_code == 200  # replay: no-op
    other = {**body, "values": {"objective": "Objective B"}}
    r = c.post(f"/api/v1/projects/{pid}/workflow/resume", json=other)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_answered"
    drain(dispatcher)
    n = db.scalar(
        select(func.count())
        .select_from(WorkflowEvent)
        .where(WorkflowEvent.kind == "answer", WorkflowEvent.question_id == uuid.UUID(run["pending_question_id"]))
    )
    assert n == 1 and draft(c, pid)["content"]["objective"] == "Objective A"
    # an old question id after the workflow moved on
    stale = {**body, "question_id": str(uuid.uuid4())}
    assert c.post(f"/api/v1/projects/{pid}/workflow/resume", json=stale).json()["error"]["code"] == "stale_question"


def test_resume_is_rejected_while_the_workflow_is_not_waiting(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    c.post(f"/api/v1/projects/{pid}/workflow")  # job dispatched but not run yet: status queued
    r = c.post(
        f"/api/v1/projects/{pid}/workflow/resume",
        json={"question_id": str(uuid.uuid4()), "action": "save", "values": {}},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_awaiting_input"


def test_draft_edits_use_optimistic_concurrency(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    v = draft(c, pid)["row_version"]
    r = c.patch(f"/api/v1/projects/{pid}/protocol/draft", json={"expected_row_version": v, "languages": ["English"]})
    assert r.status_code == 200 and r.json()["row_version"] == v + 1
    r = c.patch(f"/api/v1/projects/{pid}/protocol/draft", json={"expected_row_version": v, "languages": ["French"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    r = c.patch(f"/api/v1/projects/{pid}/protocol/draft", json={"expected_row_version": v + 1, "bogus": 1})
    assert r.status_code == 422


# ---------- versioning after approval ----------


def approved_project(c, dispatcher) -> str:
    pid = project(c)
    started(c, dispatcher, pid)
    walk_to_review(c, dispatcher, pid)
    answer(c, dispatcher, pid, "approve")
    return pid


def test_approved_protocol_cannot_be_edited_only_revised_with_a_reason(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved_project(c, dispatcher)
    r = c.patch(f"/api/v1/projects/{pid}/protocol/draft", json={"expected_row_version": 1, "objective": "sneaky"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_draft"
    assert c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": ""}).status_code == 422


def test_revision_creates_a_new_version_and_reports_downstream_impact(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = approved_project(c, dispatcher)
    v1 = c.get(f"/api/v1/projects/{pid}/protocol").json()["approved"]
    r = c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "Reviewer asked for adults 65+"})
    assert r.status_code == 202, r.text
    drain(dispatcher)
    run = state(c, pid)
    assert run["stage"] == "PROTOCOL_REVIEW" and kind(run) == "review" and run["stage_before_revision"] == "SEARCH"
    d = draft(c, pid)
    assert d["version"] == 2 and d["change_reason"] == "Reviewer asked for adults 65+"
    assert d["supersedes_id"] == v1["id"]
    assert d["content"] == v1["content"]  # starts as an exact copy
    assert c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "again"}).status_code == 409

    crit = d["content"]["criteria"]
    crit[0] = {**crit[0], "text": "Population: adults aged 65 and over"}
    r = c.patch(
        f"/api/v1/projects/{pid}/protocol/draft", json={"expected_row_version": d["row_version"], "criteria": crit}
    )
    assert r.status_code == 200
    run = answer(c, dispatcher, pid, "approve")
    assert run["status"] == "idle" and run["stage"] == "SEARCH"

    proto = c.get(f"/api/v1/projects/{pid}/protocol").json()
    v2 = proto["approved"]
    assert v2["version"] == 2 and v2["content_hash"] != v1["content_hash"]
    imp = v2["impact"]
    assert imp["compared_with_version"] == 1 and imp["earliest_stage"] == "RECORD_SCREENING"
    assert imp["changes"] == [{"field": "criteria", "change": "changed", "stable_key": crit[0]["stable_key"]}]
    assert [v["status"] for v in proto["versions"]] == ["approved", "superseded"]
    old = c.get(f"/api/v1/projects/{pid}/protocol/versions/{v1['id']}").json()
    assert old["status"] == "superseded" and old["content"] == v1["content"]  # history unchanged

    # The database itself refuses to change the superseded version, even bypassing the service.
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(update(ProtocolVersion).where(ProtocolVersion.id == uuid.UUID(v1["id"])).values(objective="x"))
        db.flush()
    db.rollback()
    with pytest.raises(DBAPIError, match="cannot change protocol_criteria"):
        db.execute(text("UPDATE protocol_criteria SET text = 'x' WHERE protocol_version_id = :v"), {"v": v2["id"]})
    db.rollback()


def test_unchanged_revision_has_no_impact(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved_project(c, dispatcher)
    c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "Re-check, no change"})
    drain(dispatcher)
    answer(c, dispatcher, pid, "approve")
    imp = c.get(f"/api/v1/projects/{pid}/protocol").json()["approved"]["impact"]
    assert imp["changes"] == [] and imp["affected_stages"] == []


def test_deleting_a_project_removes_its_protocol_and_workflow(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = approved_project(c, dispatcher)
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204
    p = uuid.UUID(pid)
    assert db.scalar(select(func.count()).select_from(ProtocolVersion).where(ProtocolVersion.project_id == p)) == 0
    assert db.scalar(select(func.count()).select_from(WorkflowRun).where(WorkflowRun.project_id == p)) == 0


# ---------- bounded loops, inconsistency, recovery ----------


def test_iteration_cap_stops_the_run_with_a_reason_and_recover_resumes_from_tables(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    db.execute(update(WorkflowRun).where(WorkflowRun.id == uuid.UUID(run["id"])).values(max_iterations=2))
    db.commit()
    # the cap lives in the checkpointed state, so restart the thread from the tables to pick it up
    db.execute(
        update(WorkflowRun)
        .where(WorkflowRun.id == uuid.UUID(run["id"]))
        .values(status="failed", pending_question=None, pending_question_id=None)
    )
    db.commit()
    assert c.post(f"/api/v1/projects/{pid}/workflow/recover").status_code == 202
    drain(dispatcher)
    assert kind(state(c, pid)) == "review"  # a draft exists, so the rebuilt thread opens at the review
    answer(c, dispatcher, pid, "edit", {"section": "objective"}, version=False)
    run = answer(c, dispatcher, pid, "skip")
    assert run["status"] == "failed" and run["stop_reason"] == "max_iterations" and "limit 2" in run["error_summary"]
    assert (
        c.post(
            f"/api/v1/projects/{pid}/workflow/resume", json={"question_id": str(uuid.uuid4()), "action": "skip"}
        ).status_code
        == 409
    )
    assert c.post(f"/api/v1/projects/{pid}/workflow/recover").status_code == 202
    drain(dispatcher)
    assert state(c, pid)["status"] == "awaiting_input"


def test_checkpoint_table_disagreement_marks_needs_attention_and_tables_win(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)
    answer_body = state(c, pid)
    r = c.post(
        f"/api/v1/projects/{pid}/workflow/resume",
        json={
            "question_id": answer_body["pending_question_id"],
            "action": "save",
            "values": {"objective": "Kept"},
            "expected_row_version": draft(c, pid)["row_version"],
        },
    )
    assert r.status_code == 202
    # Simulate drift: the run row no longer points at the version recorded in the checkpoint.
    db.execute(update(WorkflowRun).where(WorkflowRun.project_id == uuid.UUID(pid)).values(protocol_version_id=None))
    db.commit()
    drain(dispatcher)
    run = state(c, pid)
    assert run["status"] == "needs_attention" and "records are kept" in run["error_summary"]
    assert c.get(f"/api/v1/projects/{pid}").json()["status"] == "needs_attention"
    assert c.post(f"/api/v1/projects/{pid}/workflow/recover").status_code == 202
    drain(dispatcher)
    run = state(c, pid)
    assert run["status"] == "awaiting_input" and kind(run) == "review"
    assert draft(c, pid)["content"]["objective"] == "Kept"  # the table's data won
    assert c.get(f"/api/v1/projects/{pid}").json()["status"] == "setup"


def test_archived_project_cannot_start_or_answer(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    p = c.get(f"/api/v1/projects/{pid}").json()
    r = c.patch(f"/api/v1/projects/{pid}", json={"expected_version": p["row_version"], "archived": True})
    assert r.status_code == 200 and r.json()["archived_at"]
    r = c.post(
        f"/api/v1/projects/{pid}/workflow/resume", json={"question_id": run["pending_question_id"], "action": "skip"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"
    other = project(c)
    assert c.patch(f"/api/v1/projects/{other}", json={"expected_version": 1, "archived": True}).status_code == 200
    r = c.post(f"/api/v1/projects/{other}/workflow")
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"


def test_workflow_messages_cannot_be_forged_through_chat(user_client, dispatcher, db):
    """A user message posted through the ordinary chat API never becomes a workflow answer."""
    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    r = c.post(
        f"/api/v1/projects/{pid}/conversations/{run['conversation_id']}/messages",
        json={"content": "approve", "client_message_id": run["pending_question_id"]},
    )
    assert r.status_code in (200, 201)
    assert state(c, pid)["pending_question_id"] == run["pending_question_id"]  # still waiting
    m = db.scalar(select(Message).where(Message.id == uuid.UUID(r.json()["message"]["id"])))
    assert m.payload is None or m.payload.get("type") != "workflow_answer"
