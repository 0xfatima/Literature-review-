"""Scholarly search, API side: build the plan from the APPROVED protocol, start / retry / cancel searches, record the
"broaden discovery?" decision, and present runs. Provider calls never happen here (they run in the worker:
app.services.search_executor).

Rules enforced here:
  * a search is built only from the currently approved protocol version, and records that version's id;
  * no search while a protocol revision is in progress, and only one search per project at a time;
  * every provider the user adds beyond the protocol's listed sources is recorded on the run as an addition
    (the approved protocol itself is never changed by a search);
  * the additional tier (sources that mostly hold metadata for subscription content) runs only after the user
    answered "yes" to broadening discovery;
  * an edited expression is stored next to the expression the protocol produced, with source `user_edited`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, RateLimited, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import SEARCH_RUN_ACTIVE, Job, Project, ProtocolVersion, SearchQuery, SearchRun, User
from app.jobs import fsm
from app.protocol import stages
from app.providers.scholarly import ScholarlySource, match_protocol_sources, registry
from app.providers.scholarly.http import public_url
from app.repositories import search as repo
from app.repositories import workflows as wf_repo
from app.search import derive
from app.search import query as q
from app.services import audit_service, job_service, protocol_service, workflow_service
from app.services.job_service import Dispatcher

JOB_TYPE = "search.run"
RETRYABLE_QUERY_STATUSES = ("pending", "running", "failed", "partial", "cancelled")
TIER_LABELS = {
    "open_access": "Open-access-first sources",
    "additional": "Additional sources (broadened discovery)",
}


def _now() -> datetime:
    return datetime.now(UTC)


def providers(settings: Settings) -> dict[str, ScholarlySource]:
    return registry(settings)


# ---------- preconditions ----------


def _approved_or_fail(db: Session, project: Project) -> ProtocolVersion:
    approved = protocol_service.approved(db, project)
    if approved is None:
        raise Conflict("Approve the review protocol before searching.", code="protocol_not_approved")
    return approved


def _check_can_search(db: Session, project: Project) -> ProtocolVersion:
    approved = _approved_or_fail(db, project)
    run = workflow_service.get(db, project)
    # A revision moves the workflow back to PROTOCOL_REVIEW, so report it before the stage check.
    if protocol_service.draft(db, project) is not None or (run is not None and run.status != "idle"):
        raise Conflict(
            "A protocol revision or workflow step is in progress. Finish it before searching.",
            code="workflow_busy",
        )
    if run is None or stages.index(run.stage) < stages.index("SEARCH"):
        raise Conflict("The workflow has not reached the search stage.", code="not_at_search_stage")
    return approved


# ---------- plan ----------


def _parse(text: str) -> q.Node:
    try:
        return q.parse(text)
    except q.QueryError as e:
        raise ValidationFailed(
            str(e), details=[{"field": "expression", "issue": "invalid", "message": str(e)}]
        ) from None


def _default_providers(
    tier: str, reg: dict[str, ScholarlySource], matched: list[str], used_before: set[str]
) -> list[str]:
    if tier == "open_access":
        chosen = [k for k in matched if reg[k].tier == "open_access"]
        return chosen or [k for k, p in reg.items() if p.tier == "open_access" and k != "arxiv"]
    extra = [k for k, p in reg.items() if p.tier == "additional"]
    return extra + [k for k in matched if k not in used_before and k not in extra and k in reg]


def _general_limitations(fp: derive.FilterPlan, unsupported: list[str], added: list[str]) -> list[str]:
    out: list[str] = []
    if fp.unrecognised_languages:
        out.append(
            "These protocol languages are not recognised as language filters and were not applied: "
            + ", ".join(fp.unrecognised_languages)
            + "."
        )
    if fp.study_designs:
        out.append("Study designs are not used as search filters; they are applied when records are screened.")
    if unsupported:
        out.append(
            "These sources listed in the protocol have no supported search adapter and are NOT searched: "
            + ", ".join(unsupported)
            + ". Search them separately and record it, or revise the protocol."
        )
    if added:
        out.append("Searched in addition to the sources listed in the approved protocol: " + ", ".join(added) + ".")
    out.append(
        "Open-access status and full-text links are reported by the providers and have not been verified. No full "
        "text has been obtained or read by this application."
    )
    return out


def build_plan(
    db: Session,
    settings: Settings,
    project: Project,
    *,
    tier: str = "open_access",
    expression: str | None = None,
    provider_keys: list[str] | None = None,
) -> dict[str, Any]:
    """A preview of exactly what a search would send. Read-only; also the basis of `start`."""
    if tier not in TIER_LABELS:
        raise ValidationFailed("Unknown search tier.", details=[{"field": "tier", "issue": "invalid"}])
    approved = _approved_or_fail(db, project)
    content = protocol_service.to_content(approved)
    derived = derive.derive(content, project.topic_original)
    fp = derive.filter_plan(content)
    reg = providers(settings)
    matched, unsupported = match_protocol_sources(list(content.get("sources") or []), reg)
    used_before = {p for r in repo.list_runs(db, project.id, 200) for p in r.providers}
    selected = provider_keys if provider_keys is not None else _default_providers(tier, reg, matched, used_before)
    unknown = [k for k in selected if k not in reg]
    if unknown:
        raise ValidationFailed(
            "Unknown or disabled search provider.",
            details=[{"field": "providers", "issue": "unknown", "values": unknown, "allowed": list(reg)}],
        )
    wrong_tier = [k for k in selected if tier == "open_access" and reg[k].tier != "open_access"]
    if wrong_tier:
        raise ValidationFailed(
            "These sources are only searched when you choose to broaden discovery.",
            details=[{"field": "providers", "issue": "additional_tier_only", "values": wrong_tier}],
        )
    edited = expression is not None and expression.strip() != derived.expression.strip()
    text = (expression if expression is not None else derived.expression).strip()
    node = _parse(text) if text else None
    added = [k for k in selected if k not in matched]
    queries: list[dict[str, Any]] = []
    for k in selected:
        if node is None:
            break
        pq = reg[k].build_query(node, fp)
        queries.append(
            {
                "provider": k,
                "provider_label": reg[k].label,
                "query_text": pq.query_text,
                "endpoint": pq.endpoint,
                "params": pq.params,
                "url": public_url(pq.endpoint, pq.params),
                "filters_applied": pq.filters_applied,
                "limitations": pq.limitations,
            }
        )
    return {
        "protocol_version_id": approved.id,
        "protocol_version": approved.version,
        "tier": tier,
        "expression": q.to_text(node) if node is not None else "",
        "expression_source": "user_edited" if edited else derived.source,
        "derived_expression": derived.expression,
        "derivation_notes": [n for n in derived.explanation if n],
        "filters": fp.as_dict(),
        "providers": selected,
        "protocol_sources": {
            "listed": list(content.get("sources") or []),
            "matched": matched,
            "unsupported": unsupported,
            "added_beyond_protocol": added,
        },
        "limitations": _general_limitations(fp, unsupported, added),
        "queries": queries,
        "ready": node is not None and bool(selected),
        "max_records_per_provider": settings.search_max_records_per_provider,
    }


def provider_catalog(settings: Settings) -> list[dict[str, Any]]:
    return [
        {
            "key": k,
            "label": p.label,
            "tier": p.tier,
            "description": p.description,
            "attribution": p.attribution,
            "terms_url": p.terms_url,
            "aliases": list(p.aliases),
        }
        for k, p in providers(settings).items()
    ]


# ---------- start ----------


def _record_event(db: Session, project: Project, user: User | None, payload: dict[str, Any]) -> None:
    run = workflow_service.get(db, project)
    if run is not None:
        wf_repo.add_event(db, run.id, "search", stage="SEARCH", actor_id=user.id if user else None, payload=payload)


def start(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    *,
    tier: str,
    expression: str | None,
    provider_keys: list[str] | None,
) -> SearchRun:
    protocol_service.require_writable(project)
    approved = _check_can_search(db, project)
    if repo.active_run(db, project.id) is not None:
        raise Conflict("A search is already running for this project.", code="search_active")
    if tier == "additional":
        last = repo.latest_open_access_run(db, project.id)
        if last is None or last.broaden_decision != "yes":
            raise Conflict(
                "Run an open-access-first search and choose to broaden discovery before using additional sources.",
                code="broaden_not_confirmed",
            )
    plan = build_plan(db, settings, project, tier=tier, expression=expression, provider_keys=provider_keys)
    if not plan["ready"]:
        raise ValidationFailed(
            "Write a search expression and choose at least one source.",
            details=[{"field": "expression", "issue": "required"}],
        )
    allowed, retry = limiter.hit(f"search:{user.id}", settings.rate_limit_search_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many searches started in the last hour. Try again later.")
    run = SearchRun(
        project_id=project.id,
        protocol_version_id=approved.id,
        tier=tier,
        status="queued",
        expression=plan["expression"],
        expression_source=plan["expression_source"] if plan["expression_source"] != "none" else "user_edited",
        derived_expression=plan["derived_expression"] or None,
        derivation_notes=plan["derivation_notes"],
        filters=plan["filters"],
        providers=plan["providers"],
        protocol_sources=plan["protocol_sources"],
        limitations=plan["limitations"],
        created_by=user.id,
    )
    for i, pq in enumerate(plan["queries"]):
        run.queries.append(
            SearchQuery(
                project_id=project.id,
                provider=pq["provider"],
                position=i,
                endpoint=pq["endpoint"],
                query_text=pq["query_text"],
                params=pq["params"],
                filters_applied=pq["filters_applied"],
                limitations=pq["limitations"],
                status="pending",
            )
        )
    try:
        with db.begin_nested():
            db.add(run)
            db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("A search is already running for this project.", code="search_active") from None
    if project.status in ("setup", "needs_attention"):
        project.status = "searching"
        project.row_version += 1
    _record_event(
        db,
        project,
        user,
        {"action": "started", "search_run_id": str(run.id), "tier": tier, "providers": run.providers},
    )
    audit_service.record(
        db,
        action="search.started",
        entity_type="search_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={
            "tier": tier,
            "providers": run.providers,
            "protocol_version": approved.version,
            "expression_source": run.expression_source,
            "added_beyond_protocol": plan["protocol_sources"]["added_beyond_protocol"],
        },
    )
    _enqueue(db, settings, dispatch, user, run)
    return run


def _enqueue(db: Session, settings: Settings, dispatch: Dispatcher, user: User, run: SearchRun) -> None:
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=run.project_id,
        payload={"search_run_id": str(run.id), "attempt": run.attempt_count},
        request_key=None,
        rerun_of=run.job_id,
    )  # commits the run together with the job row, then dispatches
    run.job_id = job.id
    db.commit()
    db.refresh(run)


# ---------- retry / cancel ----------


def require_run(db: Session, project: Project, run_id: uuid.UUID, *, lock: bool = False) -> SearchRun:
    run = repo.get_run(db, project.id, run_id, lock=lock)
    if run is None:
        raise NotFound("Search run not found.")
    return run


def retry(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project, run_id: uuid.UUID
) -> SearchRun:
    """Continue the providers that failed, stopped early or were cancelled. Completed and capped provider queries
    are kept as they are; resumed queries continue from their saved cursor (no duplicate records)."""
    run = require_run(db, project, run_id, lock=True)  # 404 before anything else: run ids are per project
    protocol_service.require_writable(project)
    approved = _check_can_search(db, project)
    if run.status not in ("partial", "failed", "cancelled"):
        raise Conflict(
            f"This search is {run.status}; only an incomplete search can be continued.", code="not_retryable"
        )
    if run.protocol_version_id != approved.id:
        raise Conflict(
            "The protocol has been revised since this search. Start a new search from the current protocol.",
            code="protocol_changed",
        )
    if repo.active_run(db, project.id) is not None:
        raise Conflict("A search is already running for this project.", code="search_active")
    todo = [x for x in run.queries if x.status in RETRYABLE_QUERY_STATUSES]
    if not todo:
        raise Conflict("Every source in this search already finished.", code="nothing_to_retry")
    for x in todo:
        x.status, x.error_kind, x.error_summary, x.finished_at = "pending", None, None, None
    run.status, run.stop_reason, run.error_summary, run.finished_at = "queued", None, None, None
    run.attempt_count += 1
    run.row_version += 1
    run.updated_at = _now()
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("A search is already running for this project.", code="search_active") from None
    _record_event(
        db, project, user, {"action": "retried", "search_run_id": str(run.id), "providers": [x.provider for x in todo]}
    )
    audit_service.record(
        db,
        action="search.retried",
        entity_type="search_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={"providers": [x.provider for x in todo], "attempt": run.attempt_count},
    )
    _enqueue(db, settings, dispatch, user, run)
    return run


def cancel(db: Session, user: User, project: Project, run_id: uuid.UUID) -> SearchRun:
    run = require_run(db, project, run_id)
    if run.status not in SEARCH_RUN_ACTIVE or run.job_id is None:
        raise Conflict(f"This search is already {run.status}.", code="not_cancellable")
    job_service.request_cancel(db, user, run.job_id)  # queued -> cancelled now; running -> worker stops between pages
    reconcile(db, run)
    db.commit()
    db.refresh(run)
    return run


def reconcile(db: Session, run: SearchRun) -> bool:
    """If the run's job ended without the worker finalising the run (cancelled while queued, lost worker with
    attempts exhausted), finish the run so it never looks busy forever. Returns True if it changed anything."""
    if run.status not in SEARCH_RUN_ACTIVE or run.job_id is None:
        return False
    job = db.get(Job, run.job_id)
    if job is not None and job.status not in fsm.TERMINAL_STATUSES:
        return False
    cancelled = job is not None and job.status == fsm.CANCELLED
    for x in run.queries:
        if x.status in ("pending", "running"):
            x.status = "cancelled" if cancelled else ("partial" if x.records_retrieved else "failed")
            x.finished_at = x.finished_at or _now()
    run.status = "cancelled" if cancelled else "failed"
    run.stop_reason = "cancelled" if cancelled else "job_ended"
    if not cancelled:
        run.error_summary = (job.error_summary if job else None) or "The background search stopped unexpectedly."
    run.finished_at = run.finished_at or _now()
    run.row_version += 1
    return True


# ---------- broaden discovery ----------


def decide_broaden(db: Session, user: User, project: Project, run_id: uuid.UUID, decision: str) -> SearchRun:
    protocol_service.require_writable(project)
    if decision not in ("yes", "no", "later"):
        raise ValidationFailed("Choose yes, no or later.", details=[{"field": "decision", "issue": "invalid"}])
    run = require_run(db, project, run_id, lock=True)
    if run.tier != "open_access":
        raise Conflict("Broadening is decided on an open-access-first search.", code="wrong_tier")
    if run.status in SEARCH_RUN_ACTIVE:
        raise Conflict("Wait for the search to finish before deciding.", code="search_active")
    previous = run.broaden_decision
    run.broaden_decision, run.broaden_decided_at, run.broaden_decided_by = decision, _now(), user.id
    run.row_version += 1
    _record_event(db, project, user, {"action": "broaden_decision", "search_run_id": str(run.id), "decision": decision})
    audit_service.record(
        db,
        action="search.broaden_decided",
        entity_type="search_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        before={"decision": previous},
        after={"decision": decision},
    )
    db.commit()
    db.refresh(run)
    return run


# ---------- reads ----------


def list_runs(db: Session, project: Project, limit: int) -> list[SearchRun]:
    runs = repo.list_runs(db, project.id, limit)
    if any([reconcile(db, r) for r in runs]):
        db.commit()
    return runs


def get_run(db: Session, project: Project, run_id: uuid.UUID) -> SearchRun:
    run = require_run(db, project, run_id)
    if reconcile(db, run):
        db.commit()
    return run


def view(run: SearchRun, approved_id: uuid.UUID | None, reg: dict[str, ScholarlySource]) -> dict[str, Any]:
    return {
        "id": run.id,
        "tier": run.tier,
        "tier_label": TIER_LABELS[run.tier],
        "status": run.status,
        "protocol_version_id": run.protocol_version_id,
        "is_current_protocol": run.protocol_version_id == approved_id,
        "expression": run.expression,
        "expression_source": run.expression_source,
        "derived_expression": run.derived_expression,
        "derivation_notes": run.derivation_notes,
        "filters": run.filters,
        "providers": run.providers,
        "protocol_sources": run.protocol_sources,
        "limitations": run.limitations,
        "records_retrieved": run.records_retrieved,
        "records_skipped": run.records_skipped,
        "papers_new": run.papers_new,
        "papers_matched_existing": run.papers_matched_existing,
        "duplicates_in_run": run.duplicates_in_run,
        "possible_duplicates": run.possible_duplicates,
        "stop_reason": run.stop_reason,
        "error_summary": run.error_summary,
        "attempt_count": run.attempt_count,
        "broaden_decision": run.broaden_decision,
        "broaden_decided_at": run.broaden_decided_at,
        "job_id": run.job_id,
        "row_version": run.row_version,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "queries": [
            {
                "id": x.id,
                "provider": x.provider,
                "provider_label": reg[x.provider].label if x.provider in reg else x.provider,
                "attribution": reg[x.provider].attribution if x.provider in reg else None,
                "endpoint": x.endpoint,
                "query_text": x.query_text,
                "params": x.params,
                "url": public_url(x.endpoint, x.params),
                "filters_applied": x.filters_applied,
                "limitations": x.limitations,
                "status": x.status,
                "pages_fetched": x.pages_fetched,
                "request_count": x.request_count,
                "total_reported": x.total_reported,
                "records_retrieved": x.records_retrieved,
                "records_skipped": x.records_skipped,
                "skipped": x.skipped[:20],
                "error_kind": x.error_kind,
                "error_summary": x.error_summary,
                "started_at": x.started_at,
                "retrieved_at": x.retrieved_at,
                "finished_at": x.finished_at,
            }
            for x in run.queries
        ],
    }


def approved_id(db: Session, project: Project) -> uuid.UUID | None:
    a = protocol_service.approved(db, project)
    return a.id if a else None
