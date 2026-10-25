# stdlib-only
"""Deterministic tests for the workspace actions: search terms, verbatim passage search, exclusion explanations."""

from app.assist import logic

PAGES = [
    (1, "Introduction. Physical activity matters for older adults."),
    (2, "Methods. We enrolled 120 adult participants aged over 65 in a randomised trial of aerobic exercise."),
    (3, "Results. Aerobic exercise reduced depressive symptoms in adult participants at 12 weeks."),
]


def test_terms_from_query_or_field():
    assert logic.terms_for("adult participants and the trial", None) == ["adult", "participants", "trial"]
    field = {"field_key": "design", "label": "Study design", "options": ["RCT", "Cohort"]}
    assert logic.terms_for(None, field) == ["design", "rct", "cohort"]
    assert logic.terms_for("", None) == []


def test_passages_are_exact_substrings_ranked_by_matched_terms():
    hits = logic.search_pages(PAGES, ["adult", "participants", "randomised"])
    assert [h["page_no"] for h in hits] == [2, 3, 1]
    assert hits[0]["matched_terms"] == ["adult", "participants", "randomised"]
    for h in hits:
        assert h["excerpt"] in dict(PAGES)[h["page_no"]]
    assert logic.search_pages(PAGES, ["zebra"]) == [] and logic.search_pages(PAGES, []) == []


def test_long_pages_give_bounded_windows_on_word_boundaries():
    text = "word " * 300 + "the sample size was 240 adults " + "word " * 300
    hit = logic.search_pages([(7, text)], ["sample", "adults"])[0]
    assert hit["excerpt"] in text and len(hit["excerpt"]) < 500
    assert hit["truncated_start"] and hit["truncated_end"]
    assert not hit["excerpt"].startswith("ord")


def test_explanations_come_only_from_the_record():
    stages = [
        {
            "stage": "title_abstract",
            "decision": "exclude",
            "reason_code": "exclusion:k",
            "criterion_kind": "exclusion",
            "criterion_text": "Animal studies",
            "note": "Mice only.",
            "decided_at": "2026-10-01T10:00:00",
            "current_protocol": False,
        }
    ]
    ai = [{"method": "llm", "band": "low", "score": 12, "suggestion": "exclude", "summary": "About mice."}]
    lines = logic.explain_lines(stages, ai)
    assert lines[0].startswith("Excluded at title/abstract screening on 2026-10-01 by a reviewer")
    assert "Animal studies" in lines[0] and "meet this exclusion criterion" in lines[0]
    assert lines[1] == "Reviewer's note: Mice only."
    assert "earlier protocol version" in lines[2]
    assert "advisory only, never a decision" in lines[3] and "About mice." in lines[3]
    assert logic.explain_lines([], []) == ["No screening decision has been recorded for this record yet."]
    no_reason = logic.explain_lines([{"stage": "full_text", "decision": "exclude"}], [])
    assert "without a recorded reason" in no_reason[0]
