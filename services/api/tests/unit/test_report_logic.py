# stdlib-only
"""Deterministic tests for report validation (citations, counts, evidence, completeness, readiness label) and the
Markdown / DOCX / CSV renderers."""

import io
import zipfile
from xml.dom import minidom

from app.reports import render
from app.reports import validate as V

REFS = [{"key": "smith2020", "paper_id": "p1", "formatted": "Smith J (2020). A trial."}]
FACTS = {"identified": 250, "screened": 200, "included_studies": 3, "removed_duplicates": 50, "violations": []}
EVIDENCE = {"i1": {"revision": 1, "reviewed": True, "numbers": ["120"]}}
PAPERS = {"p1": {"exists": True, "retracted": False}}


def sec(key, blocks, required=True, verified=False):
    return {
        "key": key,
        "title": key.title(),
        "required": required,
        "blocks": blocks,
        "verified_by": "u" if verified else None,
    }


def para(text, bid="b", origin="edited", source="author", **kw):
    return {"id": bid, "type": "paragraph", "text": text, "origin": origin, "source": source, **kw}


def codes(sections, refs=REFS, facts=FACTS, evidence=EVIDENCE, papers=PAPERS):
    return [i["code"] for i in V.validate(sections, refs, facts, evidence, papers)["issues"]]


def test_counts_in_text_must_match_the_recorded_flow():
    assert V.check_numbers("250 records were identified and 3 studies were included.", FACTS) == []
    bad = V.check_numbers("We screened 1,200 records; 4 studies were included [@smith2020].", FACTS)
    assert [(b["fact"], b["stated"]) for b in bad] == [("included_studies", 4)]
    assert V.check_numbers("200 records were screened", FACTS) == []
    assert V.check_numbers("1,200 records were screened", FACTS)[0]["stated"] == 1200


def test_citations_must_resolve_to_stored_records():
    assert "unknown_citation" in codes([sec("results", [para("See [@nobody1999].")])])
    gone = {"p1": {"exists": False}}
    assert "reference_not_stored" in codes([sec("results", [para("x [@smith2020]")])], papers=gone)
    assert "retracted_reference" in codes(
        [sec("results", [para("x [@smith2020]")])], papers={"p1": {"exists": True, "retracted": True}}
    )


def test_evidence_linked_statements_are_checked_against_their_evidence():
    ev = [{"item_id": "i1", "revision": 1, "paper_id": "p1", "field_key": "n"}]
    ok = {
        "id": "s",
        "type": "statement",
        "text": "The trial enrolled 120 adults [@smith2020].",
        "evidence": ev,
        "origin": "generated",
        "certainty": "supported",
    }
    assert codes([sec("synthesis", [ok])]) == []
    no_cite = {**ok, "text": "The trial enrolled 120 adults."}
    assert "missing_citation" in codes([sec("synthesis", [no_cite])])
    wrong_n = {**ok, "text": "The trial enrolled 140 adults [@smith2020]."}
    assert "unverified_number" in codes([sec("synthesis", [wrong_n])])
    stale = codes([sec("synthesis", [ok])], evidence={"i1": {"revision": 2, "reviewed": True, "numbers": ["120"]}})
    assert "stale_evidence" in stale
    refs2 = [*REFS, {"key": "lee2021", "paper_id": "p2", "formatted": "Lee"}]
    extra = {**ok, "text": "The trial enrolled 120 adults [@smith2020] [@lee2021]."}
    assert "citation_not_supporting" in codes(
        [sec("synthesis", [extra])], refs=refs2, papers={**PAPERS, "p2": {"exists": True}}
    )


def test_unsupported_claims_placeholders_incomplete_sections_and_uncertain_conclusions():
    c = codes(
        [
            sec("discussion", [para("Exercise clearly works.")]),
            sec("introduction", [para("[[To be written by the authors: background]]", origin="generated")]),
            sec("methods", []),
            sec(
                "conclusion",
                [
                    {
                        "id": "c",
                        "type": "statement",
                        "text": "Maybe [@smith2020].",
                        "certainty": "uncertain",
                        "evidence": [{"item_id": "i1", "revision": 1, "paper_id": "p1"}],
                    }
                ],
            ),
        ]
    )
    assert {"unsupported_statement", "placeholder", "incomplete_section", "uncertain_conclusion"} <= set(c)
    generated_data = sec("results", [para("250 records were identified.", origin="generated", source="data:prisma")])
    assert codes([generated_data]) == []


def test_readiness_label_never_claims_publication_readiness():
    res = V.validate(
        [sec("results", [para("250 records were identified.", source="data:prisma")], verified=True)],
        REFS,
        FACTS,
        EVIDENCE,
        PAPERS,
    )
    assert res["summary"]["publication_ready"] is False
    assert "peer review" in res["summary"]["label"]
    unverified = V.validate([sec("results", [para("x [@smith2020]")])], REFS, FACTS, EVIDENCE, PAPERS)
    assert unverified["summary"]["label"].startswith("Unverified draft")
    viol = V.validate([], REFS, {**FACTS, "violations": [{"message": "screened != sum"}]}, EVIDENCE, PAPERS)
    assert viol["summary"]["errors"] == 1 and "not verified" in viol["summary"]["label"]


def _version():
    table = {"key": "t", "caption": "Counts & <stuff>", "columns": ["Stage", "N"], "rows": [["=SUM(A1)", 3]]}
    sections = [
        sec(
            "results",
            [
                para("3 studies were included [@smith2020].", source="data:prisma"),
                {"id": "t", "type": "table", "table": table},
            ],
        ),
        sec(
            "conclusion",
            [{"id": "s", "type": "statement", "text": "Benefit [@smith2020].", "certainty": "conflicting"}],
        ),
        sec("references", [{"id": "r", "type": "references"}]),
    ]
    v = {"title": "Review", "version": 2, "sections": sections, "references": REFS}
    v["validation"] = V.validate(sections, REFS, FACTS, EVIDENCE, PAPERS)
    return v


def test_markdown_shows_notice_citations_certainty_tables_and_references():
    md = render.render_markdown(_version())
    assert "not publication-ready" in md and "[smith2020]" in md and "[@" not in md
    assert "(Certainty: conflicting evidence.)" in md
    assert "| Stage | N |" in md and "- [smith2020] Smith J (2020). A trial." in md


def test_docx_is_valid_deterministic_wordprocessingml():
    v = _version()
    blob = render.render_docx(v)
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert set(z.namelist()) >= {"[Content_Types].xml", "word/document.xml", "word/styles.xml"}
    for n in z.namelist():
        minidom.parseString(z.read(n))  # noqa: S318  XML produced by our own renderer
    doc = z.read("word/document.xml").decode()
    assert "not publication-ready" in doc and "Counts &amp; &lt;stuff&gt;" in doc and "<w:tbl>" in doc
    assert render.render_docx(v) == blob


def test_csv_neutralises_formulas():
    out = render.table_csv(_version()["sections"][0]["blocks"][1]["table"])
    assert out.splitlines()[1].startswith("'=SUM(A1)")
