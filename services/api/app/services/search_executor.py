"""Scholarly search, worker side. Runs one search_run: each provider query page by page.

Reliability design:
  * ONE TRANSACTION PER PAGE: the page's source records, any new/merged papers and the query's cursor commit
    together. A crash loses at most the page in flight; the retried job resumes from the saved cursor.
  * Idempotent inserts: a source record is unique per (query, provider record id), so a page fetched twice after
    a crash adds nothing the second time.
  * Provider isolation: a provider error ends THAT provider's query (status failed, or partial if some pages
    arrived) and the search moves on. The run is `completed` only if every query completed.
  * Bounded: page size, a per-provider record cap (a query that reports more results is `capped`, never
    `completed`), per-request retries with backoff in the HTTP layer, and a time budget that stops cleanly and
    resumably before the worker's hard time limit.
  * Cooperative cancellation between pages.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Job, PaperRecord, PaperSourceRecord, Project, SearchQuery, SearchRun
from app.jobs import fsm
from app.providers.scholarly import ScholarlySource, registry
from app.providers.scholarly.base import ProviderQuery
from app.providers.scholarly.http import HttpFetcher, ProviderError
from app.providers.scholarly.throttle import LocalThrottle, RedisThrottle, Throttle
from app.repositories import jobs as job_repo
from app.repositories import search as repo
from app.repositories import workflows as wf_repo
from app.search import dedup
from app.search.records import RecordError, ScholarlyRecord, access_status

log = logging.getLogger(__name__)
MAX_SKIPPED_KEPT = 50
MERGE_FIELDS = ("abstract", "year", "venue", "publication_type", "language", "license")
ID_FIELDS = ("doi", "pmid", "pmcid", "arxiv_id", "openalex_id")
UNIQUE_ID_FIELDS = ("doi", "pmid", "arxiv_id", "openalex_id")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Outcome:
    status: str
    summary: dict[str, Any]


def make_fetcher(
    settings: Settings, *, client: httpx.Client | None = None, throttle: Throttle | None = None
) -> HttpFetcher:
    if throttle is None:
        try:
            import redis

            throttle = RedisThrottle(
                redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
            )
        except Exception:  # noqa: BLE001
            throttle = LocalThrottle()
    contact = f" (mailto:{settings.scholarly_contact_email})" if settings.scholarly_contact_email else ""
    client = client or httpx.Client(
        timeout=httpx.Timeout(settings.search_http_timeout_s, connect=min(10.0, settings.search_http_timeout_s)),
        headers={"User-Agent": f"SystematicReviewScientist/0.4 (scholarly search){contact}"},
        follow_redirects=True,
    )
    return HttpFetcher(
        client,
        throttle,
        max_retries=settings.search_max_retries,
        backoff_base_s=settings.search_backoff_base_s,
        max_backoff_s=settings.search_max_backoff_s,
    )


# ---------- dedup + merge ----------


class _Candidate:
    """Adapter so dedup.decide sees a PaperRecord through the same attribute names as a ScholarlyRecord."""

    def __init__(self, p: PaperRecord):
        self.paper = p
        self.id, self.doi, self.pmid, self.arxiv_id, self.openalex_id = p.id, p.doi, p.pmid, p.arxiv_id, p.openalex_id
        self.title_key, self.first_author_key, self.year = p.title_key, p.first_author_key, p.year


def _new_paper(project_id: uuid.UUID, rec: ScholarlyRecord, run_id: uuid.UUID) -> PaperRecord:
    fields = {f: rec.provider for f in ("title", "authors", *MERGE_FIELDS, *ID_FIELDS) if getattr(rec, f)}
    if rec.oa_status != "unknown":
        fields["oa_status"] = rec.provider
    return PaperRecord(
        project_id=project_id,
        title=rec.title,
        title_key=rec.title_key,
        authors=[{"name": a.name, "family": a.family, "orcid": a.orcid} for a in rec.authors],
        first_author_key=(rec.first_author_key or None) and rec.first_author_key[:120],
        abstract=rec.abstract,
        year=rec.year,
        venue=rec.venue,
        publication_type=(rec.publication_type or None) and rec.publication_type[:64],
        keywords=rec.keywords,
        language=(rec.language or None) and rec.language[:8],
        doi=rec.doi,
        pmid=rec.pmid,
        pmcid=rec.pmcid,
        arxiv_id=rec.arxiv_id,
        openalex_id=rec.openalex_id,
        other_ids=dict(rec.other_ids),
        is_preprint=rec.is_preprint,
        has_preprint_version=rec.is_preprint,
        is_retracted=rec.is_retracted,
        oa_status=rec.oa_status,
        oa_url=rec.oa_url,
        pdf_url=rec.pdf_url,
        license=(rec.license or None) and rec.license[:200],
        access_status=rec.access_status,
        field_sources=fields,
        providers=[rec.provider],
        source_count=0,
        first_seen_run_id=run_id,
    )


def _merge(db: Session, p: PaperRecord, rec: ScholarlyRecord) -> None:
    """Fill gaps only; never overwrite a value another source supplied. Identifiers are added only when no other
    paper in the project already holds them (otherwise the two papers are flagged, not silently fused)."""
    fs = dict(p.field_sources or {})
    for f in MERGE_FIELDS:
        if getattr(p, f) in (None, "") and getattr(rec, f):
            value = getattr(rec, f)
            limits = {"publication_type": 64, "language": 8, "license": 200, "venue": 500}
            setattr(p, f, value[: limits[f]] if f in limits and isinstance(value, str) else value)
            fs[f] = rec.provider
    if not p.authors and rec.authors:
        p.authors = [{"name": a.name, "family": a.family, "orcid": a.orcid} for a in rec.authors]
        p.first_author_key = rec.first_author_key
        fs["authors"] = rec.provider
    for f in ID_FIELDS:
        value = getattr(rec, f)
        if value and not getattr(p, f):
            holder = repo.paper_holding(db, p.project_id, f, value) if f in UNIQUE_ID_FIELDS else None
            if holder is None or holder.id == p.id:
                setattr(p, f, value)
                fs[f] = rec.provider
            elif p.possible_duplicate_of_id is None:
                p.possible_duplicate_of_id = holder.id
                p.possible_duplicate_reason = f"Another paper already has {f} {value}; the records may be duplicates."
    if rec.other_ids:
        p.other_ids = {**rec.other_ids, **(p.other_ids or {})}
    if rec.keywords:
        p.keywords = list(dict.fromkeys([*(p.keywords or []), *rec.keywords]))[:50]
    # Published version wins over preprint; remember that a preprint version exists.
    p.has_preprint_version = bool(p.has_preprint_version or rec.is_preprint)
    p.is_preprint = bool(p.is_preprint and rec.is_preprint)
    if rec.is_retracted:
        p.is_retracted = True
        fs["is_retracted"] = rec.provider
    # Open access: keep the best REPORTED access; never downgrade one provider's open report to another's unknown.
    if rec.oa_status == "open" and p.oa_status != "open":
        p.oa_status, p.oa_url, p.pdf_url = "open", rec.oa_url, rec.pdf_url
        fs["oa_status"] = rec.provider
    elif rec.oa_status == "open":
        if not p.pdf_url and rec.pdf_url:
            p.pdf_url = rec.pdf_url
            fs["pdf_url"] = rec.provider
        if not p.oa_url and rec.oa_url:
            p.oa_url = rec.oa_url
    elif rec.oa_status == "closed" and p.oa_status == "unknown":
        p.oa_status = "closed"
        fs["oa_status"] = rec.provider
    p.access_status = access_status(p.oa_status, p.pdf_url, p.oa_url)
    if rec.provider not in (p.providers or []):
        p.providers = [*(p.providers or []), rec.provider]
    p.field_sources = fs
    p.updated_at = _now()
    p.row_version += 1


def _place(
    db: Session, project_id: uuid.UUID, rec: ScholarlyRecord, run_id: uuid.UUID, *, retried: bool = False
) -> tuple[PaperRecord, str]:
    """Find or create the paper for `rec`. Returns (paper, match_basis)."""
    candidates = [_Candidate(p) for p in repo.paper_candidates(db, project_id, rec)]
    decision = dedup.decide(rec, candidates)  # type: ignore[arg-type]
    if decision.match is not None:
        paper: PaperRecord = decision.match.paper  # type: ignore[attr-defined]
        _merge(db, paper, rec)
        return paper, decision.basis
    paper = _new_paper(project_id, rec, run_id)
    if decision.possible_duplicate_of is not None:
        paper.possible_duplicate_of_id = decision.possible_duplicate_of.paper.id  # type: ignore[attr-defined]
        paper.possible_duplicate_reason = decision.reason
    try:
        with db.begin_nested():
            db.add(paper)
            db.flush()
    except IntegrityError:
        # A paper with one of these identifiers appeared after the candidate lookup (only possible if two writers
        # touched the project at once, which the one-active-search index prevents). Decide again, once.
        if retried:
            raise
        return _place(db, project_id, rec, run_id, retried=True)
    return paper, "new"


# ---------- execution ----------


class _Stop(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def _skip(query: SearchQuery, provider_record_id: str | None, reason: str) -> None:
    query.records_skipped += 1
    if len(query.skipped) < MAX_SKIPPED_KEPT:
        query.skipped = [*query.skipped, {"provider_record_id": provider_record_id, "reason": reason[:200]}]


def _ingest_page(
    db: Session, run: SearchRun, query: SearchQuery, source: ScholarlySource, records: list[dict[str, Any]]
) -> int:
    added = 0
    base_rank = query.records_retrieved + query.records_skipped
    for i, raw in enumerate(records, start=1):
        try:
            rid = source.record_id(raw)
        except RecordError as e:
            _skip(query, None, str(e))
            continue
        if repo.source_exists(db, query.id, rid):
            continue  # this page was already stored before a crash/redelivery
        try:
            rec = source.normalize(raw)
        except RecordError as e:
            _skip(query, rid, str(e))
            continue
        paper, basis = _place(db, run.project_id, rec, run.id)
        db.add(
            PaperSourceRecord(
                paper_id=paper.id,
                project_id=run.project_id,
                search_run_id=run.id,
                search_query_id=query.id,
                provider=source.key,
                provider_record_id=rid[:255],
                rank=base_rank + i,
                source_url=rec.source_url,
                raw=raw,
                normalized=rec.as_dict(),
                match_basis=basis,
            )
        )
        paper.source_count += 1
        db.flush()
        added += 1
    return added


def _run_query(
    db: Session,
    run: SearchRun,
    query: SearchQuery,
    source: ScholarlySource,
    http: HttpFetcher,
    settings: Settings,
    job: Job,
    deadline: float,
    report: Callable[[str], None],
) -> None:
    cap = max(1, min(settings.search_max_records_per_provider, 10_000))
    page_size = max(1, min(settings.search_page_size, source.max_page_size))
    pq = ProviderQuery(source.key, query.endpoint, query.query_text, dict(query.params))
    query.status = "running"
    query.started_at = query.started_at or _now()
    db.commit()
    while True:
        db.refresh(job)
        if job.cancel_requested:
            raise _Stop("cancelled")
        if time.monotonic() > deadline:
            raise _Stop("time_budget")
        remaining = cap - query.records_retrieved
        if remaining <= 0:
            query.status = "capped" if (query.total_reported or 0) > query.records_retrieved else "completed"
            break
        before = http.requests_made
        try:
            page = source.fetch_page(http, pq, query.cursor, min(page_size, remaining))
        except ProviderError as e:
            query.request_count += http.requests_made - before
            query.status = "partial" if query.records_retrieved else "failed"
            query.error_kind, query.error_summary = e.kind, e.summary()
            log.warning("provider query failed", extra={"provider": source.key, "kind": e.kind})
            break
        query.request_count += http.requests_made - before
        query.total_reported = page.total if page.total is not None else query.total_reported
        added = _ingest_page(db, run, query, source, page.records[:remaining])
        query.records_retrieved += added
        query.pages_fetched += 1
        query.retrieved_at = _now()
        query.cursor = page.next_cursor
        report(f"{source.label}: {query.records_retrieved} records")
        if page.next_cursor is None or not page.records:
            query.status = "completed"
            break
        db.commit()  # page boundary: records + cursor are durable together
    query.finished_at = _now()
    db.commit()


def _finalise(db: Session, run: SearchRun, stop: str | None) -> None:
    statuses = [x.status for x in run.queries]
    counts = repo.run_counts(db, run.id)
    run.records_retrieved = counts["records_retrieved"]
    run.records_skipped = sum(x.records_skipped for x in run.queries)
    run.papers_new = counts["papers_new"]
    run.papers_matched_existing = counts["papers_matched_existing"]
    run.duplicates_in_run = counts["duplicates_in_run"]
    run.possible_duplicates = counts["possible_duplicates"]
    if stop == "cancelled":
        run.status = "cancelled"
    elif statuses and all(s == "completed" for s in statuses):
        run.status = "completed"
    elif statuses and all(s == "failed" for s in statuses):
        run.status = "failed"
    else:
        run.status = "partial"
    run.stop_reason = stop
    problems = [f"{x.provider}: {x.error_summary}" for x in run.queries if x.error_summary]
    capped = [x.provider for x in run.queries if x.status == "capped"]
    notes = []
    if problems:
        notes.append("; ".join(problems))
    if capped:
        notes.append(
            f"Stopped at the record limit for {', '.join(capped)}: the provider reported more results than were "
            "retrieved. Narrow the query, or raise SEARCH_MAX_RECORDS_PER_PROVIDER."
        )
    if stop == "time_budget":
        notes.append("The search reached its time budget for one job. Continue it to resume where it stopped.")
    run.error_summary = " ".join(notes) or None
    run.finished_at = _now()
    run.updated_at = _now()
    run.row_version += 1


def execute(db: Session, job: Job, settings: Settings, *, http: HttpFetcher | None = None) -> Outcome:
    """Run (or resume) the search named in job.input. The caller has claimed the job; this function finishes it."""
    from app.services import audit_service  # local: keeps the worker import graph small

    run_id = uuid.UUID(str((job.input or {}).get("search_run_id")))
    run = repo.get_run_by_id(db, run_id)
    if run is None:
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="search run no longer exists")
        db.commit()
        return Outcome("failed", {})
    if run.status not in ("queued", "running"):
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.SUCCEEDED, result={"search_run_id": str(run.id), "noop": True})
        db.commit()
        return Outcome(run.status, {})
    reg = registry(settings)
    http = http or make_fetcher(settings)
    run.status = "running"
    run.started_at = run.started_at or _now()
    run.row_version += 1
    db.commit()

    deadline = time.monotonic() + settings.search_job_budget_s
    todo = [x for x in run.queries if x.status in ("pending", "running")]
    done_before = len(run.queries) - len(todo)

    def report(msg: str, i: int = 0) -> None:
        pct = int(95 * (done_before + i) / max(len(run.queries), 1))
        job_repo.set_progress(db, job.id, min(pct, 95), msg[:200])

    stop: str | None = None
    for i, query in enumerate(todo):
        source = reg.get(query.provider)
        if source is None:
            query.status, query.error_kind = "failed", "disabled"
            query.error_summary = f"{query.provider} is not enabled on this server (SEARCH_PROVIDERS_ENABLED)."
            query.finished_at = _now()
            db.commit()
            continue

        def report_query(msg: str, i: int = i) -> None:
            report(msg, i)

        try:
            _run_query(db, run, query, source, http, settings, job, deadline, report_query)
        except _Stop as s:
            stop = s.reason
            query.status = "cancelled" if s.reason == "cancelled" else "partial"
            if s.reason == "time_budget":
                query.status = "pending"  # resumable from its cursor
            query.finished_at = _now() if s.reason == "cancelled" else None
            db.commit()
            if s.reason == "cancelled":
                for rest in todo[i + 1 :]:
                    rest.status, rest.finished_at = "cancelled", _now()
            break

    _finalise(db, run, stop)
    project = db.get(Project, run.project_id)
    summary = {
        "search_run_id": str(run.id),
        "status": run.status,
        "records_retrieved": run.records_retrieved,
        "papers_new": run.papers_new,
        "duplicates_in_run": run.duplicates_in_run,
        "queries": {x.provider: x.status for x in run.queries},
    }
    wf = wf_repo.for_project(db, run.project_id)
    if wf is not None:
        wf_repo.add_event(db, wf.id, "search", stage="SEARCH", payload={"action": "finished", **summary})
    if project is not None:
        audit_service.record(
            db,
            action="search.finished",
            entity_type="search_run",
            entity_id=run.id,
            actor_id=None,
            project_id=project.id,
            after={k: v for k, v in summary.items() if k != "search_run_id"},
        )
    target = fsm.CANCELLED if run.status == "cancelled" else fsm.SUCCEEDED
    job_repo.finish(db, job.id, fsm.RUNNING, target, result=summary)
    db.commit()
    return Outcome(run.status, summary)
