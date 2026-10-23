"""Provider-neutral search expression: parsing, validation and per-provider rendering."""

import pytest

from app.providers.scholarly.arxiv import ArxivSource
from app.providers.scholarly.crossref import CrossrefSource
from app.providers.scholarly.europepmc import EuropePMCSource
from app.providers.scholarly.openalex import OpenAlexSource
from app.search import query as q
from app.search.derive import FilterPlan

EXPR = '("aerobic exercise" OR running) AND depress* NOT children'
NO_FILTERS = FilterPlan(None, None, [], [], [])
FILTERS = FilterPlan(2010, 2024, [("en", "eng"), ("fr", "fre")], [], ["RCT"])


def test_parse_builds_the_expected_tree_and_round_trips():
    node = q.parse(EXPR)
    assert isinstance(node, q.And)
    assert q.to_text(q.parse(q.to_text(node))) == q.to_text(node)
    terms = q.positive_terms(node)
    assert [t.text for t in terms] == ["aerobic exercise", "running", "depress"]
    assert terms[0].phrase and terms[2].truncated


def test_adjacent_terms_are_anded_and_nested_groups_flatten():
    assert q.to_text(q.parse("a b c")) == "a AND b AND c"
    assert q.to_text(q.parse("a AND (b AND c)")) == "a AND b AND c"
    assert q.to_text(q.parse("(a OR (b OR c)) AND d")) == "(a OR b OR c) AND d"


def test_lower_case_operators_are_ordinary_words():
    assert q.to_text(q.parse("cats or dogs")) == "cats AND or AND dogs"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("a AND", "ends where a search term was expected"),
        ("(a OR b", "parenthesis is not closed"),
        ("a)", "unmatched closing parenthesis"),
        ('"open phrase', "quotation mark is not closed"),
        ('""', "empty quoted phrase"),
        ("TITLE:depression", "Field tags"),
        ("NOT children", "only excludes terms"),
        ("x" * 2001, "too long"),
    ],
)
def test_invalid_expressions_are_rejected_with_a_readable_message(text, message):
    with pytest.raises(q.QueryError, match=message):
        q.parse(text)


def test_term_and_depth_limits():
    with pytest.raises(q.QueryError, match="Too many search terms"):
        q.parse(" ".join(f"t{i}" for i in range(q.MAX_TERMS + 1)))
    with pytest.raises(q.QueryError, match="nested too deeply"):
        q.parse("(" * (q.MAX_DEPTH + 2) + "a" + ")" * (q.MAX_DEPTH + 2))


def test_openalex_rendering_keeps_boolean_drops_truncation_and_applies_filters():
    pq = OpenAlexSource().build_query(q.parse(EXPR), FILTERS)
    assert pq.query_text == '("aerobic exercise" OR running) AND depress NOT children'
    assert pq.params["filter"] == "from_publication_date:2010-01-01,to_publication_date:2024-12-31,language:en|fr"
    assert any("truncation" in note for note in pq.limitations)
    assert pq.filters_applied == {"date_from": 2010, "date_to": 2024, "languages": ["en", "fr"]}


def test_europepmc_rendering_puts_filters_in_the_query_and_keeps_truncation():
    pq = EuropePMCSource().build_query(q.parse(EXPR), FILTERS)
    assert pq.query_text == (
        '(("aerobic exercise" OR running) AND depress* NOT children) AND (PUB_YEAR:[2010 TO 2024]) '
        'AND (LANG:"eng" OR LANG:"fre")'
    )
    assert pq.params["query"] == pq.query_text and pq.params["resultType"] == "core"


def test_arxiv_rendering_prefixes_terms_uses_andnot_and_groups_before_the_date():
    pq = ArxivSource().build_query(q.parse(EXPR), FilterPlan(2015, None, [], [], []))
    assert pq.query_text == (
        '((all:"aerobic exercise" OR all:running) AND all:depress ANDNOT all:children) '
        "AND submittedDate:[201501010000 TO 210012312359]"
    )
    assert any("preprints" in note for note in pq.limitations)


def test_crossref_rendering_flattens_to_keywords_and_says_so():
    pq = CrossrefSource().build_query(q.parse(EXPR), FILTERS)
    assert pq.query_text == '"aerobic exercise" running depress'
    joined = " ".join(pq.limitations)
    assert "does not support Boolean" in joined and "NOT" in joined and "no language filter" in joined


def test_an_or_branch_that_only_excludes_is_rejected_with_its_own_message():
    with pytest.raises(q.QueryError, match="OR alternative only excludes"):
        q.parse("a OR NOT d")


def test_renderer_reports_a_lonely_not_instead_of_keeping_it():
    tree = q.Or((q.Term("a"), q.Not(q.Term("d"))))  # constructed directly; parse() refuses this shape
    r = q.render(tree, q.Dialect(name="X"))
    assert r.text == "a" and any("exclusion outside an AND" in n for n in r.limitations)
