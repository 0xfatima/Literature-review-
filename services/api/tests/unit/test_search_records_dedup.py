"""Identifier/title normalisation and the conservative duplicate rules."""

from dataclasses import dataclass

import pytest

from app.search import dedup
from app.search.records import (
    Author,
    ScholarlyRecord,
    access_status,
    author_key,
    clean_text,
    finish,
    norm_arxiv,
    norm_doi,
    norm_openalex,
    norm_pmcid,
    norm_pmid,
    title_key,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1016/J.JAD.2020.01.001", "10.1016/j.jad.2020.01.001"),
        ("doi:10.1000/xyz.", "10.1000/xyz"),
        ("10.1000/abc)", "10.1000/abc"),
        ("not a doi", None),
        (None, None),
        ("10.1/x", None),  # registrant codes have at least 4 digits
    ],
)
def test_norm_doi(raw, expected):
    assert norm_doi(raw) == expected


def test_other_identifiers():
    assert norm_pmid("https://pubmed.ncbi.nlm.nih.gov/0031111111/") == "31111111"
    assert norm_pmcid("https://www.ncbi.nlm.nih.gov/pmc/articles/5815332") == "PMC5815332"
    assert norm_pmcid("pmc123") == "PMC123"
    assert norm_openalex("https://openalex.org/w2741809807") == "W2741809807"
    assert norm_arxiv("http://arxiv.org/abs/2101.00001v3") == "2101.00001"
    assert norm_arxiv("10.48550/arXiv.2101.00001") == "2101.00001"
    assert norm_arxiv("hep-th/9901001v1") == "hep-th/9901001"
    assert norm_arxiv("https://arxiv.org/pdf/2101.00001v2.pdf") == "2101.00001"
    assert norm_arxiv("random") is None


def test_text_normalisation():
    assert clean_text("<jats:p>Hello&nbsp;<b>world</b></jats:p>", 100) == "Hello world"
    assert clean_text("abcdef", 3) == "abc"
    assert title_key("Café: Ölçek — a STUDY!") == "cafe olcek a study"
    assert author_key(Author("Smith J")) == "smith"
    assert author_key(Author("Smith, John")) == "smith"
    assert author_key(Author("John Smith")) == "smith"
    assert author_key(Author("X", family="O'Neil")) == "oneil"


def test_access_status_distinguishes_metadata_from_full_text_links():
    assert access_status("open", "https://x/p.pdf", None) == "open_pdf_link"
    assert access_status("open", None, "https://x") == "open_landing_page"
    assert access_status("closed", "https://x/p.pdf", None) == "metadata_only"
    assert access_status("unknown", None, None) == "metadata_only"


def test_finish_derives_arxiv_id_and_drops_non_http_links():
    r = finish(ScholarlyRecord("p", "1", "T", doi="10.48550/arxiv.2101.00001", pdf_url="javascript:alert(1)"))
    assert r.arxiv_id == "2101.00001" and r.is_preprint and r.pdf_url is None


@dataclass
class P:
    id: int = 1
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    title_key: str = ""
    first_author_key: str | None = None
    year: int | None = None


LONG = "aerobic exercise for depression in older adults a randomised trial"


def rec(**kw):
    return P(id=0, **kw)


@pytest.mark.parametrize("field", ["doi", "pmid", "arxiv_id", "openalex_id"])
def test_identifier_match_merges(field):
    d = dedup.decide(rec(**{field: "X1"}), [P(**{field: "X1"})])
    assert d.match is not None and d.basis == field


def test_title_author_year_match_merges_when_nothing_conflicts():
    d = dedup.decide(
        rec(title_key=LONG, first_author_key="smith", year=2020, doi="10.1/a"),
        [P(title_key=LONG, first_author_key="smith", year=2021)],
    )
    assert d.basis == "title_author_year"


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (P(title_key=LONG, first_author_key="smith", year=2020, doi="10.2/b"), "different doi"),
        (P(title_key=LONG, first_author_key="jones", year=2020), "different first author"),
        (P(title_key=LONG, first_author_key="smith", year=2015), "years differ"),
        (P(title_key=LONG, year=2019), "first author is missing"),
    ],
)
def test_same_title_with_a_disagreement_is_flagged_not_merged(candidate, reason):
    d = dedup.decide(rec(title_key=LONG, first_author_key="smith", year=2020, doi="10.1/a"), [candidate])
    assert d.match is None and d.basis == "new"
    assert d.possible_duplicate_of is candidate and reason in d.reason.lower()


def test_short_generic_titles_never_merge():
    d = dedup.decide(
        rec(title_key="editorial", first_author_key="a", year=2020),
        [P(title_key="editorial", first_author_key="a", year=2020)],
    )
    assert d.match is None and "too generic" in d.reason


def test_unrelated_records_are_new_without_a_flag():
    d = dedup.decide(rec(title_key=LONG, doi="10.1/a"), [P(title_key="something else entirely", doi="10.9/z")])
    assert d.match is None and d.possible_duplicate_of is None
