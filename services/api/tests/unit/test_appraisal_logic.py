# stdlib-only
"""Deterministic tests: design-specific frameworks, overall-judgement rules (no numeric score), framework suggestion,
and validation of AI appraisal suggestions."""

from app.appraisal import ai
from app.appraisal import frameworks as fwm
from app.extraction.extractor import PageText

RCT = fwm.FRAMEWORKS["rct_rob"]
QUAL = fwm.FRAMEWORKS["qualitative_checklist"]
PAGE = PageText("pg1", 3, "Allocation was concealed using sealed opaque envelopes. Outcome assessors were blinded.")


def test_every_framework_is_design_specific_and_has_no_numeric_score():
    assert len(fwm.FRAMEWORKS) >= 5
    for fw in fwm.FRAMEWORKS.values():
        assert fw.designs and fw.domains and fw.reference_url.startswith("https://")
        assert len({d.key for d in fw.domains}) == len(fw.domains)
        assert all(not o.isdigit() for o in fw.overall_options)
        if fw.severity:
            assert set(fw.severity) == set(fw.judgements) and set(fw.overall_map) == set(fw.judgements)
            assert set(fw.overall_map.values()) <= set(fw.overall_options)
        else:
            assert fw.kind == "checklist"


def test_suggested_overall_is_worst_domain_and_waits_for_all_domains():
    j = {d.key: "low" for d in RCT.domains}
    assert fwm.suggested_overall(RCT, j)[0] == "low"
    j["missing_outcome_data"] = "high"
    overall, why = fwm.suggested_overall(RCT, j)
    assert overall == "high" and "Missing outcome data" in why
    j["missing_outcome_data"] = "no_information"
    assert fwm.suggested_overall(RCT, j)[0] == "some_concerns"
    j.pop("deviations")
    assert fwm.suggested_overall(RCT, j)[0] is None


def test_checklists_never_compute_an_overall():
    assert fwm.suggested_overall(QUAL, {d.key: "yes" for d in QUAL.domains})[0] is None


def test_framework_suggestion_from_design_text():
    assert fwm.suggest("Randomised controlled trial") == "rct_rob"
    assert fwm.suggest("Non-randomised controlled before-and-after study") == "nrsi_rob"
    assert fwm.suggest(None, "prospective cohort study") == "observational_nos"
    assert fwm.suggest("Cross-sectional survey") == "cross_sectional_checklist"
    assert fwm.suggest("Semi-structured interviews") == "qualitative_checklist"
    assert fwm.suggest("Editorial") is None and fwm.suggest(None) is None


def test_ai_suggestion_needs_a_verified_quote_for_a_substantive_judgement():
    data = {
        "domains": [
            {
                "domain": "randomisation",
                "judgement": "low",
                "justification": "Concealed.",
                "page": 3,
                "quote": "sealed opaque envelopes",
            },
            {"domain": "outcome_measurement", "judgement": "low", "quote": "assessors were never told"},
            {"domain": "deviations", "judgement": "no_information"},
            {"domain": "bogus", "judgement": "low"},
            {"domain": "missing_outcome_data", "judgement": "excellent"},
        ]
    }
    out = ai.validate(RCT, [PAGE], data)
    assert out["randomisation"].supported and out["randomisation"].excerpt == "sealed opaque envelopes"
    assert out["randomisation"].page_no == 3
    assert out["outcome_measurement"].judgement == "no_information" and not out["outcome_measurement"].supported
    assert out["deviations"].judgement == "no_information"
    assert "bogus" not in out and "missing_outcome_data" not in out


def test_merge_prefers_supported_and_takes_the_more_serious_judgement():
    a = {"randomisation": ai.Suggestion("low", "x", "q", "p", 1, supported=True)}
    b = {"randomisation": ai.Suggestion("high", "y", "q2", "p", 2, chunk_index=1, supported=True)}
    c = {"randomisation": ai.Suggestion("no_information", "z")}
    merged = ai.merge(RCT, [a, b, c])
    assert merged["randomisation"].judgement == "high" and "different judgement" in (
        merged["randomisation"].justification or ""
    )
    assert ai.merge(RCT, [c])["randomisation"].judgement == "no_information"


def test_serious_overall_detection():
    assert fwm.is_serious("rct_rob", "high") and fwm.is_serious("nrsi_rob", "critical")
    assert not fwm.is_serious("rct_rob", "some_concerns") and not fwm.is_serious("x", None)
