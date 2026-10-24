"""PDF upload and full-text processing on real PostgreSQL, a real local object store and real PDF parsing child
processes (tests/fixtures/pdfs.py generates genuine PDFs). The broker hop is the only thing skipped.

Covered: validation, storage, quotas, duplicates, rate limit, processing, page text, OCR (fake engine; the real
Tesseract engine is covered in unit tests), matching, linking, download, removal, retention, orphan sweep, worker loss,
redelivery, and isolation between users."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.models import AuditEvent, DocumentPage, PaperDocument
from app.services import document_service, job_service
from tests.fakes import FakeOcr, FakeScanner
from tests.fixtures import pdfs
from tests.fixtures import scholarly as fx

from .support import papers_by_title, run_document_jobs, searched, upload

PARA = pdfs.PARA.format(n=50)


def doc_of(c, pid, doc_id):
    r = c.get(f"/api/v1/projects/{pid}/documents/{doc_id}")
    assert r.status_code == 200, r.text
    return r.json()


def pages_of(c, pid, doc_id, limit=50):
    return c.get(f"/api/v1/projects/{pid}/documents/{doc_id}/pages?limit={limit}").json()


def simple_pdf(n_pages=2, tag="a"):
    return pdfs.make_pdf([f"Page {i + 1} of document {tag}. " + PARA * 2 for i in range(n_pages)])


def process(dispatcher, store, **kw):
    return run_document_jobs(dispatcher, store, **kw)


# ---------- upload: acceptance ----------


def test_upload_stores_the_file_under_a_generated_key_and_queues_it(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    data = simple_pdf()
    d = upload(c, pid, data, name="My Paper (final).pdf")
    assert d["parse_status"] == "queued" and d["original_filename"] == "My Paper (final).pdf"
    assert d["size_bytes"] == len(data) and d["sha256"] == hashlib.sha256(data).hexdigest()
    assert d["match_status"] == "unmatched" and d["paper_id"] is None
    assert d["can_download"] is True and d["can_retry"] is False
    keys = [o.key for o in object_store.list_objects("projects/")]
    assert keys == [f"projects/{pid}/documents/{d['id']}.pdf"]  # generated; the user's file name is not in the key
    with object_store.open(keys[0]) as f:
        assert f.read() == data
    assert [t for t, _ in dispatcher.sent if t == "document.process"] == ["document.process"]


def test_a_hostile_file_name_is_only_ever_display_text(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    d = upload(c, pid, simple_pdf(), name="../../etc/passwd\u202e.fdp.pdf")
    assert "/" not in d["original_filename"] and ".." not in d["original_filename"].replace(".fdp", "")
    assert "\u202e" not in d["original_filename"]
    assert [o.key for o in object_store.list_objects("projects/")] == [f"projects/{pid}/documents/{d['id']}.pdf"]


# ---------- upload: rejection (nothing is stored, nothing is recorded) ----------


def _nothing_kept(db, object_store, pid):
    assert list(object_store.list_objects("projects/")) == []
    assert (
        db.scalar(select(func.count()).select_from(PaperDocument).where(PaperDocument.project_id == uuid.UUID(pid)))
        == 0
    )


def test_validation_rejects_bad_uploads_with_specific_codes(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    good = simple_pdf()

    def code(expect, **kw):
        r = upload(c, pid, kw.pop("data", good), expect=expect, **kw)
        return r["error"]["code"]

    assert code(415, name="paper.docx") == "unsupported_file_type"
    assert code(415, name="paper.pdf", content_type="image/png") == "unsupported_media_type"
    assert code(415, name="paper.pdf", content_type="text/html") == "unsupported_media_type"
    assert code(422, data=b"<html>not a pdf</html>" * 10, name="renamed.pdf") == "not_a_pdf"
    assert code(422, data=b"PK\x03\x04" + b"0" * 100, name="zip.pdf") == "not_a_pdf"
    assert code(422, data=b"", name="empty.pdf") == "empty_file"
    # octet-stream is accepted as a generic type: the signature decides
    assert upload(c, pid, good, name="ok.pdf", content_type="application/octet-stream")["parse_status"] == "queued"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(PaperDocument)) >= 1
    # the rejected ones left nothing: only the one accepted file is stored
    assert len(list(object_store.list_objects("projects/"))) == 1


def test_oversize_upload_is_refused_by_declared_length_and_while_streaming(user_client, set_settings, object_store, db):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    data = simple_pdf(3)
    set_settings(upload_max_bytes=len(data) - 1)
    r = upload(c, pid, data, expect=413)
    assert r["error"]["code"] == "file_too_large"
    _nothing_kept(db, object_store, pid)
    set_settings(upload_max_bytes=len(data))
    assert upload(c, pid, data)["size_bytes"] == len(data)  # exactly at the limit is fine


def test_duplicate_file_is_refused_until_the_first_is_removed(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    data = simple_pdf()
    first = upload(c, pid, data)
    r = upload(c, pid, data, name="other-name.pdf", expect=409)
    assert r["error"]["code"] == "duplicate_document"
    assert r["error"]["details"][0]["document_id"] == first["id"]
    assert len(list(object_store.list_objects("projects/"))) == 1
    assert c.delete(f"/api/v1/projects/{pid}/documents/{first['id']}").status_code == 204
    assert upload(c, pid, data)["id"] != first["id"]
    # another project may hold the same file
    pid2 = c.post("/api/v1/projects", json={"title": "P2", "topic": "t"}).json()["id"]
    assert upload(c, pid2, data)["parse_status"] == "queued"


def test_quotas_are_enforced_before_anything_is_stored(user_client, set_settings, object_store, db):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    a, b = simple_pdf(1, "a"), simple_pdf(1, "b")
    set_settings(upload_quota_files_per_project=1)
    upload(c, pid, a)
    assert upload(c, pid, b, expect=413)["error"]["code"] == "quota_files_exceeded"
    set_settings(
        upload_quota_files_per_project=10, upload_quota_bytes_per_project=len(a) + 10, upload_max_bytes=len(a) + 10
    )
    assert upload(c, pid, b, expect=413)["error"]["code"] == "quota_project_bytes_exceeded"
    set_settings(upload_quota_bytes_per_project=10**9, upload_quota_bytes_per_user=len(a) + 10)
    assert upload(c, pid, b, expect=413)["error"]["code"] == "quota_user_bytes_exceeded"
    assert len(list(object_store.list_objects("projects/"))) == 1
    s = c.get(f"/api/v1/projects/{pid}/documents/summary").json()
    assert s["usage"] == {"project_bytes": len(a), "project_files": 1, "user_bytes": len(a)}


def test_upload_rate_limit(user_client, set_settings):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    set_settings(rate_limit_upload_per_hour=2)
    upload(c, pid, simple_pdf(1, "1"))
    upload(c, pid, simple_pdf(1, "2"))
    r = upload(c, pid, simple_pdf(1, "3"), expect=429)
    assert r["error"]["code"] == "rate_limited"


# ---------- processing: text ----------


def test_text_pdf_is_parsed_into_pages_with_numbers(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    d = upload(c, pid, simple_pdf(3))
    assert process(dispatcher, object_store) == ["parsed"]
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["page_count"] == 3 and d["text_version"] == 1
    assert (d["pages_text"], d["pages_ocr"], d["pages_blank"], d["pages_unreadable"]) == (3, 0, 0, 0)
    assert d["text_coverage"] == 1.0 and d["error_code"] is None
    pg = pages_of(c, pid, d["id"])
    assert [p["page_no"] for p in pg["items"]] == [1, 2, 3] and pg["total"] == 3
    assert "Page 2 of document a." in pg["items"][1]["text"]
    assert all(p["extraction"] == "text" and p["char_count"] > 100 for p in pg["items"])
    part = pages_of(c, pid, d["id"], limit=2)
    assert len(part["items"]) == 2
    nxt = c.get(f"/api/v1/projects/{pid}/documents/{d['id']}/pages?offset=2&limit=2").json()
    assert [p["page_no"] for p in nxt["items"]] == [3]


def test_a_blank_page_is_reported_as_blank_not_as_missing(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    d = upload(c, pid, pdfs.make_pdf(["Real text. " + PARA, None, "More text. " + PARA]))
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and (d["pages_text"], d["pages_blank"]) == (2, 1)
    assert [p["extraction"] for p in pages_of(c, pid, d["id"])["items"]] == ["text", "blank", "text"]


def test_pdf_metadata_is_kept(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    d = upload(c, pid, pdfs.make_pdf([PARA * 2], metadata={"title": "A Title", "author": "A. Author"}))
    process(dispatcher, object_store)
    assert doc_of(c, pid, d["id"])["pdf_metadata"]["title"] == "A Title"


# ---------- processing: failures are visible, specific and actionable ----------


def failed(c, pid, data, dispatcher, store, **kw):
    d = upload(c, pid, data)
    assert process(dispatcher, store, **kw) == ["failed"]
    return doc_of(c, pid, d["id"])


def new_project(c):
    return c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]


def test_encrypted_pdf_fails_with_a_clear_message_and_cannot_be_retried(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = failed(c, pid, pdfs.make_pdf(["secret " * 40], password="pw"), dispatcher, object_store)
    assert d["parse_status"] == "failed" and d["error_code"] == "password_protected"
    assert "password" in d["error_message"].lower() and d["can_retry"] is False
    r = c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_retryable"
    assert pages_of(c, pid, d["id"])["items"] == []


def test_corrupt_file_fails_without_crashing_the_worker(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = failed(c, pid, pdfs.corrupt_pdf(), dispatcher, object_store)
    assert d["error_code"] in {"corrupt", "no_pages"} and d["error_message"]


def test_truncated_pdf_never_reports_lost_pages_as_blank(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    good = pdfs.make_pdf([f"Page {i}. " + PARA * 3 for i in range(6)])
    d = upload(c, pid, pdfs.truncated(good, 0.5))
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["pages_blank"] == 0 and d["pages_unreadable"] == 3
    warn = next(w for w in d["warnings"] if w["code"] == "file_repaired")
    assert warn["pages"] == [4, 5, 6]
    assert d["text_coverage"] == 0.5


def test_too_many_pages_and_text_bomb_guards(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    s = get_settings().model_copy(update={"upload_max_pages": 2})
    d = upload(c, pid, simple_pdf(3, "big"))
    assert process(dispatcher, object_store, settings=s) == ["failed"]
    d = doc_of(c, pid, d["id"])
    assert d["error_code"] == "too_many_pages" and "3 pages" in d["error_message"] and d["can_retry"] is False
    d2 = upload(c, pid, simple_pdf(2, "text"))
    s = get_settings().model_copy(update={"upload_max_extracted_chars": 50})
    process(dispatcher, object_store, settings=s)
    assert doc_of(c, pid, d2["id"])["error_code"] == "text_too_large"


def test_a_parse_timeout_is_retryable_and_the_retry_succeeds(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(2, "slow"))
    process(dispatcher, object_store, settings=get_settings().model_copy(update={"document_process_timeout_s": 0.01}))
    d = doc_of(c, pid, d["id"])
    assert d["error_code"] == "parse_timeout" and d["can_retry"] is True
    r = c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/retry")
    assert r.status_code == 202 and r.json()["parse_status"] == "queued" and r.json()["error_code"] is None
    assert process(dispatcher, object_store) == ["parsed"]
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["text_version"] == 1  # the failed attempt wrote no pages


def test_a_swapped_or_damaged_stored_file_is_detected_by_its_checksum(user_client, dispatcher, object_store, tmp_path):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "orig"))
    key = f"projects/{pid}/documents/{d['id']}.pdf"
    other = tmp_path / "other.pdf"
    other.write_bytes(simple_pdf(1, "swapped"))
    object_store.put_file(key, other)
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["error_code"] == "integrity_mismatch" and d["can_retry"] is False and d["page_count"] is None


def test_a_missing_stored_file_fails_cleanly(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "gone"))
    object_store.delete(f"projects/{pid}/documents/{d['id']}.pdf")
    process(dispatcher, object_store)
    assert doc_of(c, pid, d["id"])["error_code"] == "file_missing"
    assert c.get(f"/api/v1/projects/{pid}/documents/{d['id']}/download").status_code == 404


def test_redelivery_of_a_finished_job_changes_nothing(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(2, "once"))
    sent = list(dispatcher.sent)
    assert process(dispatcher, object_store) == ["parsed"]
    # the same message arrives again: the job is already finished, so the claim fails and nothing runs
    dispatcher.sent = sent
    assert process(dispatcher, object_store) == ["ignored"]
    assert (
        db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.document_id == uuid.UUID(d["id"])))
        == 2
    )


def test_an_unexpected_crash_marks_the_document_failed_not_processing(
    user_client, dispatcher, object_store, monkeypatch
):
    from app.documents import pipeline
    from app.jobs import tasks

    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "boom"))
    monkeypatch.setattr(pipeline, "process", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    monkeypatch.setattr("app.services.document_executor.build_store", lambda s: object_store)
    jid = next(j for t, j in dispatcher.sent if t == "document.process")
    assert tasks.document_process(jid) == "failed"
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "failed" and d["error_code"] == "internal_error" and d["can_retry"] is True


def test_the_sweep_fails_a_document_whose_worker_was_lost(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "lost"))
    db.execute(text("UPDATE paper_documents SET parse_status='processing' WHERE id=:i"), {"i": d["id"]})
    db.execute(
        text(
            "UPDATE jobs SET status='running', attempts=max_attempts, heartbeat_at=:t "
            "WHERE type='document.process' AND project_id=:p"
        ),
        {"t": datetime.now(UTC) - timedelta(hours=1), "p": pid},
    )
    db.commit()
    job_service.sweep(db, get_settings(), lambda *_: None)
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "failed" and d["error_code"] == "worker_lost" and d["can_retry"] is True


def test_a_document_removed_while_queued_is_skipped(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "rm"))
    assert c.delete(f"/api/v1/projects/{pid}/documents/{d['id']}").status_code == 204
    assert process(dispatcher, object_store) == ["skipped"]
    n = db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.document_id == uuid.UUID(d["id"])))
    assert n == 0


# ---------- malware scanning ----------


def test_an_infected_file_is_deleted_and_blocked(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "evil"))
    process(dispatcher, object_store, scanner=FakeScanner("infected", "Eicar-Test"))
    d = doc_of(c, pid, d["id"])
    assert d["error_code"] == "malware_detected" and d["scan_status"] == "infected"
    assert d["can_download"] is False and d["can_retry"] is False
    assert list(object_store.list_objects("projects/")) == []
    assert c.get(f"/api/v1/projects/{pid}/documents/{d['id']}/download").status_code in (404, 409)


def test_scanner_errors_fail_closed_only_when_a_scan_is_required(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    a = upload(c, pid, simple_pdf(1, "req"))
    process(
        dispatcher,
        object_store,
        scanner=FakeScanner("error"),
        settings=get_settings().model_copy(update={"malware_scan_required": True}),
    )
    assert doc_of(c, pid, a["id"])["error_code"] == "scan_unavailable"
    b = upload(c, pid, simple_pdf(1, "opt"))
    process(dispatcher, object_store, scanner=FakeScanner("error"))
    b = doc_of(c, pid, b["id"])
    assert b["parse_status"] == "parsed" and any(w["code"] == "scan_failed" for w in b["warnings"])
    clean = upload(c, pid, simple_pdf(1, "clean"))
    sc = FakeScanner("clean")
    process(dispatcher, object_store, scanner=sc)
    assert doc_of(c, pid, clean["id"])["scan_status"] == "clean" and sc.scanned == 1


# ---------- OCR (fake engine here; the real Tesseract engine is tested in unit tests) ----------


def test_scanned_pdf_without_ocr_fails_with_an_instruction(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = failed(c, pid, pdfs.scanned_page_pdf("Scanned abstract about exercise"), dispatcher, object_store)
    assert d["error_code"] == "scanned_no_ocr" and "OCR" in d["error_message"] and d["can_retry"] is True


def test_enabling_ocr_and_retrying_reads_the_scanned_page(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = failed(c, pid, pdfs.scanned_page_pdf("Scanned abstract about exercise"), dispatcher, object_store)
    on = get_settings().model_copy(update={"feature_ocr": True})
    ocr = FakeOcr({1: "Scanned abstract about exercise and depression in older adults."}, confidence=88.0)
    assert c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/retry").status_code == 202
    assert process(dispatcher, object_store, ocr=ocr, settings=on) == ["parsed"]
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["pages_ocr"] == 1 and d["error_code"] is None
    page = pages_of(c, pid, d["id"])["items"][0]
    assert page["extraction"] == "ocr" and page["ocr_confidence"] == 88.0 and "depression" in page["text"]
    assert next(w for w in d["warnings"] if w["code"] == "ocr_used")["pages"] == [1]
    assert ocr.calls == [1]


def test_ocr_runs_only_on_pages_that_need_it(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    data = pdfs.scanned_page_pdf("Scan text", before=["Ordinary page one. " + PARA * 2, None])
    on = get_settings().model_copy(update={"feature_ocr": True})
    ocr = FakeOcr({3: "Recognised words from the scanned third page of this paper."})
    d = upload(c, pid, data)
    process(dispatcher, object_store, ocr=ocr, settings=on)
    d = doc_of(c, pid, d["id"])
    assert ocr.calls == [3]  # the text page and the blank page were never OCR'd
    assert [p["extraction"] for p in pages_of(c, pid, d["id"])["items"]] == ["text", "blank", "ocr"]


def test_reprocessing_with_ocr_adds_a_new_text_version_and_keeps_the_old_pages(
    user_client, dispatcher, object_store, db
):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, pdfs.scanned_page_pdf("Scan", before=["Ordinary page. " + PARA * 2]))
    process(dispatcher, object_store)  # OCR off: parsed, page 2 unreadable
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["pages_unreadable"] == 1 and d["text_version"] == 1
    assert d["can_retry"] is False  # OCR is off in the API's settings: nothing a retry could improve
    set_on = get_settings().model_copy(update={"feature_ocr": True})
    c.app.dependency_overrides[get_settings] = lambda: set_on  # type: ignore[attr-defined]
    d = doc_of(c, pid, d["id"])
    assert d["can_retry"] is True
    assert c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/retry").status_code == 202
    process(dispatcher, object_store, ocr=FakeOcr({2: "Now readable text recognised from the scan."}), settings=set_on)
    d = doc_of(c, pid, d["id"])
    assert d["text_version"] == 2 and d["pages_ocr"] == 1 and d["pages_unreadable"] == 0
    versions = db.execute(
        text("SELECT text_version, count(*) FROM document_pages WHERE document_id=:i GROUP BY 1 ORDER BY 1"),
        {"i": d["id"]},
    ).all()
    assert [tuple(v) for v in versions] == [(1, 2), (2, 2)]  # the first extraction is kept for anything that cited it
    assert len(pages_of(c, pid, d["id"])["items"]) == 2  # but only the current one is shown


def test_a_failed_reprocess_never_hides_the_text_already_extracted(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, pdfs.scanned_page_pdf("Scan", before=["Ordinary page. " + PARA * 2]))
    process(dispatcher, object_store)
    on = get_settings().model_copy(update={"feature_ocr": True, "document_process_timeout_s": 0.01})
    c.app.dependency_overrides[get_settings] = lambda: on  # type: ignore[attr-defined]
    assert c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/retry").status_code == 202
    assert process(dispatcher, object_store, settings=on) == ["failed"]
    d = doc_of(c, pid, d["id"])
    assert d["parse_status"] == "parsed" and d["text_version"] == 1
    assert any(w["code"] == "reprocess_failed" for w in d["warnings"])
    assert pages_of(c, pid, d["id"])["items"][0]["extraction"] == "text"


# ---------- matching to papers ----------


def library(user_client, dispatcher):
    """An approved project with 4 real (mocked-provider) papers and their ids by title."""
    c, _, uid = user_client()
    pid, _ = searched(c, dispatcher)
    return c, pid, papers_by_title(c, pid), uid


def paper_of(c, pid, paper_id):
    return c.get(f"/api/v1/projects/{pid}/papers/{paper_id}").json()


def test_a_pdf_with_the_papers_doi_and_title_is_matched_and_the_paper_shows_full_text(
    user_client, dispatcher, object_store
):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1 = papers[fx.T_W1]
    assert w1["full_text_status"] == "not_obtained"
    d = upload(c, pid, pdfs.paper_pdf(fx.T_W1, "Ann Smith", 2020, fx.DOI_W1))
    assert d["match_status"] == "unmatched"  # nothing is linked before the file has been read
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["match_status"] == "auto_matched" and d["paper_id"] == w1["id"] and d["match_method"] == "doi"
    assert d["paper_title"] == fx.T_W1 and d["match_score"] >= 0.9
    p = paper_of(c, pid, w1["id"])
    assert p["full_text_status"] == "parsed" and [x["id"] for x in p["documents"]] == [d["id"]]


def test_an_uncertain_match_is_not_linked_until_the_user_confirms(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1 = papers[fx.T_W1]
    # same study, but the title is reworded and there is no DOI on the page: plausible, not certain
    reworded = "Aerobic exercise for depression among older people: a randomised trial"
    d = upload(c, pid, pdfs.paper_pdf(reworded, "A. Smith", 2020, None))
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["match_status"] == "needs_confirmation" and d["needs_confirmation"] is True and d["paper_id"] is None
    assert d["candidates"][0]["paper_id"] == w1["id"] and d["candidates"][0]["title"] == fx.T_W1
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "not_obtained"  # not linked, so nothing is claimed
    r = c.post(f"/api/v1/projects/{pid}/documents/{d['id']}/link", json={"paper_id": w1["id"]})
    assert r.status_code == 200 and r.json()["match_status"] == "confirmed" and r.json()["paper_id"] == w1["id"]
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "parsed"


def test_a_doi_that_is_only_cited_does_not_cause_an_automatic_match(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    d = upload(
        c,
        pid,
        pdfs.paper_pdf(
            "Yoga for chronic lower back pain in adults", "Zed Adams", 2018, None, extra=f" See also doi:{fx.DOI_W2}."
        ),
    )
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["match_status"] != "auto_matched" and d["paper_id"] is None


def test_an_unrelated_pdf_stays_unmatched(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    d = upload(
        c,
        pid,
        pdfs.paper_pdf(
            "Quantum error correction in superconducting circuits", "Q. Physicist", 2022, "10.9999/quantum.1"
        ),
    )
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["match_status"] == "unmatched" and d["paper_id"] is None and d["candidates"] == []


def test_a_pdf_the_user_attaches_to_a_paper_is_linked_but_flagged_when_its_content_looks_like_another(
    user_client, dispatcher, object_store
):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1, w2 = papers[fx.T_W1], papers[fx.T_W2]
    d = upload(c, pid, pdfs.paper_pdf(fx.T_W1, "Ann Smith", 2020, fx.DOI_W1), paper_id=w2["id"])
    assert d["match_status"] == "user_linked" and d["paper_id"] == w2["id"]
    assert paper_of(c, pid, w2["id"])["full_text_status"] == "uploaded"  # queued, not yet read
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    assert d["match_status"] == "user_linked" and d["paper_id"] == w2["id"]  # the user's choice stands
    assert any(w["code"] == "content_may_not_match" for w in d["warnings"])
    assert paper_of(c, pid, w2["id"])["full_text_status"] == "parsed"
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "not_obtained"


def test_a_pdf_attached_to_the_right_paper_gets_no_warning(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1 = papers[fx.T_W1]
    d = upload(c, pid, pdfs.paper_pdf(fx.T_W1, "Ann Smith", 2020, fx.DOI_W1), paper_id=w1["id"])
    process(dispatcher, object_store)
    assert not any(w["code"] == "content_may_not_match" for w in doc_of(c, pid, d["id"])["warnings"])


def test_upload_for_an_unknown_paper_or_another_projects_paper_is_404_and_stores_nothing(
    user_client, dispatcher, object_store
):
    c, pid, papers, _ = library(user_client, dispatcher)
    other = new_project(c)
    r = upload(c, other, simple_pdf(1, "x"), paper_id=papers[fx.T_W1]["id"], expect=404)
    assert r["error"]["code"] == "not_found"
    assert upload(c, pid, simple_pdf(1, "y"), paper_id=str(uuid.uuid4()), expect=404)
    assert list(object_store.list_objects("projects/")) == []


# ---------- linking, relinking, unlinking ----------


def test_relink_and_unlink_keep_both_papers_status_correct(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1, w2 = papers[fx.T_W1], papers[fx.T_W2]
    d = upload(c, pid, simple_pdf(2, "rl"), paper_id=w1["id"])
    process(dispatcher, object_store)
    base = f"/api/v1/projects/{pid}/documents/{d['id']}/link"
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "parsed"
    r = c.post(base, json={"paper_id": w2["id"]})
    assert r.status_code == 200 and r.json()["paper_id"] == w2["id"] and r.json()["match_status"] == "user_linked"
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "not_obtained"
    assert paper_of(c, pid, w2["id"])["full_text_status"] == "parsed"
    r = c.post(base, json={"paper_id": None})
    assert r.json()["match_status"] == "unmatched" and r.json()["paper_id"] is None
    assert paper_of(c, pid, w2["id"])["full_text_status"] == "not_obtained"
    assert c.post(base, json={"paper_id": None}).status_code == 200  # unlinking again is a no-op


def test_linking_uses_optimistic_concurrency(user_client, dispatcher, object_store):
    c, pid, papers, _ = library(user_client, dispatcher)
    d = upload(c, pid, simple_pdf(1, "oc"))
    process(dispatcher, object_store)
    d = doc_of(c, pid, d["id"])
    base = f"/api/v1/projects/{pid}/documents/{d['id']}/link"
    ok = c.post(base, json={"paper_id": papers[fx.T_W1]["id"], "expected_version": d["row_version"]})
    assert ok.status_code == 200
    stale = c.post(base, json={"paper_id": papers[fx.T_W2]["id"], "expected_version": d["row_version"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert c.post(base, json={"paper_id": str(uuid.uuid4())}).status_code == 404


# ---------- download, removal, retention ----------


def test_download_returns_the_original_bytes_as_an_attachment_and_is_audited(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid = new_project(c)
    data = simple_pdf(2, "dl")
    d = upload(c, pid, data, name="Résumé of trial.pdf")
    process(dispatcher, object_store)
    r = c.get(f"/api/v1/projects/{pid}/documents/{d['id']}/download")
    assert r.status_code == 200 and r.content == data
    h = r.headers
    assert h["content-type"] == "application/pdf" and h["x-content-type-options"] == "nosniff"
    assert (
        h["content-disposition"].startswith("attachment;")
        and "filename*=UTF-8''R%C3%A9sum%C3%A9%20of%20trial.pdf" in h["content-disposition"]
    )
    assert "no-store" in h["cache-control"] and "sandbox" in h["content-security-policy"]
    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == uuid.UUID(d["id"]))))
    assert {"document.uploaded", "document.processed", "document.downloaded"} <= actions


def test_removed_documents_disappear_at_once_and_their_files_are_purged_after_retention(
    user_client, dispatcher, object_store, db
):
    c, pid, papers, _ = library(user_client, dispatcher)
    w1 = papers[fx.T_W1]
    d = upload(c, pid, simple_pdf(2, "rm2"), paper_id=w1["id"])
    process(dispatcher, object_store)
    key = f"projects/{pid}/documents/{d['id']}.pdf"
    assert c.delete(f"/api/v1/projects/{pid}/documents/{d['id']}").status_code == 204
    assert c.get(f"/api/v1/projects/{pid}/documents/{d['id']}").status_code == 404
    assert c.get(f"/api/v1/projects/{pid}/documents").json()["total"] == 0
    assert paper_of(c, pid, w1["id"])["full_text_status"] == "not_obtained"
    assert object_store.exists(key)  # retained until the retention period has passed
    held = c.get(f"/api/v1/projects/{pid}/documents/summary").json()["usage"]["project_bytes"]
    assert held > 0  # still counts towards the quota while stored
    keep = get_settings().model_copy(update={"document_removed_retention_days": 30})
    document_service.purge(db, keep, object_store)
    assert object_store.exists(key)  # 30 days have not passed
    now_ = get_settings().model_copy(update={"document_removed_retention_days": 0})
    # (the database is shared with other tests, so other removed documents may be purged here too)
    assert document_service.purge(db, now_, object_store)["purged_documents"] >= 1
    assert not object_store.exists(key)
    row = db.scalar(select(PaperDocument).where(PaperDocument.id == uuid.UUID(d["id"])))
    db.refresh(row)
    assert row.purged_at is not None and row.storage_key is None and row.sha256  # the hash and timeline remain
    assert db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.document_id == row.id)) == 0
    assert c.get(f"/api/v1/projects/{pid}/documents/summary").json()["usage"]["project_bytes"] == 0
    assert document_service.purge(db, now_, object_store)["purged_documents"] == 0  # idempotent


def test_orphan_sweep_removes_old_unreferenced_files_only(user_client, dispatcher, object_store, db):
    import os

    c, _, _ = user_client()
    pid = new_project(c)
    d = upload(c, pid, simple_pdf(1, "kept"))
    known = f"projects/{pid}/documents/{d['id']}.pdf"
    orphan = f"projects/{pid}/documents/{uuid.uuid4()}.pdf"
    fresh = f"projects/{pid}/documents/{uuid.uuid4()}.pdf"
    src = object_store.root / "src.bin"
    src.write_bytes(b"orphan")
    for k in (orphan, fresh):
        object_store.put_file(k, src)
    path = object_store.root / orphan
    old = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(path, (old, old))
    out = document_service.purge(db, get_settings(), object_store)
    assert out["orphan_objects_deleted"] == 1
    assert not object_store.exists(orphan) and object_store.exists(fresh) and object_store.exists(known)


def test_deleting_a_project_deletes_its_files_and_rows_but_not_the_audit_trail(
    user_client, dispatcher, object_store, db
):
    c, _, _ = user_client()
    pid = new_project(c)
    other = new_project(c)
    d = upload(c, pid, simple_pdf(2, "pd"))
    o = upload(c, other, simple_pdf(2, "other-project"))
    process(dispatcher, object_store)
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204
    assert [x.key for x in object_store.list_objects("projects/")] == [f"projects/{other}/documents/{o['id']}.pdf"]
    db.expire_all()
    assert (
        db.scalar(select(func.count()).select_from(PaperDocument).where(PaperDocument.project_id == uuid.UUID(pid)))
        == 0
    )
    assert (
        db.scalar(select(func.count()).select_from(DocumentPage).where(DocumentPage.project_id == uuid.UUID(pid))) == 0
    )
    assert (
        db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(d["id"]))) >= 2
    )


# ---------- listing, summary, isolation ----------


def test_list_filters_summary_and_validation(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid = new_project(c)
    ok = upload(c, pid, simple_pdf(1, "ok"), name="alpha-trial.pdf")
    bad = upload(c, pid, pdfs.corrupt_pdf(), name="broken.pdf")
    process(dispatcher, object_store)
    base = f"/api/v1/projects/{pid}/documents"
    assert c.get(base).json()["total"] == 2
    assert [x["id"] for x in c.get(f"{base}?status=failed").json()["items"]] == [bad["id"]]
    assert [x["id"] for x in c.get(f"{base}?q=alpha").json()["items"]] == [ok["id"]]
    assert c.get(f"{base}?q=%25").json()["total"] == 0  # a literal % is not a wildcard
    assert c.get(f"{base}?status=").json()["total"] == 2  # empty means no filter
    assert c.get(f"{base}?status=bogus").status_code == 422
    assert c.get(f"{base}?match=bogus").status_code == 422
    s = c.get(f"{base}/summary").json()
    assert s["counts"]["parse"] == {"parsed": 1, "failed": 1} and s["usage"]["project_files"] == 2
    assert s["limits"]["max_bytes"] == get_settings().upload_max_bytes and s["limits"]["ocr_enabled"] is False


def test_another_user_cannot_see_change_download_or_upload_into_a_document_project(
    user_client, dispatcher, object_store
):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = new_project(a)
    d = upload(a, pid, simple_pdf(1, "mine"))
    process(dispatcher, object_store)
    base = f"/api/v1/projects/{pid}/documents"
    for method, url in [
        ("GET", base),
        ("GET", f"{base}/summary"),
        ("GET", f"{base}/{d['id']}"),
        ("GET", f"{base}/{d['id']}/pages"),
        ("GET", f"{base}/{d['id']}/download"),
        ("POST", f"{base}/{d['id']}/retry"),
        ("DELETE", f"{base}/{d['id']}"),
    ]:
        assert b.request(method, url).status_code == 404, (method, url)
    assert b.post(f"{base}/{d['id']}/link", json={"paper_id": None}).status_code == 404
    r = b.post(
        base, content=simple_pdf(1, "intruder"), headers={"Content-Type": "application/pdf", "X-Filename": "x.pdf"}
    )
    assert r.status_code == 404
    assert [o.key for o in object_store.list_objects("projects/")] == [f"projects/{pid}/documents/{d['id']}.pdf"]
    assert a.get(f"{base}/{d['id']}").json()["parse_status"] == "parsed"
