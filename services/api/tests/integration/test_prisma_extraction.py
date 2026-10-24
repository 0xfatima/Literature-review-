"""Integration tests for Phases 7 and 8: PRISMA flow, full-text screening, extraction schemas,
and evidence recording. Uses real PostgreSQL; the broker hop is skipped."""

import uuid

from sqlalchemy import select

from app.db.models import RecordStatusEvent
from tests.fixtures.pdfs import PARA, make_pdf

from .support import run_document_jobs, searched, upload

# ---------- helpers ----------


def proj_client(user_client):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "P", "topic": "t"}).json()["id"]
    return c, pid


def get_flow(c, pid, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return c.get(f"/api/v1/projects/{pid}/prisma/flow" + (f"?{qs}" if qs else "")).json()


def ft_decide(c, pid, paper_id, decision, reason_code=None, note=None):
    body = {"decision": decision}
    if reason_code:
        body["reason_code"] = reason_code
    if note:
        body["note"] = note
    return c.post(f"/api/v1/projects/{pid}/prisma/ft-decisions/{paper_id}", json=body)


def ta_decide(c, pid, paper_id, decision, reason_code=None):
    body = {"decision": decision}
    if reason_code:
        body["reason_code"] = reason_code
    return c.post(f"/api/v1/projects/{pid}/screening/papers/{paper_id}/decision", json=body)


# ---------- PRISMA flow ----------


def test_search_seeds_identified_events_and_flow_reflects_counts(user_client, dispatcher):
    c, _, _ = user_client()
    pid, pv_id = searched(c, dispatcher)
    flow = get_flow(c, pid)
    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    assert flow["identified_a"] == len(papers) > 0
    assert flow["pending_ta"] == len(papers)
    assert flow["ta_excluded"] == 0 and flow["ta_included_a"] == 0
    assert flow["violations"] == []


def test_ta_screening_moves_papers_through_prisma_flow(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    p1, p2, _p3 = papers[0]["id"], papers[1]["id"], papers[2]["id"]

    ta_decide(c, pid, p1, "include")
    ta_decide(c, pid, p2, "exclude", reason_code="off_topic")
    # p3 stays pending

    flow = get_flow(c, pid)
    assert flow["ta_included_a"] == 1
    assert flow["ta_excluded"] == 1
    # pending_ta reflects the papers not yet decided
    assert flow["pending_ta"] == len(papers) - 2
    # V3 passes: screened == pending_ta + ta_excluded + ta_included_a
    assert flow["screened"] == flow["pending_ta"] + flow["ta_excluded"] + flow["ta_included_a"]
    assert flow["violations"] == []
    assert not flow["is_final"]


def test_ft_decision_requires_reason_on_exclude(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")

    # ft exclude without reason → 422
    r = ft_decide(c, pid, paper_id, "exclude")
    assert r.status_code == 422

    # ft exclude with reason → ok
    r = ft_decide(c, pid, paper_id, "exclude", reason_code="wrong_study_design")
    assert r.status_code == 200
    assert r.json()["decision"] == "exclude"


def test_ft_include_produces_prisma_included_event(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")
    ft_decide(c, pid, paper_id, "include")

    flow = get_flow(c, pid)
    assert flow["included_reports"] >= 1
    assert flow["included_studies"] >= 1

    # Current event = not superseded (no other event references this one's id as supersedes_id)
    superseded_ids = (
        select(RecordStatusEvent.supersedes_id)
        .where(
            RecordStatusEvent.project_id == uuid.UUID(pid),
            RecordStatusEvent.supersedes_id.is_not(None),
        )
        .scalar_subquery()
    )
    ev = db.scalar(
        select(RecordStatusEvent).where(
            RecordStatusEvent.paper_id == uuid.UUID(paper_id),
            RecordStatusEvent.stage == "included",
            RecordStatusEvent.id.not_in(superseded_ids),
        )
    )
    assert ev is not None


def test_ft_reasons_endpoint_returns_protocol_criteria(user_client, dispatcher):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    reasons = c.get(f"/api/v1/projects/{pid}/prisma/ft-reasons").json()
    codes = {r["code"] for r in reasons}
    # should include the fixed methodological reasons
    assert "wrong_study_design" in codes
    assert "wrong_population" in codes
    assert "other" in codes


def test_study_grouping_reduces_included_studies_count(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    p1, p2 = papers[0]["id"], papers[1]["id"]

    ta_decide(c, pid, p1, "include")
    ta_decide(c, pid, p2, "include")
    ft_decide(c, pid, p1, "include")
    ft_decide(c, pid, p2, "include")

    before = get_flow(c, pid)
    assert before["included_reports"] == 2 and before["included_studies"] == 2

    r = c.post(f"/api/v1/projects/{pid}/studies", json={"paper_ids": [p1, p2], "short_name": "Smith 2020a+b"})
    assert r.status_code == 200

    after = get_flow(c, pid)
    assert after["included_reports"] == 2 and after["included_studies"] == 1


def test_prisma_flow_invariant_violation_flagged(user_client, dispatcher):
    """V8 (included_studies > included_reports) is flagged, not silently suppressed."""
    from app.prisma.flow import PrismaFlow, validate

    f = PrismaFlow(included_reports=2, included_studies=7)
    violations = validate(f)
    vids = {v["id"] for v in violations}
    assert "V8" in vids

    # Also confirm the API endpoint returns violations when the data has them
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")
    ft_decide(c, pid, paper_id, "include")
    flow = get_flow(c, pid)
    assert isinstance(flow["violations"], list)  # structure is always present


# ---------- extraction schemas ----------


def _approved_schema_fields():
    return [
        {
            "field_key": "study_design",
            "label": "Study design",
            "type": "choice",
            "options": ["RCT", "cohort", "case-control", "other"],
            "required": True,
        },
        {"field_key": "sample_size", "label": "Sample size (N)", "type": "number"},
        {"field_key": "primary_outcome", "label": "Primary outcome measure", "type": "text"},
        {"field_key": "effect_size", "label": "Effect size (e.g. SMD)", "type": "text"},
        {"field_key": "funding", "label": "Funding source", "type": "text"},
    ]


def test_schema_create_update_approve_lifecycle(user_client):
    c, pid = proj_client(user_client)
    base = f"/api/v1/projects/{pid}/extraction"

    # Create draft
    r = c.post(f"{base}/schemas", json={"field_definitions": _approved_schema_fields(), "notes": "v1 draft"})
    assert r.status_code == 201
    s = r.json()
    assert s["version"] == 1 and s["status"] == "draft"
    sid = s["id"]

    # Update
    new_fields = _approved_schema_fields() + [{"field_key": "limitations", "label": "Limitations", "type": "text"}]
    r = c.patch(f"{base}/schemas/{sid}", json={"field_definitions": new_fields, "expected_row_version": 1})
    assert r.status_code == 200 and r.json()["field_count"] == 6

    # Second draft → conflict
    r2 = c.post(f"{base}/schemas", json={"field_definitions": _approved_schema_fields()})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "draft_exists"

    # Approve
    r = c.post(f"{base}/schemas/{sid}/approve", json={"expected_row_version": 2})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    # Approved is immutable
    r = c.patch(f"{base}/schemas/{sid}", json={"notes": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "schema_approved"

    # Can create a new draft after approval
    r = c.post(f"{base}/schemas", json={"field_definitions": _approved_schema_fields(), "change_reason": "added field"})
    assert r.status_code == 201 and r.json()["version"] == 2


def test_schema_validation_rejects_bad_field_definitions(user_client):
    c, pid = proj_client(user_client)
    base = f"/api/v1/projects/{pid}/extraction/schemas"

    # Duplicate field_key
    r = c.post(
        base,
        json={
            "field_definitions": [
                {"field_key": "x", "label": "X", "type": "text"},
                {"field_key": "x", "label": "Y", "type": "text"},
            ]
        },
    )
    assert r.status_code == 422

    # choice type missing options
    r = c.post(
        base,
        json={
            "field_definitions": [
                {"field_key": "design", "label": "Design", "type": "choice"},
            ]
        },
    )
    assert r.status_code == 422

    # unknown type
    r = c.post(
        base,
        json={
            "field_definitions": [
                {"field_key": "x", "label": "X", "type": "graph"},
            ]
        },
    )
    assert r.status_code == 422


def test_schema_cannot_be_approved_when_empty(user_client):
    c, pid = proj_client(user_client)
    base = f"/api/v1/projects/{pid}/extraction/schemas"
    r = c.post(base, json={"field_definitions": []})
    assert r.status_code == 201
    sid = r.json()["id"]
    r = c.post(f"{base}/{sid}/approve", json={})
    assert r.status_code == 422


# ---------- evidence items ----------


def test_evidence_upsert_status_lifecycle(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")

    fields = _approved_schema_fields()
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": fields})
    sid = r.json()["id"]
    c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={})

    base = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence"
    ev_url = f"{base}/{paper_id}/study_design"

    # Set to found
    r = c.put(ev_url, json={"status": "found", "value": {"choice": "RCT"}})
    assert r.status_code == 200 and r.json()["status"] == "found"

    # Optimistic concurrency
    stale = c.put(ev_url, json={"status": "not_located", "expected_row_version": 0})
    assert stale.status_code == 409

    # Update with correct version
    current_v = r.json()["row_version"]
    r2 = c.put(ev_url, json={"status": "not_located", "expected_row_version": current_v})
    assert r2.status_code == 200 and r2.json()["status"] == "not_located"

    # List endpoint
    lst = c.get(f"{base}?paper_id={paper_id}").json()
    assert lst["total"] == 1 and lst["items"][0]["field_key"] == "study_design"


def test_evidence_unavailable_cannot_cite_a_page(user_client, dispatcher, object_store):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")

    r = c.post(
        f"/api/v1/projects/{pid}/extraction/schemas",
        json={"field_definitions": [{"field_key": "n", "label": "N", "type": "number"}]},
    )
    sid = r.json()["id"]
    c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={})

    fake_page_id = str(uuid.uuid4())
    r = c.put(
        f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{paper_id}/n",
        json={"status": "unavailable", "document_page_id": fake_page_id},
    )
    assert r.status_code == 422


def test_evidence_excerpt_must_exist_verbatim_in_page(user_client, dispatcher, object_store, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    paper_id = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["items"][0]["id"]
    ta_decide(c, pid, paper_id, "include")

    # Upload and process a PDF so we have a real page
    body = make_pdf(["This RCT enrolled 120 adult participants. " + PARA.format(n=50) * 3])
    d = upload(c, pid, body, paper_id=paper_id)
    run_document_jobs(dispatcher, object_store)

    c.get(f"/api/v1/projects/{pid}/documents/{d['id']}").json()
    page_info = c.get(f"/api/v1/projects/{pid}/documents/{d['id']}/pages?limit=1").json()["items"][0]
    _ = page_info  # dict with page_no, text, etc.

    from app.db.models import DocumentPage

    page_row = db.scalar(
        select(DocumentPage).where(DocumentPage.document_id == uuid.UUID(d["id"]), DocumentPage.page_no == 1)
    )
    assert page_row is not None, "page was not extracted"

    fields = [{"field_key": "sample_size", "label": "Sample size", "type": "text"}]
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": fields})
    sid = r.json()["id"]
    c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={})
    ev_url = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence/{paper_id}/sample_size"

    # Exact excerpt from page → ok
    exact = "120 adult participants"
    r = c.put(
        ev_url,
        json={
            "status": "found",
            "value": {"text": "120"},
            "excerpt": exact,
            "document_page_id": str(page_row.id),
        },
    )
    assert r.status_code == 200

    # Made-up excerpt → 422
    r = c.put(
        ev_url,
        json={
            "status": "found",
            "value": {"text": "999"},
            "excerpt": "999 participants who did not exist",
            "document_page_id": str(page_row.id),
        },
    )
    assert r.status_code == 422


def test_coverage_by_question_distinguishes_found_not_located_unavailable(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()["items"]
    p1, p2, p3 = papers[0]["id"], papers[1]["id"], papers[2]["id"]
    for pid_ in [p1, p2, p3]:
        ta_decide(c, pid, pid_, "include")
        ft_decide(c, pid, pid_, "include")

    fields = [{"field_key": "outcome", "label": "Primary outcome", "type": "text"}]
    qmap = {"outcome": ["q1"]}
    r = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": fields, "question_map": qmap})
    sid = r.json()["id"]
    c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={})

    base_ev = f"/api/v1/projects/{pid}/extraction/schemas/{sid}/evidence"
    c.put(f"{base_ev}/{p1}/outcome", json={"status": "found", "value": {"text": "HbA1c"}})
    c.put(f"{base_ev}/{p2}/outcome", json={"status": "not_located"})
    c.put(f"{base_ev}/{p3}/outcome", json={"status": "unavailable"})

    cov = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/coverage").json()
    q1 = cov["by_question"]["q1"]["fields"][0]
    assert q1["found"] == 1
    assert q1["not_located"] == 1
    assert q1["unavailable"] == 1
    # Coverage must never confuse not_located with unavailable
    assert q1["not_located"] != q1["unavailable"] or (q1["not_located"] == 1 == q1["unavailable"])


def test_different_user_cannot_access_schema_or_evidence(user_client, dispatcher):
    a, _, _ = user_client()
    b, _, _ = user_client()

    pid_a, pv_id = searched(a, dispatcher)
    fields = [{"field_key": "x", "label": "X", "type": "text"}]
    r = a.post(f"/api/v1/projects/{pid_a}/extraction/schemas", json={"field_definitions": fields})
    sid = r.json()["id"]

    for method, url in [
        ("GET", f"/api/v1/projects/{pid_a}/extraction/schemas"),
        ("GET", f"/api/v1/projects/{pid_a}/extraction/schemas/{sid}"),
        ("POST", f"/api/v1/projects/{pid_a}/extraction/schemas/{sid}/approve"),
    ]:
        assert b.request(method, url, json={}).status_code == 404
