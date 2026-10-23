"""Deterministic validation of AI relevance answers: AI proposes, code validates, humans decide."""

from app.screening import ai_assessment as ai

CONTENT = {
    "objective": "Effect of exercise on depression",
    "questions": [{"text": "Does exercise reduce depression?"}],
    "criteria": [
        {"stable_key": "c1", "kind": "inclusion", "text": "Randomised trials", "state": "accepted"},
        {"stable_key": "c2", "kind": "exclusion", "text": "Children", "state": "accepted"},
        {"stable_key": "c3", "kind": "exclusion", "text": "Rejected one", "state": "rejected"},
    ],
}
P = ai.protocol_input(CONTENT)
PAPERS = [
    ai.PaperInput(
        "P1",
        "id-1",
        "Exercise in children with depression",
        "We enrolled 40 children aged 8 to 12.",
        [],
        2020,
        "article",
    ),
    ai.PaperInput("P2", "id-2", "Running and mood", None, [], 2019, None),
]


def answer(paper, **kw):
    base = {
        "paper": paper,
        "relevance": 80,
        "relevance_reason": "r",
        "insufficient_information": False,
        "eligibility_suggestion": "include",
        "criteria": [],
    }
    return {**base, **kw}


def test_protocol_input_numbers_questions_and_only_accepted_criteria():
    assert P.questions == [("Q1", "Does exercise reduce depression?")]
    assert [c[0] for c in P.criteria] == ["C1", "C2"]


def test_prompt_contains_records_by_short_id_and_no_internal_ids():
    text = ai.build_prompt(P, PAPERS)
    assert "--- P1" in text and "--- P2" in text and "id-1" not in text
    assert "(no abstract available)" in text and "C2 [exclusion]: Children" in text


def test_an_exclusion_backed_by_a_verbatim_quote_is_kept():
    v, missing, problems = ai.validate(
        {
            "assessments": [
                answer(
                    "P1",
                    eligibility_suggestion="exclude",
                    criteria=[
                        {
                            "criterion": "C2",
                            "judgement": "met",
                            "evidence_quote": "we enrolled 40  children aged 8 to 12",
                        }
                    ],
                )
            ]
        },
        P,
        PAPERS[:1],
    )
    assert not missing and not problems
    assert v[0].suggestion == "exclude" and v[0].suggestion_detail["criteria"][0]["quote_verified"]
    assert v[0].band == "high" and v[0].paper_id == "id-1"


def test_an_invented_quote_is_removed_and_exclusion_becomes_uncertain():
    v, _, _ = ai.validate(
        {
            "assessments": [
                answer(
                    "P1",
                    eligibility_suggestion="exclude",
                    criteria=[
                        {
                            "criterion": "C2",
                            "judgement": "met",
                            "evidence_quote": "all participants were children under five",
                        }
                    ],
                )
            ]
        },
        P,
        PAPERS[:1],
    )
    c = v[0].suggestion_detail["criteria"][0]
    assert v[0].suggestion == "uncertain" and c["evidence_quote"] is None and not c["quote_verified"]
    assert any("not found in the record" in w for w in v[0].warnings)


def test_an_exclusion_without_any_criterion_evidence_becomes_uncertain():
    v, _, _ = ai.validate({"assessments": [answer("P1", eligibility_suggestion="exclude")]}, P, PAPERS[:1])
    assert v[0].suggestion == "uncertain"


def test_insufficient_information_forces_uncertain_and_its_band():
    v, _, _ = ai.validate({"assessments": [answer("P2", relevance=20)]}, P, PAPERS)
    assert v[0].suggestion == "uncertain" and v[0].band == "insufficient" and v[0].confidence == "low"


def test_unknown_records_criteria_and_bad_scores_are_dropped_and_reported():
    v, missing, problems = ai.validate(
        {
            "assessments": [
                answer("P9"),
                answer("P1", criteria=[{"criterion": "C99", "judgement": "met"}]),
                answer("P1"),  # repeated
                answer("P2", relevance="high"),
            ]
        },
        P,
        PAPERS,
    )
    assert [x.paper_id for x in v] == ["id-1"]
    assert missing == ["P2"]
    assert any("P9" in p for p in problems) and any("not a number" in p for p in problems)
    assert any("unknown criterion" in w for w in v[0].warnings)


def test_scores_are_clamped_and_a_missing_list_is_handled():
    v, _, _ = ai.validate({"assessments": [answer("P1", relevance=250)]}, P, PAPERS[:1])
    assert v[0].score == 100
    v, missing, problems = ai.validate({"nope": []}, P, PAPERS)
    assert v == [] and missing == ["P1", "P2"] and problems
