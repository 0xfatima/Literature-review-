"""Critical appraisal: API side (overview, record editing, approval, history, suggestion job start/status) and worker
side (`appraisal.suggest`). AI output is only ever written to `answers[domain].ai_suggestion`; judgements are written
only by reviewers. Every change is an append-only revision."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.appraisal import ai
from app.appraisal import frameworks as fwm
from app.core.config import Settings
from app.core.errors import Conflict, NotFound, RateLimited, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.db.models import AppraisalRecord, AppraisalRevision, EvidenceItem, Job, PaperRecord, Project, User
from app.extraction import schema_service
from app.jobs import fsm
from app.providers.llm import LLMError, LLMGateway, build_gateway, describe
from app.repositories import jobs as job_repo
from app.services import audit_service, consent_service, job_service, llm_jobs, protocol_service
from app.services.job_service import Dispatcher

JOB_TYPE = "appraisal.suggest"
PURPOSE = "appraisal_suggestion"
MAX_JUSTIFICATION = 2000
log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _paper(db: Session, project: Project, paper_id: uuid.UUID) -> PaperRecord:
    p = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project.id))
    if p is None:
        raise NotFound("Paper not found.")
    return p


def _framework(key: str) -> fwm.Framework:
    fw = fwm.get(key)
    if fw is None:
        raise ValidationFailed(f"Unknown appraisal framework '{key}'.", details=[{"field": "framework_key"}])
    return fw


def snapshot(r: AppraisalRecord) -> dict[str, Any]:
    return {
        "framework_key": r.framework_key,
        "framework_version": r.framework_version,
        "status": r.status,
        "answers": r.answers,
        "suggested_overall": r.suggested_overall,
        "overall_judgement": r.overall_judgement,
        "overall_justification": r.overall_justification,
        "reviewer_id": str(r.reviewer_id) if r.reviewer_id else None,
    }


def view(r: AppraisalRecord) -> dict[str, Any]:
    fw = fwm.get(r.framework_key)
    judged = sum(1 for a in (r.answers or {}).values() if a.get("decided_by"))
    return {
        "id": r.id,
        "paper_id": r.paper_id,
        **snapshot(r),
        "domains_total": len(fw.domains) if fw else 0,
        "domains_judged": judged,
        "ai_suggestions": sum(1 for a in (r.answers or {}).values() if a.get("ai_suggestion")),
        "approved_at": r.approved_at,
        "row_version": r.row_version,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _revise(
    db: Session,
    r: AppraisalRecord,
    action: str,
    before: dict[str, Any] | None,
    actor_id: uuid.UUID | None,
    *,
    model_run_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    db.flush()
    db.add(
        AppraisalRevision(
            project_id=r.project_id,
            appraisal_record_id=r.id,
            revision=r.row_version,
            action=action,
            before=before,
            after=snapshot(r),
            actor_id=actor_id,
            model_run_id=model_run_id,
            reason=reason,
        )
    )
    db.flush()


def design_hint(db: Session, project: Project, paper: PaperRecord) -> str | None:
    """The study design as extracted (a reviewed value of a field whose key or label mentions 'design')."""
    schema = schema_service.current_schema(db, project)
    if schema is None:
        return None
    keys = [
        f["field_key"]
        for f in schema.field_definitions or []
        if "design" in f["field_key"].lower() or "design" in str(f.get("label") or "").lower()
    ]
    if not keys:
        return None
    item = db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.extraction_schema_id == schema.id,
            EvidenceItem.paper_id == paper.id,
            EvidenceItem.field_key.in_(keys),
            EvidenceItem.status == "found",
        )
    )
    if item is None or item.value is None:
        return None
    v = item.value
    return "; ".join(str(x) for x in v) if isinstance(v, list) else str(v)


def _records(db: Session, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[AppraisalRecord]]:
    out: dict[uuid.UUID, list[AppraisalRecord]] = {}
    if paper_ids:
        for r in db.scalars(select(AppraisalRecord).where(AppraisalRecord.paper_id.in_(paper_ids))):
            out.setdefault(r.paper_id, []).append(r)
    return out


def overview(db: Session, project: Project) -> dict[str, Any]:
    ids = schema_service.included_paper_ids(db, project)
    papers = list(
        db.scalars(select(PaperRecord).where(PaperRecord.id.in_(ids)).order_by(PaperRecord.title, PaperRecord.id))
    )
    recs = _records(db, ids)
    rows = []
    for p in papers:
        hint = design_hint(db, project, p)
        rows.append(
            {
                "paper": {"id": p.id, "title": p.title, "year": p.year, "publication_type": p.publication_type},
                "design_hint": hint,
                "suggested_framework": fwm.suggest(hint, p.publication_type),
                "records": [view(r) for r in recs.get(p.id, [])],
            }
        )
    return {"frameworks": [f.view() for f in fwm.FRAMEWORKS.values()], "papers": rows}


def detail(db: Session, project: Project, paper_id: uuid.UUID) -> dict[str, Any]:
    p = _paper(db, project, paper_id)
    hint = design_hint(db, project, p)
    return {
        "paper": {"id": p.id, "title": p.title, "year": p.year, "publication_type": p.publication_type},
        "design_hint": hint,
        "suggested_framework": fwm.suggest(hint, p.publication_type),
        "records": [view(r) for r in _records(db, [p.id]).get(p.id, [])],
    }


def save(db: Session, project: Project, user: User, paper_id: uuid.UUID, body: dict[str, Any]) -> AppraisalRecord:
    """Reviewer edits of domain judgements (with justification and optional verbatim excerpt) and the overall."""
    paper = _paper(db, project, paper_id)
    protocol_service.require_writable(project)
    fw = _framework(str(body.get("framework_key") or ""))
    domains = body.get("domains") or {}
    if not isinstance(domains, dict):
        raise ValidationFailed("domains must be an object keyed by domain.", details=[{"field": "domains"}])
    keys = {d.key for d in fw.domains}
    rec = db.scalar(
        select(AppraisalRecord)
        .where(AppraisalRecord.paper_id == paper.id, AppraisalRecord.framework_key == fw.key)
        .with_for_update()
    )
    before = snapshot(rec) if rec else None
    if rec is None:
        rec = AppraisalRecord(
            id=uuid.uuid4(),
            project_id=project.id,
            paper_id=paper.id,
            framework_key=fw.key,
            framework_version=fw.version,
            status="draft",
            answers={},
            row_version=1,
        )
        db.add(rec)
    else:
        exp = body.get("expected_row_version")
        if exp is not None and exp != rec.row_version:
            raise Conflict("This appraisal was changed elsewhere. Reload and try again.", code="version_conflict")
    reason = (str(body.get("reason") or "").strip() or None) if body.get("reason") else None
    if rec.status == "approved" and not reason:
        raise ValidationFailed("Give a reason for changing an approved appraisal.", details=[{"field": "reason"}])
    answers = {k: dict(v) for k, v in (rec.answers or {}).items()}
    for key, ans in domains.items():
        if key not in keys:
            raise ValidationFailed(f"Unknown domain '{key}' for {fw.label}.", details=[{"field": f"domains.{key}"}])
        if not isinstance(ans, dict):
            raise ValidationFailed("Each domain answer must be an object.", details=[{"field": f"domains.{key}"}])
        j = ans.get("judgement")
        cur = answers.get(key, {})
        if j in (None, ""):
            answers[key] = {"ai_suggestion": cur.get("ai_suggestion")}
            continue
        if j not in fw.judgements:
            raise ValidationFailed(f"'{j}' is not a judgement on this framework's scale.", details=[{"field": key}])
        just = str(ans.get("justification") or "").strip()
        if not just or len(just) > MAX_JUSTIFICATION:
            raise ValidationFailed(
                f"Domain '{key}': a justification (max {MAX_JUSTIFICATION} characters) is required.",
                details=[{"field": f"domains.{key}.justification"}],
            )
        excerpt = str(ans.get("excerpt") or "") or None
        page = None
        if excerpt:
            doc_id = uuid.UUID(str(ans["document_id"])) if ans.get("document_id") else None
            if doc_id is None and not ans.get("document_page_id"):
                from app.services.extraction_service import full_text  # the paper's linked full text

                linked = full_text(db, paper.id)
                doc_id = linked.id if linked else None
            page = schema_service.resolve_page(
                db,
                project,
                paper.id,
                document_page_id=uuid.UUID(str(ans["document_page_id"])) if ans.get("document_page_id") else None,
                document_id=doc_id,
                page_no=ans.get("page_no") if isinstance(ans.get("page_no"), int) else None,
            )
            if page is None:
                raise ValidationFailed("Cite the page the excerpt was copied from.", details=[{"field": key}])
            if excerpt not in page.text:
                raise ValidationFailed(
                    f"Domain '{key}': the excerpt does not occur verbatim in the cited page.",
                    details=[{"field": f"domains.{key}.excerpt", "issue": "not_found_in_page"}],
                )
        answers[key] = {
            "judgement": j,
            "justification": just,
            "excerpt": excerpt,
            "document_page_id": str(page.id) if page else None,
            "page_no": page.page_no if page else None,
            "decided_by": str(user.id),
            "decided_at": _now().isoformat(),
            "ai_suggestion": cur.get("ai_suggestion"),
        }
    rec.answers = answers
    if "overall_judgement" in body:
        oj = body.get("overall_judgement") or None
        if oj is not None and oj not in fw.overall_options:
            raise ValidationFailed(f"'{oj}' is not an overall option for {fw.label}.")
        rec.overall_judgement = oj
    if "overall_justification" in body:
        rec.overall_justification = str(body.get("overall_justification") or "").strip() or None
    rec.suggested_overall = fwm.suggested_overall(fw, {k: a.get("judgement") for k, a in answers.items()})[0]
    if before is not None:
        rec.row_version += 1
        rec.updated_at = _now()
    reopened = rec.status == "approved"
    if reopened:
        rec.status, rec.approved_at = "draft", None
    _revise(db, rec, "reopened" if reopened else "reviewer_edit", before, user.id, reason=reason)
    audit_service.record(
        db,
        action="appraisal.edited",
        entity_type="appraisal_record",
        entity_id=rec.id,
        actor_id=user.id,
        project_id=project.id,
        after={"framework": fw.key, "revision": rec.row_version, "reopened": reopened},
    )
    db.commit()
    db.refresh(rec)
    return rec


def approve(db: Session, project: Project, user: User, paper_id: uuid.UUID, body: dict[str, Any]) -> AppraisalRecord:
    paper = _paper(db, project, paper_id)
    protocol_service.require_writable(project)
    fw = _framework(str(body.get("framework_key") or ""))
    rec = db.scalar(
        select(AppraisalRecord).where(AppraisalRecord.paper_id == paper.id, AppraisalRecord.framework_key == fw.key)
    )
    if rec is None:
        raise NotFound("Appraisal not found.")
    exp = body.get("expected_row_version")
    if exp is not None and exp != rec.row_version:
        raise Conflict("This appraisal was changed elsewhere. Reload and try again.", code="version_conflict")
    if rec.status == "approved":
        return rec
    undecided = [d.label for d in fw.domains if not (rec.answers or {}).get(d.key, {}).get("decided_by")]
    if undecided:
        raise Conflict(
            f"A reviewer must judge every domain before approval (AI suggestions do not count): "
            f"{', '.join(undecided)}.",
            code="domains_undecided",
        )
    if not rec.overall_judgement:
        raise Conflict("Choose an overall judgement before approval.", code="overall_missing")
    needs_why = fw.kind == "checklist" or (rec.suggested_overall and rec.overall_judgement != rec.suggested_overall)
    if needs_why and not rec.overall_justification:
        raise Conflict(
            "Justify the overall judgement (required for checklists and when departing from the suggestion).",
            code="overall_justification_missing",
        )
    before = snapshot(rec)
    rec.status, rec.reviewer_id, rec.approved_at = "approved", user.id, _now()
    rec.row_version += 1
    rec.updated_at = _now()
    _revise(db, rec, "approved", before, user.id)
    audit_service.record(
        db,
        action="appraisal.approved",
        entity_type="appraisal_record",
        entity_id=rec.id,
        actor_id=user.id,
        project_id=project.id,
        after={"framework": fw.key, "overall": rec.overall_judgement},
    )
    db.commit()
    db.refresh(rec)
    return rec


def history(db: Session, project: Project, paper_id: uuid.UUID, framework_key: str | None) -> dict[str, Any]:
    _paper(db, project, paper_id)
    q = select(AppraisalRecord).where(AppraisalRecord.paper_id == paper_id)
    if framework_key:
        q = q.where(AppraisalRecord.framework_key == framework_key)
    recs = list(db.scalars(q))
    revs = (
        list(
            db.scalars(
                select(AppraisalRevision)
                .where(AppraisalRevision.appraisal_record_id.in_([r.id for r in recs]))
                .order_by(AppraisalRevision.at, AppraisalRevision.revision)
            )
        )
        if recs
        else []
    )
    return {
        "revisions": [
            {
                "record_id": r.appraisal_record_id,
                "revision": r.revision,
                "action": r.action,
                "before": r.before,
                "after": r.after,
                "actor_id": r.actor_id,
                "model_run_id": r.model_run_id,
                "reason": r.reason,
                "at": r.at,
            }
            for r in revs
        ]
    }


def approved_by_paper(db: Session, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, AppraisalRecord]:
    """Latest approved appraisal per paper (used by synthesis)."""
    out: dict[uuid.UUID, AppraisalRecord] = {}
    if not paper_ids:
        return out
    rows = db.scalars(
        select(AppraisalRecord)
        .where(AppraisalRecord.paper_id.in_(paper_ids), AppraisalRecord.status == "approved")
        .order_by(AppraisalRecord.approved_at)
    )
    for r in rows:
        out[r.paper_id] = r
    return out


# ---------- AI suggestions: API side ----------


def _jobs(db: Session, user: User, project: Project, active_only: bool) -> list[Job]:
    return [
        j
        for j in job_service.list_(db, user, project_id=project.id, active_only=active_only, limit=50)
        if j.type == JOB_TYPE
    ]


def suggestion_status(db: Session, settings: Settings, user: User, project: Project) -> dict[str, Any]:
    recent = _jobs(db, user, project, active_only=False)
    return {
        "llm": describe(settings),
        "consent": consent_service.status(db, settings, project),
        "prompt_version": ai.PROMPT_VERSION,
        "active_job_id": next((j.id for j in recent if j.status in fsm.ACTIVE_STATUSES), None),
        "last_result": next((j.result for j in recent if j.status in fsm.TERMINAL_STATUSES and j.result), None),
        "last_error": next((j.error_summary for j in recent if j.status == fsm.FAILED), None),
    }


def start_suggestions(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    limiter: RateLimiter,
    user: User,
    project: Project,
    *,
    paper_ids: list[str] | None,
    frameworks: dict[str, str] | None,
    request_key: str | None = None,
) -> Job:
    protocol_service.require_writable(project)
    included = [str(p) for p in schema_service.included_paper_ids(db, project)]
    if not included:
        raise Conflict("No papers are included yet.", code="no_included_papers")
    chosen = [str(p) for p in paper_ids] if paper_ids else included
    if any(p not in included for p in chosen):
        raise ValidationFailed("Only included papers can be appraised.", details=[{"field": "paper_ids"}])
    fmap: dict[str, str] = {}
    for p in chosen:
        key = (frameworks or {}).get(p)
        if key is None:
            paper = db.get(PaperRecord, uuid.UUID(p))
            existing = db.scalar(select(AppraisalRecord.framework_key).where(AppraisalRecord.paper_id == uuid.UUID(p)))
            key = existing or (fwm.suggest(design_hint(db, project, paper), paper.publication_type) if paper else None)
        if key is not None:
            _framework(key)
            fmap[p] = key
    if not fmap:
        raise Conflict("Choose a framework for at least one paper first.", code="no_framework")
    if _jobs(db, user, project, active_only=True):
        raise Conflict("Appraisal suggestions are already running.", code="job_already_active")
    allowed, retry = limiter.hit(f"appraisal:{user.id}", settings.rate_limit_extraction_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many appraisal suggestion runs in the last hour. Try again later.")
    job, _ = job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=project.id,
        payload={"frameworks": fmap, "nonce": request_key or str(uuid.uuid4())},
        request_key=request_key,
    )
    return job


# ---------- AI suggestions: worker side ----------


def _store_suggestions(
    db: Session,
    project: Project,
    paper_id: uuid.UUID,
    fw: fwm.Framework,
    merged: dict[str, ai.Suggestion],
    run_ids: dict[int, uuid.UUID],
) -> None:
    rec = db.scalar(
        select(AppraisalRecord)
        .where(AppraisalRecord.paper_id == paper_id, AppraisalRecord.framework_key == fw.key)
        .with_for_update()
    )
    before = snapshot(rec) if rec else None
    if rec is None:
        rec = AppraisalRecord(
            id=uuid.uuid4(),
            project_id=project.id,
            paper_id=paper_id,
            framework_key=fw.key,
            framework_version=fw.version,
            status="draft",
            answers={},
            row_version=1,
        )
        db.add(rec)
    else:
        rec.row_version += 1
        rec.updated_at = _now()
    answers = {k: dict(v) for k, v in (rec.answers or {}).items()}
    last_run: uuid.UUID | None = None
    for key, s in merged.items():
        run = run_ids.get(s.chunk_index)
        last_run = run or last_run
        answers.setdefault(key, {})["ai_suggestion"] = {
            "judgement": s.judgement,
            "justification": s.justification,
            "excerpt": s.excerpt,
            "document_page_id": s.page_id,
            "page_no": s.page_no,
            "supported": s.supported,
            "model_run_id": str(run) if run else None,
            "prompt_version": ai.PROMPT_VERSION,
        }
    rec.answers = answers
    _revise(db, rec, "ai_suggested", before, None, model_run_id=last_run)


def execute_suggestions(db: Session, job: Job, settings: Settings, *, gateway: LLMGateway | None = None) -> str:
    from app.services.extraction_service import full_text

    payload = job.input or {}
    project = db.get(Project, job.project_id) if job.project_id else None
    if project is None:
        job_repo.finish(db, job.id, fsm.RUNNING, fsm.FAILED, error="Project not found.")
        db.commit()
        return "failed"
    gw = gateway or build_gateway(settings)
    consent, block = llm_jobs.gate(db, project, gw)
    result: dict[str, Any] = {"papers_suggested": 0, "papers_no_full_text": 0, "papers_skipped_approved": 0}
    stop: str | None = None
    if block is not None:
        stop = "not_configured" if gw.provider is None else "refused"
        result["blocked_reason"] = block
    deadline = time.monotonic() + settings.extraction_job_budget_s
    items = list((payload.get("frameworks") or {}).items())
    for n, (pid_s, key) in enumerate(items, start=1):
        if stop is not None:
            break
        db.refresh(job)
        if job.cancel_requested:
            stop = "cancelled"
            break
        if time.monotonic() > deadline:
            stop = "time_budget"
            break
        fw = fwm.get(key)
        paper = db.get(PaperRecord, uuid.UUID(pid_s))
        if fw is None or paper is None or paper.project_id != project.id:
            continue
        existing = db.scalar(
            select(AppraisalRecord).where(AppraisalRecord.paper_id == paper.id, AppraisalRecord.framework_key == key)
        )
        if existing is not None and existing.status == "approved":
            result["papers_skipped_approved"] += 1
            continue
        doc = full_text(db, paper.id)
        if doc is None:
            result["papers_no_full_text"] += 1
            continue
        chunks = llm_jobs.document_chunks(db, doc, settings.extraction_chunk_chars)
        per_chunk: list[dict[str, ai.Suggestion]] = []
        run_ids: dict[int, uuid.UUID] = {}
        for i, chunk in enumerate(chunks):
            try:
                res = gw.generate_json(
                    data_class="private_document",
                    system=ai.SYSTEM,
                    prompt=ai.build_prompt(fw, paper.title, chunk, i + 1, len(chunks)),
                    schema=ai.SCHEMA,
                    temperature=0.0,
                    max_output_tokens=min(8192, 250 * len(fw.domains) + 400),
                    consent=consent,
                )
            except LLMError as e:
                llm_jobs.model_run(
                    db,
                    job,
                    project.id,
                    gw,
                    purpose=PURPOSE,
                    prompt_version=ai.PROMPT_VERSION,
                    n=len(fw.domains),
                    status="failed",
                    error_kind=e.kind,
                    error_summary=e.message[:500],
                )
                if e.kind in llm_jobs.STOP_KINDS:
                    stop = e.kind
                    break
                continue
            mr = llm_jobs.model_run(
                db,
                job,
                project.id,
                gw,
                purpose=PURPOSE,
                prompt_version=ai.PROMPT_VERSION,
                n=len(fw.domains),
                model=res.model,
                status="succeeded",
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                duration_ms=res.duration_ms,
                attempts=res.attempts,
            )
            run_ids[i] = mr.id
            per_chunk.append(ai.validate(fw, chunk, res.data or {}, i))
        merged = ai.merge(fw, per_chunk)
        if merged:
            _store_suggestions(db, project, paper.id, fw, merged, run_ids)
            result["papers_suggested"] += 1
        job_repo.set_progress(db, job.id, min(95, int(95 * n / max(len(items), 1))), f"Appraised {n} of {len(items)}")
        db.commit()
    result["stop_reason"] = stop
    audit_service.record(
        db,
        action="appraisal.suggested",
        entity_type="job",
        entity_id=job.id,
        actor_id=None,
        project_id=project.id,
        after={k: v for k, v in result.items() if k != "blocked_reason"},
    )
    job_repo.finish(db, job.id, fsm.RUNNING, fsm.CANCELLED if stop == "cancelled" else fsm.SUCCEEDED, result=result)
    db.commit()
    return "cancelled" if stop == "cancelled" else "ok"
