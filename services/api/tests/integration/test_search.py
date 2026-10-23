"""Scholarly search on real PostgreSQL: plan, run, normalise, deduplicate, provenance, partial failure, resume,
caps, broaden gate, cancellation, stale protocol, worker loss and cascade. Providers are mocked at the HTTP layer
(tests/fixtures/scholarly.py); everything else is the real code path."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.models import Job, PaperRecord, PaperSourceRecord, SearchQuery, SearchRun, WorkflowEvent
from app.services import job_service
from tests.fixtures import scholarly as fx
from tests.fixtures.scholarly import ScholarlyTransport

from .support import OPEN_ACCESS, approved, run_search_jobs, searched, start_search
from .test_protocol_workflow import answer, project, started


def summary(c, pid):
    return c.get(f"/api/v1/projects/{pid}/search/summary").json()


def run_of(c, pid, rid):
    return c.get(f"/api/v1/projects/{pid}/search-runs/{rid}").json()


def by_provider(run):
    return {q["provider"]: q for q in run["queries"]}


# ---------- plan ----------


def test_plan_is_built_from_the_approved_protocol(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    plan = c.post(f"/api/v1/projects/{pid}/search/plan", json={}).json()
    assert plan["providers"] == ["openalex", "europepmc"]  # the protocol's listed sources
    assert plan["protocol_sources"]["matched"] == ["openalex", "europepmc"]
    assert plan["expression_source"] == "framework_elements"
    assert "aerobic exercise" in plan["expression"] and "usual care" not in plan["expression"]
    oa = {q["provider"]: q for q in plan["queries"]}["openalex"]
    assert oa["filters_applied"] == {"date_from": 2000, "date_to": 2025, "languages": ["en"]}
    assert {p["key"] for p in plan["catalog"]} == {"openalex", "europepmc", "arxiv", "crossref"}

    edited = c.post(
        f"/api/v1/projects/{pid}/search/plan",
        json={"expression": "yoga AND anxiety", "providers": ["openalex", "arxiv"]},
    ).json()
    assert edited["expression_source"] == "user_edited" and edited["expression"] == "yoga AND anxiety"
    assert edited["protocol_sources"]["added_beyond_protocol"] == ["arxiv"]
    assert any("in addition to the sources" in x for x in edited["limitations"])

    bad = c.post(f"/api/v1/projects/{pid}/search/plan", json={"expression": "(yoga"})
    assert bad.status_code == 422 and "parenthesis" in bad.json()["error"]["message"]
    wrong = c.post(f"/api/v1/projects/{pid}/search/plan", json={"providers": ["crossref"]})
    assert wrong.status_code == 422  # crossref is only for broadened discovery
    unknown = c.post(f"/api/v1/projects/{pid}/search/plan", json={"providers": ["scopus"]})
    assert unknown.status_code == 422


def test_search_needs_an_approved_protocol(user_client, dispatcher):
    c, _, _ = user_client()
    pid = project(c)
    started(c, dispatcher, pid)  # draft only
    r = c.post(f"/api/v1/projects/{pid}/search-runs", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "protocol_not_approved"
    assert c.post(f"/api/v1/projects/{pid}/search/plan", json={}).status_code == 409


# ---------- the main path ----------


def test_search_retrieves_normalises_deduplicates_and_keeps_provenance(user_client, dispatcher, db):
    c, _, _ = user_client()
    transport = ScholarlyTransport()
    pid, run = searched(c, dispatcher, transport)
    assert run["status"] == "completed" and run["is_current_protocol"]
    assert (run["records_retrieved"], run["papers_new"], run["duplicates_in_run"]) == (6, 4, 2)
    q = by_provider(run)
    assert q["openalex"]["pages_fetched"] == 2 and q["openalex"]["total_reported"] == 3
    assert q["europepmc"]["query_text"].endswith('(LANG:"eng")') and q["europepmc"]["retrieved_at"]
    assert all(x["status"] == "completed" for x in run["queries"])
    assert run["protocol_sources"]["added_beyond_protocol"] == ["arxiv"]
    assert transport.count("api.openalex.org") == 2

    s = summary(c, pid)["current_protocol"]
    assert (s["records_identified"], s["unique_papers"], s["duplicates_removed"]) == (6, 4, 2)
    assert s["records_by_provider"] == {"openalex": 3, "europepmc": 2, "arxiv": 1}

    papers = c.get(f"/api/v1/projects/{pid}/papers?limit=100").json()
    assert papers["total"] == 4
    w1 = next(p for p in papers["items"] if p["doi"] == fx.DOI_W1)
    assert sorted(w1["providers"]) == ["europepmc", "openalex"] and w1["source_count"] == 2
    assert w1["pmid"] == "31111111" and w1["access_status"] == "open_pdf_link"
    assert w1["full_text_status"] == "not_obtained"  # a link is not a full text

    detail = c.get(f"/api/v1/projects/{pid}/papers/{w1['id']}").json()
    bases = {s["provider"]: s["match_basis"] for s in detail["sources"]}
    assert bases == {"openalex": "new", "europepmc": "doi"}
    epmc = next(s for s in detail["sources"] if s["provider"] == "europepmc")
    assert epmc["raw"]["source"] == "MED" and epmc["provider_record_id"] == "MED:31111111"
    assert epmc["query_text"] and epmc["normalized"]["doi"] == fx.DOI_W1
    assert detail["field_sources"]["title"] == "openalex"

    w3 = next(p for p in papers["items"] if p["arxiv_id"] == "2101.00001")
    assert sorted(w3["providers"]) == ["arxiv", "openalex"] and w3["is_preprint"]

    assert c.get(f"/api/v1/projects/{pid}/papers?provider=europepmc").json()["total"] == 2
    assert c.get(f"/api/v1/projects/{pid}/papers?access=metadata_only").json()["total"] == 1
    assert c.get(f"/api/v1/projects/{pid}/papers?q=running").json()["total"] == 1
    assert c.get(f"/api/v1/projects/{pid}/papers?access=bogus").status_code == 422

    stored = db.scalars(select(SearchQuery.params).where(SearchQuery.search_run_id == uuid.UUID(run["id"]))).all()
    assert all("mailto" not in p and "api_key" not in p for p in stored)
    assert c.get(f"/api/v1/projects/{pid}").json()["status"] == "searching"
    events = db.scalars(
        select(WorkflowEvent).where(WorkflowEvent.kind == "search").order_by(WorkflowEvent.created_at, WorkflowEvent.id)
    )
    actions = [e.payload.get("action") for e in events if e.payload.get("search_run_id") == run["id"]]
    assert actions == ["started", "finished"]


def test_running_the_same_job_twice_adds_nothing(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, run = searched(c, dispatcher)
    job_id = uuid.UUID(run["job_id"])
    db.execute(text("UPDATE jobs SET status='queued' WHERE id=:id"), {"id": job_id})  # a redelivered message
    db.commit()
    dispatcher.sent.append(("search.run", str(job_id)))
    assert run_search_jobs(dispatcher) == ["completed"]  # no-op: the run is already finished
    n = db.scalar(
        select(func.count())
        .select_from(PaperSourceRecord)
        .where(PaperSourceRecord.search_run_id == uuid.UUID(run["id"]))
    )
    assert n == 6


# ---------- partial failure, resume, caps ----------


def test_one_provider_failing_keeps_the_others_and_the_run_is_not_complete(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    rid = start_search(c, pid, providers=OPEN_ACCESS)["id"]
    assert run_search_jobs(dispatcher, ScholarlyTransport(fail={"www.ebi.ac.uk": 503})) == ["partial"]
    run = run_of(c, pid, rid)
    q = by_provider(run)
    assert q["europepmc"]["status"] == "failed" and q["europepmc"]["error_kind"] == "server_error"
    assert q["openalex"]["status"] == "completed" and q["arxiv"]["status"] == "completed"
    assert run["status"] == "partial" and "europepmc" in run["error_summary"]
    assert run["records_retrieved"] == 4  # OpenAlex 3 + arXiv 1 kept

    r = c.post(f"/api/v1/projects/{pid}/search-runs/{rid}/retry")
    assert r.status_code == 202 and r.json()["attempt_count"] == 2
    assert run_search_jobs(dispatcher) == ["completed"]
    run = run_of(c, pid, rid)
    assert run["status"] == "completed" and run["records_retrieved"] == 6 and run["papers_new"] == 4
    nothing = c.post(f"/api/v1/projects/{pid}/search-runs/{rid}/retry")
    assert nothing.status_code == 409


def test_failure_between_pages_resumes_from_the_saved_cursor_without_duplicates(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    rid = start_search(c, pid, providers=["openalex"])["id"]
    t = ScholarlyTransport(fail_after={"api.openalex.org": 1})
    assert run_search_jobs(dispatcher, t) == ["partial"]
    q = by_provider(run_of(c, pid, rid))["openalex"]
    assert (q["status"], q["records_retrieved"], q["pages_fetched"]) == ("partial", 2, 1)

    c.post(f"/api/v1/projects/{pid}/search-runs/{rid}/retry")
    healthy = ScholarlyTransport()
    assert run_search_jobs(dispatcher, healthy) == ["completed"]
    q = by_provider(run_of(c, pid, rid))["openalex"]
    assert (q["records_retrieved"], q["pages_fetched"]) == (3, 2)
    assert [r.url.params["cursor"] for r in healthy.requests] == ["cursor-2"]  # resumed, not restarted
    assert c.get(f"/api/v1/projects/{pid}/papers").json()["total"] == 3


def test_record_cap_marks_the_query_capped_never_complete(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    rid = start_search(c, pid, providers=["openalex"])["id"]
    capped = get_settings().model_copy(update={"search_max_records_per_provider": 2})
    assert run_search_jobs(dispatcher, settings=capped) == ["partial"]
    run = run_of(c, pid, rid)
    q = by_provider(run)["openalex"]
    assert q["status"] == "capped" and q["records_retrieved"] == 2 and q["total_reported"] == 3
    assert "record limit" in run["error_summary"]


def test_malformed_provider_records_are_skipped_and_counted(user_client, dispatcher, monkeypatch):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    rid = start_search(c, pid, providers=["openalex"])["id"]
    broken = {
        "meta": {"count": 2, "next_cursor": None},
        "results": [{"id": "https://openalex.org/W77", "title": None}, fx.OPENALEX_PAGE_2["results"][0]],
    }
    monkeypatch.setattr(fx, "OPENALEX_PAGE_1", broken)
    assert run_search_jobs(dispatcher) == ["completed"]
    q = by_provider(run_of(c, pid, rid))["openalex"]
    assert q["records_retrieved"] == 1 and q["records_skipped"] == 1
    assert q["skipped"][0] == {"provider_record_id": "W77", "reason": "record has no title"}


# ---------- broaden, concurrency, cancel ----------


def test_additional_sources_need_a_yes_to_broadening(user_client, dispatcher):
    c, _, _ = user_client()
    pid, run = searched(c, dispatcher)
    r = c.post(f"/api/v1/projects/{pid}/search-runs", json={"tier": "additional", "providers": ["crossref"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "broaden_not_confirmed"
    assert summary(c, pid)["broaden_prompt"] == {"search_run_id": run["id"], "decision": None}

    assert (
        c.post(f"/api/v1/projects/{pid}/search-runs/{run['id']}/broaden", json={"decision": "later"}).status_code == 200
    )
    assert c.post(f"/api/v1/projects/{pid}/search-runs", json={"tier": "additional"}).status_code == 409
    assert (
        c.post(f"/api/v1/projects/{pid}/search-runs/{run['id']}/broaden", json={"decision": "maybe"}).status_code == 422
    )
    assert (
        c.post(f"/api/v1/projects/{pid}/search-runs/{run['id']}/broaden", json={"decision": "yes"}).status_code == 200
    )

    extra = start_search(c, pid, tier="additional", providers=["crossref"])
    assert run_search_jobs(dispatcher) == ["completed"]
    extra = run_of(c, pid, extra["id"])
    assert extra["tier"] == "additional" and (extra["papers_new"], extra["papers_matched_existing"]) == (1, 1)
    assert any("Boolean" in x for x in by_provider(extra)["crossref"]["limitations"])
    s = summary(c, pid)["all_runs"]
    assert s["records_by_tier"] == {"open_access": 6, "additional": 2} and s["unique_papers"] == 5
    wrong_tier = c.post(f"/api/v1/projects/{pid}/search-runs/{extra['id']}/broaden", json={"decision": "no"})
    assert wrong_tier.status_code == 409


def test_only_one_search_at_a_time_and_a_queued_search_can_be_cancelled(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    first = start_search(c, pid, providers=["openalex"])
    r = c.post(f"/api/v1/projects/{pid}/search-runs", json={"providers": ["openalex"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "search_active"

    cancelled = c.post(f"/api/v1/projects/{pid}/search-runs/{first['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and cancelled["queries"][0]["status"] == "cancelled"
    assert db.get(Job, uuid.UUID(first["job_id"])).status == "cancelled"
    assert run_search_jobs(dispatcher) == ["ignored"]  # the cancelled job's message is a no-op
    assert c.post(f"/api/v1/projects/{pid}/search-runs/{first['id']}/cancel").status_code == 409

    assert c.post(f"/api/v1/projects/{pid}/search-runs/{first['id']}/retry").status_code == 202
    assert run_search_jobs(dispatcher) == ["completed"]


def test_a_running_search_stops_between_pages_when_cancelled(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    run = start_search(c, pid, providers=["openalex", "europepmc"])
    db.execute(text("UPDATE jobs SET cancel_requested = true WHERE id = :id"), {"id": uuid.UUID(run["job_id"])})
    db.commit()
    assert run_search_jobs(dispatcher) == ["cancelled"]
    got = run_of(c, pid, run["id"])
    assert got["status"] == "cancelled" and {q["status"] for q in got["queries"]} == {"cancelled"}


# ---------- protocol changes, worker loss, rate limit, deletion ----------


def test_a_search_from_a_superseded_protocol_cannot_be_continued(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    rid = start_search(c, pid, providers=OPEN_ACCESS)["id"]
    run_search_jobs(dispatcher, ScholarlyTransport(fail={"www.ebi.ac.uk": 503}))

    from .conftest import drain

    assert c.post(f"/api/v1/projects/{pid}/protocol/revisions", json={"reason": "Widen dates"}).status_code == 202
    drain(dispatcher)
    busy = c.post(f"/api/v1/projects/{pid}/search-runs", json={})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "workflow_busy"
    answer(c, dispatcher, pid, "approve")

    run = run_of(c, pid, rid)
    assert run["is_current_protocol"] is False
    r = c.post(f"/api/v1/projects/{pid}/search-runs/{rid}/retry")
    assert r.status_code == 409 and r.json()["error"]["code"] == "protocol_changed"
    assert summary(c, pid)["stale_runs"] == 1


def test_the_sweep_fails_a_search_whose_worker_was_lost(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    run = start_search(c, pid, providers=["openalex"])
    jid = uuid.UUID(run["job_id"])
    old = datetime.now(UTC) - timedelta(hours=1)
    db.execute(
        text("UPDATE jobs SET status='running', attempts=max_attempts, heartbeat_at=:t WHERE id=:id"),
        {"t": old, "id": jid},
    )
    db.execute(text("UPDATE search_runs SET status='running' WHERE id=:id"), {"id": uuid.UUID(run["id"])})
    db.commit()
    job_service.sweep(db, get_settings(), lambda *_: None)
    got = run_of(c, pid, run["id"])
    assert got["status"] == "failed" and got["stop_reason"] == "worker_lost"
    assert c.post(f"/api/v1/projects/{pid}/search-runs/{run['id']}/retry").status_code == 202


def test_starting_searches_is_rate_limited(user_client, dispatcher, app):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"rate_limit_search_per_hour": 1})
    first = start_search(c, pid, providers=["openalex"])
    c.post(f"/api/v1/projects/{pid}/search-runs/{first['id']}/cancel")
    r = c.post(f"/api/v1/projects/{pid}/search-runs", json={"providers": ["openalex"]})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_deleting_the_project_removes_its_search_data(user_client, dispatcher, db):
    c, _, _ = user_client()
    pid, _ = searched(c, dispatcher)
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204
    p = uuid.UUID(pid)
    for model in (SearchRun, SearchQuery, PaperRecord, PaperSourceRecord):
        assert db.scalar(select(func.count()).select_from(model).where(model.project_id == p)) == 0
