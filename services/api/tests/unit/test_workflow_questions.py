# stdlib-only
"""Workflow question rules: ids must be deterministic (LangGraph re-runs a node on resume) and routing honest."""

from app.workflows import questions as qs


def test_question_ids_are_deterministic_and_distinct():
    a = qs.build("objective", thread_id="t1", step=0, protocol_version_id="v")
    assert a == qs.build("objective", thread_id="t1", step=0, protocol_version_id="v")
    assert a["id"] != qs.build("objective", thread_id="t1", step=1, protocol_version_id="v")["id"]
    assert a["id"] != qs.build("objective", thread_id="t2", step=0, protocol_version_id="v")["id"]
    assert a["id"] != qs.build("framework", thread_id="t1", step=0, protocol_version_id="v")["id"]


def test_review_offers_approve_and_edit_every_other_step_save_and_skip():
    assert qs.build("review", thread_id="t", step=0, protocol_version_id="v")["actions"] == ["approve", "edit"]
    for node in qs.EDITABLE_SECTIONS:
        assert qs.build(node, thread_id="t", step=0, protocol_version_id="v")["actions"] == ["save", "skip"]


def test_default_order_and_elements_skipped_without_a_framework():
    assert qs.ORDER == ("objective", "framework", "elements", "questions", "scope", "criteria", "review")
    assert qs.default_next("framework", framework="pico") == "elements"
    assert qs.default_next("framework", framework="none") == "questions"
    assert qs.default_next("framework", framework=None) == "questions"
    assert qs.default_next("criteria", framework="pico") == "review"


def test_only_the_review_is_a_protocol_review_stage():
    stages = {n: qs.build(n, thread_id="t", step=0, protocol_version_id="v")["stage"] for n in qs.ORDER}
    assert stages.pop("review") == "PROTOCOL_REVIEW" and set(stages.values()) == {"DRAFT"}
