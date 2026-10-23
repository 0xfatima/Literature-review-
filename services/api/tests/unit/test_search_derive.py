"""Turning an approved protocol into a search expression and a filter plan (deterministic, no AI)."""

from app.search import derive
from app.search import query as q


def content(**kw):
    base = {
        "objective": None,
        "framework": "pico",
        "framework_elements": [],
        "search_strategy": None,
        "languages": [],
        "date_from": None,
        "date_to": None,
        "study_designs": [],
    }
    return {**base, **kw}


def test_a_boolean_search_strategy_is_used_as_written():
    d = derive.derive(content(search_strategy='("older adults" OR elderly) AND exercise'), "topic")
    assert d.source == "search_strategy"
    assert d.expression == '("older adults" OR elderly) AND exercise'


def test_prose_strategy_falls_back_to_search_concepts_and_leaves_outcomes_for_screening():
    els = [
        {"key": "population", "label": "Population", "value": "adults over 60 with depression; older adults"},
        {"key": "intervention", "label": "Intervention", "value": "aerobic exercise, running"},
        {"key": "comparison", "label": "Comparison", "value": "usual care"},
        {"key": "outcome", "label": "Outcome", "value": "depressive symptoms"},
    ]
    d = derive.derive(content(search_strategy="Search the main databases.", framework_elements=els), "t")
    assert d.source == "framework_elements"
    # a synonym of more than four words becomes an AND of its content words; short ones stay phrases
    assert d.expression == '((adults AND depression) OR "older adults") AND ("aerobic exercise" OR running)'
    q.parse(d.expression)  # always a valid expression
    text = " ".join(d.explanation)
    assert "prose" in text and "Comparison" in text and "Outcome" in text
    assert "usual care" not in d.expression and "symptoms" not in d.expression


def test_a_long_synonym_becomes_an_and_of_its_content_words():
    els = [{"key": "population", "label": "P", "value": "community dwelling adults living alone in rural areas"}]
    d = derive.derive(content(framework_elements=els), "t")
    assert d.expression.startswith("(") and " AND " in d.expression and '"' not in d.expression


def test_without_concepts_the_objective_then_the_topic_give_a_keyword_query():
    d = derive.derive(content(objective="Effect of tai chi on balance"), "ignored topic")
    assert d.source == "objective" and d.expression == "tai AND chi AND balance"
    d = derive.derive(content(), "Yoga for chronic low back pain")
    assert d.source == "topic" and "yoga" in d.expression
    assert any("review and edit" in n.lower() for n in d.explanation)


def test_nothing_usable_yields_an_empty_expression_with_an_explanation():
    d = derive.derive(content(), "the of and")
    assert d.source == "none" and d.expression == "" and d.explanation


def test_filter_plan_maps_languages_and_reports_unknown_ones():
    fp = derive.filter_plan(
        content(languages=["English", "fr", "deu", "Klingon"], date_from=2000, date_to=2020, study_designs=["RCT"])
    )
    assert fp.languages == [("en", "eng"), ("fr", "fre")]
    assert fp.unrecognised_languages == ["deu", "Klingon"]  # ISO 639-2/T 'deu' is not the /B code 'ger'
    assert fp.as_dict()["study_designs"] == ["RCT"]
