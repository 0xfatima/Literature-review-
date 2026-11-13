"""Transparent word-overlap relevance (lexical-v2). Relevance is not eligibility."""

import pytest

from app.screening import relevance as lex

CONTENT = {
    "framework": "pico",
    "framework_elements": [
        {"key": "population", "label": "Population", "value": "adults with depression; depressive disorder"},
        {"key": "intervention", "label": "Intervention", "value": "aerobic exercise, running"},
        {"key": "outcome", "label": "Outcome", "value": "symptoms"},
    ],
    "questions": [{"stable_key": "q1", "text": "Does aerobic exercise reduce depressive symptoms in adults?"}],
}


def test_stemmer_matches_word_families_but_leaves_short_words():
    assert lex.stem("depression") == lex.stem("depressive") == "depress"
    assert lex.stem("running") == "runn" and lex.stem("runs") == "runs"


def test_concepts_use_search_elements_and_questions_only():
    cs = lex.concepts_from_protocol(CONTENT)
    assert [c.id for c in cs] == ["el:population", "el:intervention", "q:q1"]
    assert cs[0].groups == [["adult", "depress"], ["depress", "disord"]]


def test_custom_framework_uses_every_element():
    cs = lex.concepts_from_protocol(
        {"framework": "custom", "framework_elements": [{"key": "setting", "label": "Setting", "value": "rural"}]}
    )
    assert [c.id for c in cs] == ["el:setting"]


def test_a_matching_record_scores_high_and_explains_where_each_word_matched():
    a = lex.assess(
        {"title": "Aerobic exercise for depressive disorder in adults", "abstract": "Running helped.", "keywords": []},
        lex.concepts_from_protocol(CONTENT),
    )
    assert a.band == "high" and a.score >= 60 and a.confidence == "normal"
    pop = a.explanation["concepts"][0]
    assert pop["best_field"] == "title" and pop["score"] == 100
    assert "not eligibility" in a.explanation["summary"]


def test_field_weights_rank_title_above_abstract():
    cs = lex.concepts_from_protocol(CONTENT)
    title = lex.assess({"title": "Aerobic exercise in adults with depression", "abstract": "x y z"}, cs)
    abstract = lex.assess({"title": "A trial", "abstract": "Aerobic exercise in adults with depression"}, cs)
    assert title.score > abstract.score


def test_unrelated_record_is_low_and_missing_metadata_is_insufficient():
    cs = lex.concepts_from_protocol(CONTENT)
    assert lex.assess({"title": "Soil microbiome of wheat fields", "abstract": "Bacteria."}, cs).band == "low"
    a = lex.assess({"title": "Depression", "abstract": None}, cs)
    assert a.band == "insufficient" and a.confidence == "low"


def test_no_protocol_concepts_means_not_assessed():
    a = lex.assess({"title": "Anything", "abstract": "text"}, [])
    assert a.band == "not_assessed" and a.score == 0


@pytest.mark.parametrize(
    ("score", "band"), [(65, "high"), (64, "moderate_high"), (40, "moderate_high"), (20, "possible"), (19, "low")]
)
def test_lexical_band_thresholds(score, band):
    assert lex.band_for(score, lex.LEXICAL_THRESHOLDS) == band


def test_search_concepts_drive_the_score_and_long_questions_saturate():
    concepts = lex.concepts_from_protocol(
        {"questions": [{"text": "How much is quantum machine learning clinically deployable right now?"}]},
        '("quantum machine learning" OR qml) AND (clinic* AND deploy*)',
    )
    assert [c.kind for c in concepts] == ["search_concept", "search_concept", "research_question"]
    on_topic = lex.assess(
        {
            "title": "Quantum technologies in clinical medicine",
            "abstract": "Deploying quantum machine learning in hospitals.",
            "keywords": [],
        },
        concepts,
    )
    partly = lex.assess(
        {"title": "Quantum adversarial machine learning", "abstract": "Robust models.", "keywords": []}, concepts
    )
    off = lex.assess({"title": "Deep learning for crop yields", "abstract": "Farming.", "keywords": []}, concepts)
    assert on_topic.band == "high" and partly.band == "moderate_high" and off.band == "low"
    assert lex.stem("clinically") == lex.stem("clinical") == "clinic"
