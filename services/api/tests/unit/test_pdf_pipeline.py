"""Text extraction on REAL PDFs (generated with PyMuPDF) through the real child process: page numbers, blank pages,
image-only pages with OCR off / on, damaged files, encryption, limits and crash handling."""

import json
import os
import shutil

import pytest

from app.core.config import Settings
from app.documents import pipeline, runner
from app.documents.ocr import NullOcr, TesseractOcr, parse_tsv
from tests.fakes import FakeOcr
from tests.fixtures import pdfs

PARA = pdfs.PARA.format(n=9)


def run(tmp_path, data, ocr=None, **settings):
    p = tmp_path / "t.pdf"
    p.write_bytes(data)
    return pipeline.process(p, Settings(**settings), ocr or NullOcr())


def fails(tmp_path, data, ocr=None, **settings) -> pipeline.ProcessingFailure:
    with pytest.raises(pipeline.ProcessingFailure) as e:
        run(tmp_path, data, ocr, **settings)
    return e.value


def test_page_numbers_text_and_metadata(tmp_path):
    ex = run(
        tmp_path, pdfs.make_pdf([f"First page. {PARA}", f"Second page. {PARA}"], metadata={"title": "T", "author": "A"})
    )
    assert ex.page_count == 2 and [p.page_no for p in ex.pages] == [1, 2]
    assert ex.pages[0].text.startswith("First page.") and ex.pages[1].text.startswith("Second page.")
    assert ex.metadata["title"] == "T" and ex.warnings == []


def test_blank_pages_are_blank_when_the_file_is_intact(tmp_path):
    ex = run(tmp_path, pdfs.make_pdf([PARA, None, PARA]))
    assert [p.extraction for p in ex.pages] == ["text", "blank", "text"]


def test_image_only_page_is_unreadable_without_ocr_and_never_gets_invented_text(tmp_path):
    ex = run(tmp_path, pdfs.scanned_page_pdf("Scanned words", before=[PARA]))
    assert [p.extraction for p in ex.pages] == ["text", "unreadable"] and ex.pages[1].text == ""
    w = next(w for w in ex.warnings if w["code"] == "image_only_pages")
    assert w["pages"] == [2] and "OCR is turned off" in w["message"]


def test_only_scanned_pdf_fails_with_instructions(tmp_path):
    f = fails(tmp_path, pdfs.scanned_page_pdf("Scanned words"))
    assert f.code == "scanned_no_ocr" and f.retryable and "OCR" in f.message


def test_ocr_enabled_but_engine_missing_says_so(tmp_path):
    f = fails(tmp_path, pdfs.scanned_page_pdf("Scanned words"), FakeOcr(available=False), feature_ocr=True)
    assert f.code == "ocr_unavailable" and "tesseract" in f.message.lower()


def test_ocr_reads_only_the_pages_that_need_it_and_records_confidence(tmp_path):
    ocr = FakeOcr({3: "Recognised text from a scanned third page of the paper."}, confidence=42.0)
    ex = run(tmp_path, pdfs.scanned_page_pdf("Scan", before=[PARA, None]), ocr, feature_ocr=True)
    assert ocr.calls == [3] and [p.extraction for p in ex.pages] == ["text", "blank", "ocr"]
    assert ex.pages[2].ocr_confidence == 42.0
    codes = {w["code"]: w for w in ex.warnings}
    assert codes["ocr_used"]["pages"] == [3] and codes["ocr_low_confidence"]["pages"] == [3]


def test_ocr_that_finds_nothing_marks_the_page_blank_with_a_reason(tmp_path):
    ex = run(tmp_path, pdfs.scanned_page_pdf("Scan", before=[PARA]), FakeOcr({}), feature_ocr=True)
    assert ex.pages[1].extraction == "blank" and "no_text_found_by_ocr" in ex.pages[1].warnings


def test_ocr_page_limit_is_reported_not_silent(tmp_path):
    ocr = FakeOcr({3: "Recognised text from a scanned page of the paper, long enough."})
    data = pdfs.scanned_page_pdf("Scan", before=[PARA, PARA])
    ex = run(tmp_path, data, ocr, feature_ocr=True, ocr_max_pages=1)
    assert ex.pages[2].extraction == "ocr"  # one scanned page, within the limit
    # two image pages, limit of one: the second is reported as not read
    import pymupdf

    doc = pymupdf.open()
    for _ in range(2):
        pg = doc.new_page()
        src = pymupdf.open()
        s = src.new_page()
        s.insert_text((50, 100), "Scanned page of words", fontsize=24)
        pg.insert_image(pg.rect, pixmap=s.get_pixmap(dpi=100))
    doc.save(tmp_path / "two.pdf")
    ocr2 = FakeOcr({1: "Recognised text from the first scanned page of the document."})
    ex = pipeline.process(tmp_path / "two.pdf", Settings(feature_ocr=True, ocr_max_pages=1), ocr2)
    assert [p.extraction for p in ex.pages] == ["ocr", "unreadable"]
    assert next(w for w in ex.warnings if w["code"] == "ocr_page_limit")["pages"] == [2]


def test_encrypted_pdf(tmp_path):
    f = fails(tmp_path, pdfs.make_pdf([PARA], password="pw"))
    assert f.code == "password_protected" and not f.retryable


def test_garbage_after_a_valid_header_is_a_clear_failure(tmp_path):
    f = fails(tmp_path, pdfs.corrupt_pdf())
    assert f.code in {"no_pages", "corrupt"} and f.message


def test_truncated_file_reports_lost_pages_as_unreadable_not_blank(tmp_path):
    good = pdfs.make_pdf([f"Page {i}. {PARA}" for i in range(6)])
    ex = run(tmp_path, pdfs.truncated(good, 0.5))
    kinds = [p.extraction for p in ex.pages]
    assert kinds[:3] == ["text"] * 3 and kinds[3:] == ["unreadable"] * 3 and "blank" not in kinds
    w = next(w for w in ex.warnings if w["code"] == "file_repaired")
    assert w["pages"] == [4, 5, 6]


def test_limits(tmp_path):
    data = pdfs.make_pdf([PARA] * 3)
    f = fails(tmp_path, data, upload_max_pages=2)
    assert f.code == "too_many_pages" and "3 pages" in f.message and not f.retryable
    assert fails(tmp_path, data, upload_max_extracted_chars=50).code == "text_too_large"
    assert fails(tmp_path, data, document_process_timeout_s=0.01).code == "parse_timeout"


def test_active_content_is_flagged_as_a_heuristic_never_executed(tmp_path):
    ex = run(tmp_path, pdfs.make_pdf([PARA], javascript=True))
    w = next(w for w in ex.warnings if w["code"] == "active_content")
    assert "JavaScript" in w["message"] and "not a malware scan" in w["message"]


# ---- classification and child-output handling (pure) ----


def test_page_classification_rules():
    c = pipeline.classify
    assert c({"text": "", "image_count": 0})[0] == "blank"
    assert c({"text": "  \n ", "image_count": 0})[0] == "blank"
    assert c({"text": "12", "image_count": 0})[0] == "text"  # a page number is not a scan
    assert c({"text": "", "image_count": 1, "image_cover": 0.9})[0] == "needs_ocr"
    assert c({"text": "x" * 40, "image_count": 1, "image_cover": 0.1})[0] == "text"  # a figure on a text page
    assert c({"text": "Downloaded from a site " * 3, "image_count": 1, "image_cover": 0.95})[0] == "needs_ocr"
    assert c({"text": "word " * 100, "image_count": 1, "image_cover": 0.95})[0] == "text"
    assert c({"text": "\ufffd" * 80 + " ok", "image_count": 0})[0] == "garbled"
    assert c({"text": "anything", "error": "RuntimeError"}) == ("failed", "")


def test_a_child_that_died_is_reported_not_trusted(monkeypatch, tmp_path):
    (tmp_path / "t.pdf").write_bytes(pdfs.make_pdf([PARA]))
    dead = runner.ChildResult(
        returncode=-11,
        stdout=b'{"type": "header", "page_count": 1}\n',
        stderr_tail="",
        timed_out=False,
        output_truncated=False,
    )
    monkeypatch.setattr(runner, "run_child", lambda *a, **k: dead)
    with pytest.raises(pipeline.ProcessingFailure) as e:
        pipeline.extract(tmp_path / "t.pdf", Settings())
    assert e.value.code == "parser_crashed"


def test_missing_pages_in_child_output_are_detected(monkeypatch, tmp_path):
    out = b"\n".join(
        json.dumps(x).encode()
        for x in [
            {"type": "header", "page_count": 3, "metadata": {}},
            {"type": "page", "page_no": 1, "text": "a", "image_count": 0, "image_cover": 0},
            {"type": "done", "pages": 3},
        ]
    )
    res = runner.ChildResult(0, out, "", False, False)
    monkeypatch.setattr(runner, "run_child", lambda *a, **k: res)
    with pytest.raises(pipeline.ProcessingFailure) as e:
        pipeline.extract(tmp_path / "t.pdf", Settings())
    assert e.value.code == "pages_missing" and "1 of 3" in e.value.message


# ---- OCR ----

TSV = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t",
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95.5\tHello",
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t80.5\tworld",
        "5\t1\t1\t1\t2\t1\t0\t0\t10\t10\t90\tsecond",
        "5\t1\t1\t2\t1\t1\t0\t0\t10\t10\t70\tNew",
        "5\t1\t1\t2\t1\t2\t0\t0\t10\t10\t-1\t",
    ]
)


def test_tesseract_tsv_is_rebuilt_into_lines_and_paragraphs_with_mean_confidence():
    r = parse_tsv(TSV)
    assert r.text == "Hello world\nsecond\n\nNew" and r.confidence == 84.0
    assert parse_tsv("header only").text == "" and parse_tsv("").confidence is None


needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None and os.environ.get("REQUIRE_OCR_TESTS") != "1",
    reason="tesseract is not installed",
)


@needs_tesseract
def test_real_tesseract_reads_a_scanned_page_end_to_end(tmp_path):
    """No fakes: a picture of text goes through the render child, Tesseract and the pipeline."""
    s = Settings(feature_ocr=True)
    ocr = TesseractOcr(s.tesseract_cmd, s.ocr_languages, s.ocr_timeout_per_page_s)
    assert ocr.available(), "REQUIRE_OCR_TESTS=1 but tesseract (with the eng language data) is not installed"
    ex = run(
        tmp_path, pdfs.scanned_page_pdf("Exercise reduces depressive symptoms in older adults"), ocr, feature_ocr=True
    )
    assert ex.pages[0].extraction == "ocr" and ex.pages[0].ocr_confidence and ex.pages[0].ocr_confidence > 60
    assert "depressive" in ex.pages[0].text.lower() and "older" in ex.pages[0].text.lower()


def test_tesseract_with_a_missing_binary_or_language_is_unavailable_not_an_error():
    assert TesseractOcr("definitely-not-installed-xyz", "eng", 5).available() is False
    if shutil.which("tesseract"):
        assert TesseractOcr("tesseract", "xxx_not_a_language", 5).available() is False
