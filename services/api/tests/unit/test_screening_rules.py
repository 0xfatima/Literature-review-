"""Exclusion reasons and decision validation."""

import pytest

from app.screening import decisions as rules

CONTENT = {
    "criteria": [
        {"stable_key": "i1", "kind": "inclusion", "text": "RCTs", "state": "accepted"},
        {"stable_key": "e1", "kind": "exclusion", "text": "Animals", "state": "accepted"},
        {"stable_key": "e2", "kind": "exclusion", "text": "Rejected", "state": "rejected"},
    ]
}
OPTS = rules.reason_options(CONTENT)


def test_reasons_come_from_accepted_protocol_criteria_plus_generic_ones():
    codes = [r.code for r in OPTS]
    assert codes[:2] == ["exclusion:e1", "inclusion:i1"] and "exclusion:e2" not in codes
    assert {"off_topic", "wrong_publication_type", "duplicate", "other"} <= set(codes)


def test_valid_decisions():
    assert rules.check("include", None, " note ", OPTS, require_reason=False) == (None, None, "note")
    code, label, note = rules.check("exclude", "exclusion:e1", None, OPTS, require_reason=True)
    assert code == "exclusion:e1" and label.startswith("Meets exclusion criterion") and note is None
    assert rules.check("exclude", None, None, OPTS, require_reason=False) == (None, None, None)


@pytest.mark.parametrize(
    ("args", "field"),
    [
        (("cleared", None, None), "decision"),
        (("exclude", None, None), "reason_code"),  # with require_reason=True below
        (("exclude", "exclusion:e2", None), "reason_code"),
        (("exclude", "other", None), "note"),
        (("include", "exclusion:e1", None), "reason_code"),
        (("uncertain", None, "x" * 2001), "note"),
    ],
)
def test_invalid_decisions(args, field):
    with pytest.raises(rules.DecisionError) as e:
        rules.check(*args, OPTS, require_reason=True)
    assert e.value.field == field
