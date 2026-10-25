# stdlib-only
"""Deterministic tests: grouping, agreement/disagreement/gap signals, certainty rules (never raised above the
evidence), statements derived without AI, and validation of AI synthesis drafts (citations, no pooling)."""

from app.synthesis import ai
from app.synthesis import signals as sig

FIELDS = [
    {"field_key": "design", "label": "Design", "type": "choice", "options": ["RCT", "Cohort"]},
    {"field_key": "n", "label": "Sample size", "type": "number"},
    {"field_key": "effect", "label": "Effect direction", "type": "choice", "options": ["benefit", "no effect"]},
    {"field_key": "notes", "label": "Notes", "type": "text"},
]
LOW = {"record_id": "r", "revision": 1, "framework_key": "rct_rob", "overall": "low"}
HIGH = {"record_id": "r2", "revision": 1, "framework_key": "rct_rob", "overall": "high"}


def cell(pid, key, value, status="found", review="approved", excerpt=None):
    return sig.Cell(f"{pid}-{key}", key, 1, status, value, excerpt, 2 if status == "found" else None, review)


def study(pid, design, n, effect, appraisal=LOW, notes="x"):
    s = sig.Study(pid, f"Study {pid}", 2020, {}, appraisal)
    for k, v in (("design", design), ("n", n), ("effect", effect), ("notes", notes)):
        if v == "NL":
            s.cells[k] = cell(pid, k, None, status="not_located")
        elif v == "NA":
            s.cells[k] = cell(pid, k, None, status="unavailable")
        elif v == "UNREVIEWED":
            s.cells[k] = cell(pid, k, "benefit", review="unreviewed")
        elif v is not None:
            s.cells[k] = cell(pid, k, v, excerpt=f"n = {v}" if k == "n" else str(v))
    return s


S = [
    study("a", "RCT", 120, "benefit"),
    study("b", "RCT", 80, "benefit"),
    study("c", "Cohort", "NL", "no effect", appraisal=HIGH),
    study("d", "Cohort", "NA", "UNREVIEWED", appraisal=None),
]


def test_grouping_by_field_and_question():
    groups = sig.group_studies(S, FIELDS, "design", {})
    assert [(g["label"], g["paper_ids"]) for g in groups] == [("Cohort", ["c", "d"]), ("RCT", ["a", "b"])]
    assert "design" not in groups[0]["field_keys"]
    q = sig.group_studies(S, FIELDS, "question:q1", {"effect": ["q1"]})
    assert q[0]["field_keys"] == ["effect"] and q[0]["paper_ids"] == ["a", "b", "c", "d"]
    assert sig.group_studies(S, FIELDS, None, {})[0]["key"] == "all"


def test_field_signals_separate_patterns_and_gap_reasons():
    eff = sig.field_signal(FIELDS[2], S)
    assert eff["pattern"] == "disagreement" and eff["studies_with_value"] == 3
    assert eff["gaps"]["not_reviewed"] == ["d"]  # unreviewed AI values never count as evidence
    n = sig.field_signal(FIELDS[1], S)
    assert n["range"] == {"min": 80.0, "max": 120.0, "n": 2}
    assert n["gaps"]["not_located"] == ["c"] and n["gaps"]["unavailable"] == ["d"]
    assert sig.field_signal(FIELDS[2], S[:2])["pattern"] == "agreement"
    assert sig.field_signal(FIELDS[2], S[:1])["pattern"] == "single_study"
    notes = sig.field_signal(
        FIELDS[3], [study("x", "RCT", 1, "benefit", notes="a"), study("y", "RCT", 1, "benefit", notes="b")]
    )
    assert notes["pattern"] == "varied"  # free text is "reported differently", not a conflict


def test_certainty_rules():
    a, b, c = S[0], S[1], S[2]
    two = [(a, a.cells["effect"]), (b, b.cells["effect"])]
    assert sig.compute_certainty("Both trials found a benefit.", two, "finding")[0] == "supported"
    one = sig.compute_certainty("A benefit was found.", two[:1], "finding")
    assert one[0] == "uncertain" and "single study" in one[1][0].lower()
    conflict = sig.compute_certainty("Effects differ.", two + [(c, c.cells["effect"])], "disagreement")
    assert conflict[0] == "conflicting"
    weak = sig.compute_certainty("x", [(a, a.cells["effect"]), (c, c.cells["design"])], "finding")
    assert weak[0] == "uncertain"  # a cited study has serious appraisal concerns
    nums = sig.compute_certainty("Samples were 120 and 999.", [(a, a.cells["n"]), (b, b.cells["n"])], "finding")
    assert nums[0] == "uncertain" and "999" in nums[1][-1]
    assert sig.compute_certainty("x", [], "finding")[0] == "insufficient"
    gap = sig.compute_certainty("Not located in one study.", [(c, c.cells["n"])], "gap")
    assert gap[0] == "supported"


def test_claimed_certainty_can_only_lower():
    assert sig.cap("supported", "uncertain") == "uncertain"
    assert sig.cap("uncertain", "supported") == "uncertain"
    assert sig.cap("supported", "conflicting") == "conflicting"
    assert sig.cap(None, "supported") == "supported"


def test_system_statements_cite_what_they_describe():
    groups = sig.group_studies(S, FIELDS, None, {})
    st = sig.system_statements(FIELDS, groups, S, "design")
    kinds = {s["kind"] for s in st}
    assert {"disagreement", "gap", "limitation", "method_difference"} <= kinds
    gap = next(s for s in st if s["kind"] == "gap" and "Sample size" in s["text"])
    assert "not evidence of absence" in gap["text"] and set(gap["item_ids"]) == {"c-n", "d-n"}
    assert all(s["item_ids"] for s in st)
    assert all("d-effect" not in s["item_ids"] for s in st)  # unreviewed value never cited


def test_ai_draft_validation_requires_citations_and_rejects_pooling():
    keys, lines = ai.evidence_keys(S[:2], FIELDS)
    assert any("E1" in ln for ln in lines) and keys[0].item_id == "a-design"
    data = {
        "statements": [
            {
                "kind": "finding",
                "text": "Both trials report benefit.",
                "citations": ["E3", "E7"],
                "certainty": "supported",
            },
            {"kind": "conclusion", "text": "The pooled effect favours exercise.", "citations": ["E3"]},
            {"kind": "finding", "text": "Uncited claim.", "citations": ["E99"]},
            {"kind": "opinion", "text": "x", "citations": ["E1"]},
        ]
    }
    out, problems = ai.validate(data, keys)
    assert len(out) == 1 and out[0]["item_ids"] == [keys[2].item_id, keys[6].item_id]
    assert out[0]["origin"] == "ai" and out[0]["claimed_certainty"] == "supported"
    assert any("pooling" in p for p in problems) and any("valid citation" in p for p in problems)
    assert sig.META_ANALYSIS["in_scope"] is False
