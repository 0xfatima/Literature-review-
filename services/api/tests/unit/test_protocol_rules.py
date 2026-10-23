# stdlib-only
"""Protocol domain rules: framework advisor, normalisation, approval validation, diff, impact, suggestions."""

import uuid
from datetime import date

from app.protocol import content as pc
from app.protocol import stages
from app.protocol.frameworks import FRAMEWORKS, KeywordFrameworkAdvisor
from app.protocol.suggestions import propose_criteria, propose_question
from tests.helpers import raises

TODAY = date(2026, 10, 23)
K1, K2, K3 = (str(uuid.UUID(int=i)) for i in (1, 2, 3))
NS = uuid.UUID(int=99)


def approvable() -> dict:
    return pc.normalize(
        {
            "objective": "Assess whether aerobic exercise reduces depressive symptoms in older adults.",
            "framework": "pico",
            "framework_source": "suggested",
            "framework_elements": [{"key": "population", "value": "adults over 60"}],
            "questions": [{"stable_key": K1, "text": "Does exercise reduce depression?"}],
            "criteria": [{"stable_key": K2, "kind": "inclusion", "text": "Adults aged 60+", "origin": "user"}],
            "date_from": 2000,
            "date_to": 2025,
            "sources": ["OpenAlex"],
        },
        today=TODAY,
    )


# ---------- framework advisor ----------


def test_advisor_suggests_pico_for_intervention_wording_and_explains_why():
    s = KeywordFrameworkAdvisor().suggest("Effectiveness of exercise therapy compared with usual care")
    assert s.framework == "pico"
    assert "effectiveness" in s.matched_terms and "'effectiveness'" in s.rationale
    assert s.method == "keyword_rules_v1"


def test_advisor_suggests_peco_for_exposures_and_spider_for_experiences():
    assert KeywordFrameworkAdvisor().suggest("Air pollution exposure and asthma in children").framework == "peco"
    assert KeywordFrameworkAdvisor().suggest("Lived experiences of nurses: a qualitative synthesis").framework == (
        "spider"
    )


def test_advisor_never_guesses_when_nothing_matches():
    s = KeywordFrameworkAdvisor().suggest("Graph neural networks for molecules")
    assert s.framework == "custom" and s.matched_terms == ()
    assert "No wording" in s.rationale


def test_advisor_matches_whole_words_only():
    # 'reduce' must not match inside 'reducer'; 'program' must not match inside 'programming'
    assert KeywordFrameworkAdvisor().suggest("A reducer for functional programming").framework == "custom"


def test_every_framework_suggested_by_the_advisor_exists():
    for text in ("effect of x", "exposure to y", "experiences of z", "nothing"):
        assert KeywordFrameworkAdvisor().suggest(text).framework in FRAMEWORKS


# ---------- normalisation ----------


def test_normalize_trims_dedupes_and_orders_framework_elements():
    c = pc.normalize(
        {
            "objective": "  Reduce   falls ",
            "languages": ["English", "english", " French "],
            "framework": "pico",
            "framework_elements": [{"key": "outcome", "value": "falls"}, {"key": "population", "value": "older"}],
        },
        today=TODAY,
    )
    assert c["objective"] == "Reduce falls"
    assert c["languages"] == ["English", "French"]
    assert [e["key"] for e in c["framework_elements"]] == ["population", "intervention", "comparison", "outcome"]
    assert c["framework_elements"][0]["value"] == "older" and c["framework_elements"][1]["value"] is None


def test_normalize_reports_every_problem_with_its_field():
    try:
        pc.normalize(
            {
                "framework": "nope",
                "date_from": 1700,
                "date_to": "2020",
                "criteria": [{"stable_key": "not-a-uuid", "kind": "maybe", "text": ""}],
                "questions": [{"stable_key": K1, "text": "q"}, {"stable_key": K1, "text": "dup"}],
            },
            today=TODAY,
        )
    except pc.ContentError as e:
        fields = {d["field"] for d in e.details}
        assert {"framework", "date_from", "date_to", "criteria[0].stable_key", "criteria[0].kind"} <= fields
        assert "questions[1].stable_key" in fields
    else:
        raise AssertionError("expected ContentError")


def test_wrong_container_types_are_rejected_not_coerced():
    with raises(pc.ContentError):
        pc.normalize({"questions": "one question"}, today=TODAY)
    with raises(pc.ContentError):
        pc.normalize({"date_from": True}, today=TODAY)  # bool is an int subclass in Python


def test_users_own_criterion_is_accepted_but_a_suggestion_stays_proposed():
    c = pc.normalize(
        {
            "criteria": [
                {"stable_key": K1, "kind": "inclusion", "text": "mine", "origin": "user", "state": "proposed"},
                {"stable_key": K2, "kind": "exclusion", "text": "theirs", "origin": "suggested", "state": "proposed"},
            ]
        },
        today=TODAY,
    )
    assert [x["state"] for x in c["criteria"]] == ["accepted", "proposed"]


def test_custom_framework_keeps_user_defined_labels():
    c = pc.normalize(
        {"framework": "custom", "framework_elements": [{"label": "Setting", "value": "rural clinics"}]}, today=TODAY
    )
    assert c["framework_elements"] == [{"key": "setting", "label": "Setting", "value": "rural clinics"}]


def test_framework_none_drops_elements():
    c = pc.normalize({"framework": "none", "framework_elements": [{"key": "population", "value": "x"}]}, today=TODAY)
    assert c["framework_elements"] == []


# ---------- approval validation ----------


def test_complete_protocol_has_no_blocking_issues():
    assert pc.blocking(pc.approval_issues(approvable())) == []


def test_missing_parts_block_approval_with_specific_messages():
    issues = pc.approval_issues(pc.empty())
    blocked = {i.field for i in pc.blocking(issues)}
    assert blocked == {"objective", "framework", "questions", "criteria"}


def test_undecided_suggestion_blocks_approval():
    c = approvable()
    c["criteria"].append(
        {
            "stable_key": K3,
            "kind": "exclusion",
            "text": "x",
            "rationale": None,
            "origin": "suggested",
            "state": "proposed",
        }
    )
    assert any("still need" in i.message for i in pc.blocking(pc.approval_issues(c)))


def test_inverted_date_range_blocks_and_open_range_only_warns():
    c = approvable()
    c["date_from"], c["date_to"] = 2025, 2000
    assert [i.field for i in pc.blocking(pc.approval_issues(c))] == ["date_range"]
    c["date_from"] = c["date_to"] = None
    assert pc.blocking(pc.approval_issues(c)) == []
    assert any(i.field == "date_range" and i.severity == "warning" for i in pc.approval_issues(c))


# ---------- hash, summary ----------


def test_content_hash_is_stable_and_sensitive():
    a, b = approvable(), approvable()
    assert pc.content_hash(a) == pc.content_hash(b)
    b["criteria"][0]["text"] = "Adults aged 65+"
    assert pc.content_hash(a) != pc.content_hash(b)


def test_summary_contains_only_stored_content_and_hides_rejected_suggestions():
    c = approvable()
    c["criteria"].append(
        {
            "stable_key": K3,
            "kind": "exclusion",
            "text": "Rejected idea",
            "rationale": None,
            "origin": "suggested",
            "state": "rejected",
        }
    )
    text = "\n".join(pc.summary_lines(c))
    assert "Adults aged 60+" in text and "Rejected idea" not in text
    assert "Publication years: 2000 to 2025" in text and "Sources: OpenAlex" in text


# ---------- diff and impact ----------


def test_identical_versions_have_no_changes_and_no_impact():
    assert pc.diff(approvable(), approvable()) == []
    assert stages.impact([])["affected_stages"] == []


def test_criterion_change_affects_screening_onwards_but_not_search():
    old, new = approvable(), approvable()
    new["criteria"][0]["text"] = "Adults aged 65+"
    changes = pc.diff(old, new)
    assert changes == [{"field": "criteria", "change": "changed", "stable_key": K2}]
    imp = stages.impact(changes)
    assert imp["earliest_stage"] == "RECORD_SCREENING"
    assert imp["affected_stages"] == ["RECORD_SCREENING", "FULL_TEXT_SCREENING", "EXTRACTION", "SYNTHESIS", "REPORT"]


def test_scope_change_reaches_back_to_search():
    old, new = approvable(), approvable()
    new["date_from"] = 2010
    new["criteria"][0]["text"] = "changed too"
    imp = stages.impact(pc.diff(old, new))
    assert imp["earliest_stage"] == "SEARCH" and imp["affected_stages"][0] == "SEARCH"
    assert set(imp["triggers"]) == {"SEARCH", "RECORD_SCREENING"}


def test_rejecting_an_already_rejected_suggestion_or_reordering_is_not_material():
    old = approvable()
    old["questions"].append({"stable_key": K3, "text": "second"})
    new = approvable()
    new["questions"] = [{"stable_key": K3, "text": "second"}, old["questions"][0]]
    changes = pc.diff(old, new)
    assert changes == [{"field": "questions", "change": "reordered"}]
    assert stages.impact(changes)["affected_stages"] == []


def test_unaccepting_a_criterion_is_a_removal():
    old, new = approvable(), approvable()
    new["criteria"][0]["state"] = "rejected"
    assert pc.diff(old, new) == [{"field": "criteria", "change": "removed", "stable_key": K2}]


def test_backtrack_never_moves_forward():
    assert stages.backtrack_target("SEARCH", "RECORD_SCREENING") == "SEARCH"
    assert stages.backtrack_target("EXTRACTION", "SEARCH") == "SEARCH"
    assert stages.backtrack_target("EXTRACTION", None) == "EXTRACTION"


# ---------- suggestions ----------


def test_suggested_criteria_restate_answers_and_start_proposed():
    c = pc.normalize(
        {
            "framework": "pico",
            "framework_elements": [{"key": "population", "value": "adults over 60"}],
            "date_from": 2000,
            "languages": ["English"],
        },
        today=TODAY,
    )
    proposed = propose_criteria(c, NS)
    texts = [p["text"] for p in proposed]
    assert "Population: adults over 60" in texts and "Published from 2000 to the present." in texts
    assert all(p["state"] == "proposed" and p["origin"] == "suggested" and p["rationale"] for p in proposed)


def test_suggestions_are_deterministic_and_not_repeated():
    c = pc.normalize({"framework": "pico", "framework_elements": [{"key": "population", "value": "x"}]}, today=TODAY)
    first = propose_criteria(c, NS)
    assert first == propose_criteria(c, NS)
    c["criteria"] = first
    assert propose_criteria(c, NS) == []


def test_no_suggestions_from_an_empty_protocol():
    assert propose_criteria(pc.empty(), NS) == []
    assert propose_question(pc.empty()) is None


def test_question_template_uses_only_given_elements():
    c = pc.normalize(
        {
            "framework": "pico",
            "framework_elements": [
                {"key": "population", "value": "older adults"},
                {"key": "intervention", "value": "aerobic exercise"},
                {"key": "outcome", "value": "depressive symptoms"},
            ],
        },
        today=TODAY,
    )
    assert propose_question(c) == "In older adults, what is the effect of aerobic exercise on depressive symptoms?"
