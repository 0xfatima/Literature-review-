# stdlib-only
"""Deterministic tests for app.extraction.extractor: chunking, quote location, value coercion, numeric checks,
validation of model answers, merging across chunks and schema diffs."""

from app.extraction import extractor as ex

P1 = ex.PageText("id-1", 1, "Methods. We enrolled 1,234 adults\nin a randomised controlled trial.")
P2 = ex.PageText("id-2", 2, "Results. Mean HbA1c fell by 0.50% (95% CI 0.3 to 0.7) at 12 weeks.")
P3 = ex.PageText("id-3", 3, "")
FIELDS = [
    {"field_key": "n", "label": "Sample size", "type": "number"},
    {"field_key": "design", "label": "Design", "type": "choice", "options": ["RCT", "Cohort"]},
    {"field_key": "result", "label": "Result", "type": "text"},
    {"field_key": "funding", "label": "Funding", "type": "text"},
]


def test_chunks_keep_whole_pages_and_skip_empty_pages():
    chunks = ex.chunk_pages([P1, P2, P3], max_chars=80)
    assert [[p.page_no for p in c] for c in chunks] == [[1], [2]]
    assert [[p.page_no for p in c] for c in ex.chunk_pages([P1, P2], 10_000)] == [[1, 2]]
    big = ex.PageText("b", 9, "x" * 500)
    assert [[p.page_no for p in c] for c in ex.chunk_pages([big, P1], 100)] == [[9], [1]]


def test_prompt_lists_fields_options_and_page_markers():
    prompt = ex.build_prompt(FIELDS, "A trial", [P1, P2], 1, 2)
    assert "=== PAGE 1 ===" in prompt and "=== PAGE 2 ===" in prompt
    assert "options: RCT; Cohort" in prompt and "Text part 1 of 2" in prompt


def test_locate_quote_is_whitespace_and_case_insensitive_but_returns_exact_source():
    assert ex.locate_quote(P1.text, "enrolled 1,234 ADULTS in a") == "enrolled 1,234 adults\nin a"
    assert ex.locate_quote(P1.text, "enrolled 999 adults") is None
    assert ex.locate_quote(P1.text, "12") is None  # too short to be a verifiable quote
    assert ex.locate_quote("the \ufb01rst \u201cdose\u201d", 'first "dose"') == "\ufb01rst \u201cdose\u201d"


def test_numbers_are_normalised():
    assert ex.numbers_in("n = 1,234; 2.50 mg; \u22123") == {"1234", "2.5", "-3"}
    assert "19" in ex.numbers_in("COVID-19")


def test_numeric_warnings_flag_numbers_and_units_missing_from_excerpt():
    assert ex.numeric_warnings("0.5%", P2.text) == []
    codes = [w["code"] for w in ex.numeric_warnings("0.8%", "fell by 0.50 points")]
    assert codes == ["number_not_in_excerpt", "unit_not_in_excerpt"]
    assert [w["code"] for w in ex.numeric_warnings("120", None)] == ["no_source_excerpt"]
    assert ex.numeric_warnings({"text": "120"}, "120 adults") == []


def test_coerce_value_never_fixes_values_that_do_not_fit():
    num, cho, multi, boo = (
        {"type": "number"},
        {"type": "choice", "options": ["RCT", "Cohort"]},
        {"type": "multi_choice", "options": ["A", "B"]},
        {"type": "boolean"},
    )
    assert ex.coerce_value(num, "1,234") == (1234, None)
    assert ex.coerce_value(num, "2.5") == (2.5, None)
    assert ex.coerce_value(num, "between 10 and 20")[1] is not None
    assert ex.coerce_value(cho, "rct") == ("RCT", None)
    assert ex.coerce_value(cho, "case series")[1] is not None
    assert ex.coerce_value(multi, "a; B") == (["A", "B"], None)
    assert ex.coerce_value(multi, "A; C")[1] is not None
    assert ex.coerce_value(boo, "yes") == (True, None)
    assert ex.coerce_value(boo, "maybe")[1] is not None
    assert ex.coerce_value({"type": "text"}, "  ")[1] == "empty value"


def _answer(**kw):
    return {"fields": [kw]}


def test_found_answer_is_kept_only_with_a_verified_quote():
    data = {
        "fields": [
            {"field_key": "n", "status": "found", "value": "1234", "page": 1, "quote": "enrolled 1,234 adults"},
            {"field_key": "design", "status": "found", "value": "RCT", "page": 2, "quote": "randomised controlled"},
            {"field_key": "result", "status": "found", "value": "fell", "page": 2, "quote": "invented sentence here"},
            {"field_key": "funding", "status": "not_located"},
            {"field_key": "bogus", "status": "found", "value": "x", "quote": "Methods"},
        ]
    }
    out, problems = ex.validate_answer(FIELDS, [P1, P2], data)
    assert out["n"].status == "found" and out["n"].value == 1234 and out["n"].page_no == 1
    assert out["n"].excerpt == "enrolled 1,234 adults"
    # quote is on page 1, model cited 2: page corrected, warning kept for review
    assert out["design"].page_no == 1 and out["design"].warnings[0]["code"] == "page_corrected"
    assert out["result"].status == "rejected" and out["result"].warnings[0]["code"] == "quote_not_in_source"
    assert out["funding"].status == "not_located"
    assert any("bogus" in p for p in problems)


def test_numeric_field_whose_number_is_not_in_quote_is_discarded():
    data = _answer(field_key="n", status="found", value="999", page=1, quote="enrolled 1,234 adults")
    out, _ = ex.validate_answer(FIELDS, [P1], data)
    assert out["n"].status == "rejected" and out["n"].warnings[0]["code"] == "number_not_in_excerpt"


def test_text_value_with_unquoted_number_is_kept_with_warning():
    data = _answer(field_key="result", status="found", value="HbA1c fell 0.9%", page=2, quote="Mean HbA1c fell by")
    out, _ = ex.validate_answer(FIELDS, [P2], data)
    assert out["result"].status == "found"
    assert "number_not_in_excerpt" in [w["code"] for w in out["result"].warnings]


def test_ambiguous_keeps_note_and_verified_quote_only():
    data = _answer(field_key="result", status="ambiguous", note="Two values", page=2, quote="not in the text")
    out, _ = ex.validate_answer(FIELDS, [P2], data)
    assert out["result"].status == "ambiguous" and out["result"].excerpt is None and out["result"].note == "Two values"


def test_malformed_response_produces_no_outcomes():
    assert ex.validate_answer(FIELDS, [P1], {"nope": 1})[0] == {}


def test_merge_requires_not_located_from_every_chunk_and_flags_conflicts():
    found_a = ex.Outcome("found", value="RCT", excerpt="x", page_id="id-1", page_no=1)
    found_b = ex.Outcome("found", value="Cohort", excerpt="y", page_id="id-2", page_no=2, chunk_index=1)
    nl = ex.Outcome("not_located")
    merged = ex.merge([{"design": found_a, "funding": nl}, {"design": found_b}], ["design", "funding", "n"], 2)
    assert merged["design"].status == "ambiguous" and "Cohort" in (merged["design"].note or "")
    assert "funding" not in merged  # second chunk did not answer: not proven absent from processed text
    assert "n" not in merged
    merged2 = ex.merge([{"funding": nl}, {"funding": nl}], ["funding"], 2)
    assert merged2["funding"].status == "not_located"
    same = ex.merge([{"design": found_a}, {"design": ex.Outcome("found", value="rct", page_no=3)}], ["design"], 2)
    assert same["design"].status == "found" and same["design"].page_no == 1


def test_merge_prefers_found_over_rejected_and_keeps_rejection_otherwise():
    rej = ex.Outcome("rejected", warnings=[{"code": "quote_not_in_source", "message": "m"}])
    found = ex.Outcome("found", value=5, page_no=2, chunk_index=1)
    assert ex.merge([{"n": rej}, {"n": found}], ["n"], 2)["n"].status == "found"
    assert ex.merge([{"n": rej}], ["n"], 1)["n"].status == "rejected"


def test_schema_diff():
    old = [{"field_key": "a", "label": "A", "type": "text"}, {"field_key": "b", "label": "B", "type": "text"}]
    new = [{"field_key": "a", "label": "A", "type": "number"}, {"field_key": "c", "label": "C", "type": "text"}]
    assert ex.schema_diff(old, new) == {"added": ["c"], "removed": ["b"], "changed": ["a"]}
