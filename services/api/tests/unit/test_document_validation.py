"""Upload validation, streaming intake and text hygiene (no database, no PDF parser)."""

import asyncio
import os
import stat

import pytest

from app.documents import intake
from app.documents import validation as v
from app.documents.text import clean_page_text, garbled_ratio, match_string, match_tokens


def test_filenames_are_reduced_to_safe_display_text():
    assert v.sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert v.sanitize_filename("C:\\Users\\me\\paper.pdf") == "paper.pdf"
    assert v.sanitize_filename("a\u202eb.fdp.pdf") == "ab.fdp.pdf"  # bidi override cannot spoof an extension
    assert v.sanitize_filename("") == "document.pdf" and v.sanitize_filename(None) == "document.pdf"
    assert v.sanitize_filename("  ...  ") == "document.pdf"
    long = v.sanitize_filename("x" * 400 + ".pdf")
    assert len(long) <= v.MAX_FILENAME_LENGTH and long.endswith(".pdf")


def test_header_decoding_is_percent_utf8_and_bounded():
    assert v.decode_filename_header("R%C3%A9sum%C3%A9.pdf") == "Résumé.pdf"
    assert v.decode_filename_header(None) == "" and v.decode_filename_header("a" * 5000) == ""


@pytest.mark.parametrize(
    "name,ok", [("a.pdf", True), ("A.PDF", True), ("a.pdf.exe", False), ("a.docx", False), ("pdf", False)]
)
def test_extension(name, ok):
    if ok:
        v.check_extension(name)
    else:
        with pytest.raises(v.UploadRejection) as e:
            v.check_extension(name)
        assert e.value.status_code == 415 and e.value.code == "unsupported_file_type"


@pytest.mark.parametrize(
    "ct", [None, "", "application/pdf", "Application/PDF; charset=binary", "application/octet-stream"]
)
def test_declared_types_accepted(ct):
    v.check_declared_type(ct)


@pytest.mark.parametrize("ct", ["image/png", "text/html", "application/zip", "application/json"])
def test_declared_types_rejected(ct):
    with pytest.raises(v.UploadRejection) as e:
        v.check_declared_type(ct)
    assert e.value.status_code == 415 and e.value.code == "unsupported_media_type"


@pytest.mark.parametrize(
    "head,ok",
    [
        (b"%PDF-1.7\n", True),
        (b"%PDF-2.0", True),
        (b"%PDF-1.4", True),
        (b" %PDF-1.7", False),
        (b"PK\x03\x04", False),
        (b"%PDF-", False),
        (b"", False),
        (b"%PDF-3.0", False),
    ],
)
def test_signature(head, ok):
    if ok:
        v.check_signature(head)
    else:
        with pytest.raises(v.UploadRejection) as e:
            v.check_signature(head)
        assert e.value.status_code == 422 and e.value.code == "not_a_pdf"


def test_eof_marker_detection():
    assert v.has_eof_marker(b"..." + b"x" * 2000 + b"%%EOF\n") and not v.has_eof_marker(b"x" * 2000)


async def _gen(*chunks):
    for c in chunks:
        yield c


def run(coro):
    return asyncio.run(coro)


def test_intake_streams_to_a_private_file_and_hashes_it():
    import hashlib

    body = b"%PDF-1.7\n" + b"x" * 3_000_000
    r = run(
        intake.receive(
            _gen(body[:1000], body[1000:2_000_000], body[2_000_000:]), max_bytes=10_000_000, declared_length=len(body)
        )
    )
    try:
        assert r.size == len(body) and r.sha256 == hashlib.sha256(body).hexdigest()
        assert r.path.read_bytes() == body and r.head == body[:8] and r.tail == body[-1024:]
        assert stat.S_IMODE(os.stat(r.path).st_mode) == 0o600
    finally:
        r.cleanup()
    assert not r.directory.exists()


def test_intake_rejects_oversize_without_reading_the_rest_and_leaves_nothing():
    consumed = []

    async def stream():
        for i in range(100):
            consumed.append(i)
            yield b"%PDF-1.7" + b"x" * 1000 if i == 0 else b"x" * 1000

    with pytest.raises(v.UploadRejection) as e:
        run(intake.receive(stream(), max_bytes=2500, declared_length=None))
    assert e.value.status_code == 413 and len(consumed) < 10  # stopped early


def test_intake_declared_length_over_the_limit_is_refused_before_reading():
    with pytest.raises(v.UploadRejection) as e:
        run(intake.receive(_gen(b"never read"), max_bytes=10, declared_length=11))
    assert e.value.code == "file_too_large"


def test_intake_rejects_non_pdf_early_and_empty_bodies():
    consumed = []

    async def stream():
        for i in range(50):
            consumed.append(i)
            yield b"<html>" + b"x" * 100

    with pytest.raises(v.UploadRejection) as e:
        run(intake.receive(stream(), max_bytes=10**6, declared_length=None))
    assert e.value.code == "not_a_pdf" and len(consumed) == 1
    with pytest.raises(v.UploadRejection) as e:
        run(intake.receive(_gen(), max_bytes=10**6, declared_length=None))
    assert e.value.code == "empty_file"
    with pytest.raises(v.UploadRejection) as e:
        run(intake.receive(_gen(b"%PD"), max_bytes=10**6, declared_length=None))  # shorter than the sniff window
    assert e.value.code == "not_a_pdf"


def test_intake_cleans_up_when_the_stream_fails_midway(tmp_path):
    async def stream():
        yield b"%PDF-1.7" + b"x" * 100
        raise ConnectionResetError("client went away")

    with pytest.raises(ConnectionResetError):
        run(intake.receive(stream(), max_bytes=10**6, declared_length=None, tmp_root=tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_text_cleaning_never_changes_wording_but_removes_what_postgres_rejects():
    assert clean_page_text("a\x00b\x07c\r\nd\re\tf") == "abc\nd\ne\tf"
    assert clean_page_text("ﬁnding Ünïcode 日本語") == "ﬁnding Ünïcode 日本語"  # ligatures and scripts untouched
    assert "\ud800" not in clean_page_text("x\ud800y")


def test_garbled_detection():
    assert garbled_ratio("") == 0.0 and garbled_ratio("a normal sentence") == 0.0
    assert garbled_ratio("\ufffd" * 8 + "ab") == 0.8
    assert garbled_ratio("(cid:12)(cid:13)(cid:14) ab") > 0.5
    assert garbled_ratio("\ue000\ue001\ue002 abc") > 0.3


def test_matching_tokens_fold_accents_and_case():
    assert match_tokens("Résumé: Aerobic-Exercise (2020)!") == ["resume", "aerobic", "exercise", "2020"]
    assert match_string("  A   B ") == "a b"
