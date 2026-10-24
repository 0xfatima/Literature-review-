"""Matching an uploaded PDF to paper records: identifiers, titles, ambiguity and the 'cited DOI' trap."""

from app.documents.matching import (
    DocumentFacts,
    PaperFacts,
    consistency_warning,
    extract_arxiv_ids,
    extract_dois,
    match,
    title_evidence,
)

T1 = "Aerobic exercise for depression in older adults: a randomised controlled trial"
T2 = "Walking programmes and depressive symptoms in later life"
P1 = PaperFacts("p1", T1, "10.1016/j.jad.2020.01.001", None, 2020, "smith")
P2 = PaperFacts("p2", T2, "10.1001/jama.2019.12345", None, 2019, "chen")
P3 = PaperFacts("p3", "Machine learning prediction of exercise response in depression", None, "2101.00001", 2021, "lee")
PAPERS = [P1, P2, P3]


def doc(text: str, **kw) -> DocumentFacts:
    return DocumentFacts(first_pages_text=text, **kw)


def test_doi_extraction_handles_urls_case_punctuation_and_brackets():
    assert extract_dois("see https://doi.org/10.1234/ABC-1(2).") == ["10.1234/abc-1(2)"]
    assert extract_dois("(doi:10.1016/S0140-6736(20)30183-5)") == ["10.1016/s0140-6736(20)30183-5"]
    assert extract_dois("DOI 10.1000/xyz, and 10.1000/xyz. again 10.2000/b;") == ["10.1000/xyz", "10.2000/b"]
    assert extract_dois("no identifiers here, version 10.5 of 3/4") == []


def test_arxiv_extraction():
    assert extract_arxiv_ids("arXiv:2101.00001v2 [cs.LG] and arXiv:hep-th/9901001") == ["2101.00001", "hep-th/9901001"]


def test_title_evidence_levels():
    assert title_evidence(T1, f"{T1}\nAnn Smith", "") >= 0.95  # whole title on the page
    assert title_evidence(T1, "", T1) >= 0.95  # PDF metadata title
    assert 0.5 < title_evidence(T1, "Aerobic exercise for depression among older people: a randomised trial", "") < 0.9
    assert title_evidence(T1, "Quantum error correction in superconducting circuits", "") < 0.3
    assert title_evidence("Short title", "Short title", "") == 0.0  # fewer than 3 words: never evidence


def test_doi_with_matching_title_is_an_automatic_match():
    r = match(doc(f"{T1}\nAnn Smith 2020\nhttps://doi.org/10.1016/J.JAD.2020.01.001"), PAPERS)
    assert (r.status, r.paper_id, r.method) == ("auto_matched", "p1", "doi") and r.score == 1.0


def test_doi_in_the_pdf_metadata_alone_is_reliable():
    r = match(doc("Some unrelated front matter text", metadata_doi="doi:10.1001/jama.2019.12345"), PAPERS)
    assert (r.status, r.paper_id) == ("auto_matched", "p2")


def test_a_doi_that_is_only_cited_is_not_enough():
    text = "Yoga for chronic back pain in adults\nZed Adams 2018\nSee also 10.1001/jama.2019.12345 for context."
    r = match(doc(text), PAPERS)
    assert r.status != "auto_matched" and r.paper_id is None


def test_first_doi_on_the_page_with_the_first_author_is_reliable_even_if_title_differs():
    r = match(doc("Smith A, et al. 2020. A very different headline\n10.1016/j.jad.2020.01.001\nmore text"), PAPERS)
    assert (r.status, r.paper_id) == ("auto_matched", "p1")


def test_two_papers_identified_by_identifier_are_never_matched_automatically():
    text = f"{T1}\n{T2}\n10.1016/j.jad.2020.01.001 10.1001/jama.2019.12345 Smith Chen"
    r = match(doc(text), PAPERS)
    assert r.status == "needs_confirmation" and r.paper_id is None
    assert {c["paper_id"] for c in r.candidates} == {"p1", "p2"}


def test_arxiv_identifier_match():
    r = match(
        doc("Machine learning prediction of exercise response in depression\nLee 2021\narXiv:2101.00001v1 [cs.LG]"),
        PAPERS,
    )
    assert (r.status, r.paper_id, r.method) == ("auto_matched", "p3", "arxiv")


def test_title_and_author_without_identifiers_match_only_with_a_clear_margin():
    r = match(doc(f"{T2}\nBo Chen\n2019\nAbstract ..."), PAPERS)
    assert (r.status, r.paper_id, r.method) == ("auto_matched", "p2", "title_author")


def test_a_reworded_title_needs_confirmation_and_is_not_linked():
    r = match(doc("Aerobic exercise for depression among older people: a randomised trial\nA. Smith 2020"), PAPERS)
    assert r.status == "needs_confirmation" and r.paper_id is None and r.candidates[0]["paper_id"] == "p1"
    assert r.candidates[0]["score"] < 0.9 and r.candidates[0]["reasons"]


def test_two_near_identical_papers_are_a_tie_and_need_confirmation():
    a = PaperFacts("a", "Exercise and mood in older adults a cohort study", None, None, 2020, "smith")
    b = PaperFacts("b", "Exercise and mood in older adults a cohort study protocol", None, None, 2020, "smith")
    r = match(doc("Exercise and mood in older adults a cohort study protocol\nSmith 2020"), [a, b])
    assert r.status != "auto_matched"


def test_unrelated_text_is_unmatched():
    r = match(doc("Quantum error correction in superconducting circuits. 10.9999/q.1"), PAPERS)
    assert (r.status, r.paper_id, r.candidates) == ("unmatched", None, [])
    assert match(doc(""), PAPERS).status == "unmatched" and match(doc(T1), []).status == "unmatched"


def test_consistency_warning_for_a_user_linked_pdf():
    text = f"{T1}\nAnn Smith 2020\n10.1016/j.jad.2020.01.001"
    assert consistency_warning(doc(text), P2, PAPERS)  # attached to the wrong paper: say so
    assert consistency_warning(doc(text), P1, PAPERS) is None  # attached to the right paper
    assert consistency_warning(doc("Unrelated physics text"), P1, PAPERS) is None  # no evidence either way
