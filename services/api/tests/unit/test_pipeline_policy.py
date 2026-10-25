# stdlib-only
"""Deterministic tests: retry/backoff policy, budgets, autonomy defaults, output diffs, and criteria-change impact."""

from datetime import UTC, datetime, timedelta

from app.pipeline import impact, policy

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def test_transient_failures_retry_with_bounded_exponential_backoff():
    d0 = policy.retry_decision(0, "Worker lost; retry", max_retries=3, base_s=30, cap_s=100, now=NOW)
    assert d0.retry and d0.attempt == 1 and d0.not_before == NOW + timedelta(seconds=30)
    d2 = policy.retry_decision(2, "request timed out", max_retries=3, base_s=30, cap_s=100, now=NOW)
    assert d2.not_before == NOW + timedelta(seconds=100)  # 120 capped at 100
    assert not policy.retry_decision(3, "timeout", max_retries=3, base_s=30, cap_s=100, now=NOW).retry
    hard = policy.retry_decision(
        0, "The extraction schema is no longer available.", max_retries=3, base_s=1, cap_s=1, now=NOW
    )
    assert not hard.retry and "person" in hard.reason


def test_budgets_autonomy_and_diffs():
    assert policy.over_budget(10, 10, 0, 5) and policy.over_budget(0, 0, 5, 5)
    assert policy.over_budget(9, 10, 4, 5) is None and policy.over_budget(99, 0, 0, 0) is None
    a = policy.normalise_autonomy({"search": True, "bogus": True})
    assert a["search"] is True and "bogus" not in a and a["extraction"] is False
    assert policy.downstream("screening") == ["extraction", "appraisal", "synthesis", "report"]
    assert policy.diff_outputs({"included_studies": 12}, {"included_studies": 10, "screened": 40}) == [
        "included studies: 12 → 10",
        "screened: — → 40",
    ]


OLD = [
    {"stable_key": "a", "kind": "inclusion", "text": "Adults", "state": "accepted"},
    {"stable_key": "b", "kind": "exclusion", "text": "Animal studies", "state": "accepted"},
    {"stable_key": "c", "kind": "exclusion", "text": "Conference abstracts", "state": "accepted"},
]


def test_criteria_change_never_silently_keeps_decisions():
    new = [
        {"stable_key": "a", "kind": "inclusion", "text": "Adults aged 65 or older", "state": "accepted"},
        {"stable_key": "c", "kind": "exclusion", "text": "Conference abstracts", "state": "accepted"},
    ]
    decisions = [
        {"paper_id": "p1", "decision": "exclude", "reason_code": "exclusion:b"},  # criterion removed
        {"paper_id": "p2", "decision": "exclude", "reason_code": "exclusion:c"},  # unchanged criterion
        {"paper_id": "p3", "decision": "exclude", "reason_code": "inclusion:a"},  # wording changed
        {"paper_id": "p4", "decision": "include", "reason_code": None},  # criteria changed since inclusion
        {"paper_id": "p5", "decision": "exclude", "reason_code": "wrong_study_design"},  # generic reason
    ]
    res = impact.classify(decisions, OLD, new)
    assert res["criteria_changes"] == {"added": [], "removed": ["b"], "changed": ["a"]}
    assert [d["paper_id"] for d in res["needs_redecision"]] == ["p1"]
    assert [d["paper_id"] for d in res["needs_review"]] == ["p3", "p4"]
    assert [d["paper_id"] for d in res["carry_forward"]] == ["p2", "p5"]
    assert res["counts"] == {"needs_redecision": 1, "needs_review": 2, "carry_forward": 2}


def test_unchanged_criteria_only_carry_forward():
    res = impact.classify([{"paper_id": "p", "decision": "include", "reason_code": None}], OLD, OLD)
    assert res["counts"]["carry_forward"] == 1 and res["counts"]["needs_review"] == 0
