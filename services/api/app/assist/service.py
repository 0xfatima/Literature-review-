"""Contextual workspace actions. Each is a real backend operation, scoped to an owned project:

* explain_exclusion: the recorded screening decisions (reason, protocol criterion, note), plus advisory AI
  assessments, as structured data and plain sentences; optionally posted into a chat thread.
* find_evidence: ranked verbatim passages from the paper's stored full text for a field or query.
* ask: a short AI answer in a chat thread, grounded only in the protocol, the current workflow step and (optionally)
  one paper's bibliographic metadata (data class `public_metadata`; uploaded documents are never sent). The answer
  is stored as an assistant message marked advisory, with a model run record.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assist import logic
from app.core.config import Settings
from app.core.errors import Conflict, NotFound, RateLimited, ServiceUnavailable, ValidationFailed
from app.core.rate_limit import RateLimiter
from app.core.untrusted import fence
from app.db.models import (
    DocumentPage,
    ExtractionSchema,
    Message,
    ModelRun,
    PaperRecord,
    Project,
    ProtocolVersion,
    RelevanceAssessment,
    ScreeningDecision,
    ScreeningState,
    User,
    WorkflowRun,
)
from app.extraction import schema_service
from app.providers.llm import LLMError, LLMGateway
from app.services import audit_service, conversation_service, protocol_service

ASK_PROMPT_VERSION = "assistant-v1"
MAX_QUESTION = 1000
ASK_SYSTEM = (
    "You are the assistant inside a systematic-review application. Answer the user's question in at most 150 words "
    "using ONLY the context given (the review protocol, the current workflow step and, if given, one paper's "
    "bibliographic record). If the context does not contain the answer, say so plainly and suggest where in the "
    "application the user can find or decide it. Never invent studies, numbers, citations or decisions, and never "
    "make a screening or eligibility decision for the user."
)
ASK_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "answer": {"type": "STRING"},
        "answerable_from_context": {"type": "BOOLEAN"},
    },
    "required": ["answer", "answerable_from_context"],
}


def _paper(db: Session, project: Project, paper_id: uuid.UUID) -> PaperRecord:
    p = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project.id))
    if p is None:
        raise NotFound("Paper not found.")
    return p


def _criteria(
    db: Session, pv_id: uuid.UUID, cache: dict[uuid.UUID, dict[str, dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    if pv_id not in cache:
        pv = db.get(ProtocolVersion, pv_id)
        content = protocol_service.to_content(pv) if pv else {"criteria": []}
        cache[pv_id] = {c["stable_key"]: c for c in content.get("criteria") or []}
    return cache[pv_id]


def explain_exclusion(db: Session, project: Project, paper_id: uuid.UUID) -> dict[str, Any]:
    paper = _paper(db, project, paper_id)
    approved = protocol_service.approved(db, project)
    rows = db.execute(
        select(ScreeningState, ScreeningDecision)
        .join(ScreeningDecision, ScreeningDecision.id == ScreeningState.decision_id)
        .where(ScreeningState.paper_id == paper.id)
        .order_by(ScreeningState.stage)
    ).all()
    cache: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
    stages = []
    for st, d in rows:
        crit_kind = crit_text = None
        if d.reason_code and ":" in d.reason_code:
            kind, key = d.reason_code.split(":", 1)
            c = _criteria(db, d.protocol_version_id, cache).get(key)
            if c is not None:
                crit_kind, crit_text = kind, c["text"]
        stages.append(
            {
                "stage": st.stage,
                "decision": st.decision,
                "reason_code": d.reason_code,
                "reason_label": d.reason_label,
                "criterion_kind": crit_kind,
                "criterion_text": crit_text,
                "note": d.note,
                "decided_at": d.created_at.isoformat(),
                "decided_by_you": True,  # projects have a single owner; every decision is the owner's
                "current_protocol": approved is not None and d.protocol_version_id == approved.id,
                "decision_id": d.id,
            }
        )
    history = db.scalar(
        select(ScreeningDecision.id).where(ScreeningDecision.paper_id == paper.id).limit(1).offset(len(rows))
    )
    ai: list[dict[str, Any]] = []
    if approved is not None:
        for a in db.scalars(
            select(RelevanceAssessment)
            .where(RelevanceAssessment.paper_id == paper.id, RelevanceAssessment.protocol_version_id == approved.id)
            .order_by(RelevanceAssessment.created_at.desc())
        ):
            if any(x["method"] == a.method for x in ai):
                continue
            ai.append(
                {
                    "method": a.method,
                    "score": a.score,
                    "band": a.band,
                    "suggestion": a.eligibility_suggestion,
                    "summary": str((a.explanation or {}).get("summary") or "")[:600] or None,
                    "warnings": list(a.warnings or []),
                }
            )
    excluded = any(s["decision"] == "exclude" for s in stages)
    return {
        "paper": {"id": paper.id, "title": paper.title, "year": paper.year},
        "status": "excluded" if excluded else (stages[-1]["decision"] if stages else "undecided"),
        "stages": stages,
        "has_earlier_decisions": history is not None,
        "ai_assessments": ai,
        "lines": logic.explain_lines(stages, ai),
        "source": "Composed from the recorded screening decisions and assessments; nothing is inferred.",
    }


def find_evidence(db: Session, project: Project, paper_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    from app.services.extraction_service import full_text

    paper = _paper(db, project, paper_id)
    query = str(body.get("query") or "").strip()[:300] or None
    field: dict[str, Any] | None = None
    field_key = body.get("field_key")
    if field_key:
        schema: ExtractionSchema | None = schema_service.current_schema(db, project)
        field = next(
            (f for f in (schema.field_definitions if schema else []) or [] if f["field_key"] == field_key), None
        )
        if field is None:
            raise ValidationFailed("Unknown field for the current extraction schema.", details=[{"field": "field_key"}])
    if not query and field is None:
        raise ValidationFailed("Give a search query or a field.", details=[{"field": "query"}])
    terms = logic.terms_for(query, field)
    doc = full_text(db, paper.id)
    if doc is None:
        return {
            "status": "no_full_text",
            "terms": terms,
            "hits": [],
            "document_id": None,
            "field_key": field_key,
            "message": "No parsed full text is linked to this paper. Upload a copy you are authorised to use.",
        }
    pages = [
        (p.page_no, p.text or "")
        for p in db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == doc.id, DocumentPage.text_version == doc.text_version)
            .order_by(DocumentPage.page_no)
        )
    ]
    hits = logic.search_pages(pages, terms)
    return {
        "status": "ok" if hits else "no_match",
        "terms": terms,
        "hits": hits,
        "document_id": doc.id,
        "filename": doc.original_filename,
        "pages_searched": len(pages),
        "field_key": field_key,
        "message": None
        if hits
        else "No passage in the processed text contains these terms. This is not evidence "
        "that the paper does not report it.",
    }


def post_note(
    db: Session, project: Project, user: User, conversation_id: uuid.UUID, content: str, payload: dict[str, Any]
) -> Message:
    """Write an application message (role assistant) into a thread."""
    protocol_service.require_writable(project)
    conversation_service.get(db, project, conversation_id)
    msg = conversation_service.append_workflow_message(
        db,
        conversation_id,
        role="assistant",
        author_id=None,
        content=content,
        payload=payload,
        client_message_id=uuid.uuid4(),
    )
    db.commit()
    db.refresh(msg)
    return msg


def explain_to_chat(
    db: Session, project: Project, user: User, paper_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> dict[str, Any]:
    out = explain_exclusion(db, project, paper_id)
    if conversation_id is not None:
        text = f"Explanation for “{out['paper']['title']}” (from the review record):\n" + "\n".join(
            f"• {line}" for line in out["lines"]
        )
        msg = post_note(
            db,
            project,
            user,
            conversation_id,
            text,
            {"type": "assist_result", "action": "explain_exclusion", "paper_id": str(paper_id)},
        )
        out["message_id"] = msg.id
    return out


def _context(db: Session, project: Project, paper: PaperRecord | None) -> str:
    pv = protocol_service.approved(db, project) or protocol_service.draft(db, project)
    lines = [f"Project title: {project.title}"]
    if pv is not None:
        c = protocol_service.to_content(pv)
        lines.append(f"Protocol version {pv.version} ({pv.status}). Objective: {c.get('objective') or 'not set'}")
        lines += [f"Question: {q['text']}" for q in c.get("questions") or []]
        lines += [
            f"{x['kind']} criterion: {x['text']}"
            for x in c.get("criteria") or []
            if x.get("state", "accepted") == "accepted"
        ]
        if c.get("study_designs"):
            lines.append(f"Study designs: {', '.join(c['study_designs'])}")
    else:
        lines.append("No protocol has been drafted yet.")
    run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.project_id == project.id).order_by(WorkflowRun.created_at.desc()).limit(1)
    )
    if run is not None:
        lines.append(f"Workflow stage: {run.stage}; status: {run.status}.")
        q = run.pending_question or {}
        if q:
            lines.append(f"Pending question to the user: {q.get('prompt')} ({q.get('help') or ''})")
    if paper is not None:
        record = [
            f"Paper: {paper.title} ({paper.year or 'n.d.'}); venue: {paper.venue or 'unknown'}; "
            f"type: {paper.publication_type or 'unknown'}"
        ]
        if paper.abstract:
            record.append(f"Abstract: {paper.abstract[:2500]}")
        lines.append(fence("\n".join(record), "selected paper"))  # third-party text (Phase 14)
    return "\n".join(lines)


def ask(
    db: Session,
    settings: Settings,
    gw: LLMGateway,
    limiter: RateLimiter,
    project: Project,
    user: User,
    conversation_id: uuid.UUID,
    body: dict[str, Any],
) -> dict[str, Any]:
    conversation_service.get(db, project, conversation_id)  # 404 first
    protocol_service.require_writable(project)
    question = str(body.get("question") or "").strip()
    if not question or len(question) > MAX_QUESTION:
        raise ValidationFailed(f"Ask a question of 1-{MAX_QUESTION} characters.", details=[{"field": "question"}])
    paper = _paper(db, project, uuid.UUID(str(body["paper_id"]))) if body.get("paper_id") else None
    if not gw.available or gw.provider is None:
        raise Conflict(
            "No AI model is configured on this server, so the assistant cannot answer.", code="llm_not_configured"
        )
    allowed, retry = limiter.hit(f"assistant:{user.id}", settings.rate_limit_assistant_per_hour, 3600)
    if not allowed:
        raise RateLimited(retry, "Too many assistant questions in the last hour. Try again later.")
    user_msg, _ = conversation_service.post_user_message(
        db, user, project, conversation_id, content=question, client_message_id=None
    )
    if gw.budget_guard is None:
        from app.services.llm_jobs import token_guard

        gw.budget_guard = token_guard(db, project.id, settings.llm_daily_token_budget)
    prompt = f"Context:\n{_context(db, project, paper)}\n\nUser question: {question}"
    started = time.monotonic()
    try:
        res = gw.generate_json(
            data_class="public_metadata",
            system=ASK_SYSTEM,
            prompt=prompt,
            schema=ASK_SCHEMA,
            temperature=0.2,
            max_output_tokens=800,
        )
    except LLMError as e:
        db.add(
            ModelRun(
                project_id=project.id,
                purpose="assistant_answer",
                provider=gw.provider.name,
                model=gw.provider.model[:120],
                prompt_version=ASK_PROMPT_VERSION,
                data_class="public_metadata",
                status="failed",
                error_kind=e.kind,
                error_summary=e.message[:500],
                item_count=1,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        db.commit()
        raise ServiceUnavailable(f"The assistant could not answer: {e.message}") from None
    data = res.data or {}
    answer = str(data.get("answer") or "").strip()[:2000] or "The assistant returned no answer."
    mr = ModelRun(
        project_id=project.id,
        purpose="assistant_answer",
        provider=res.provider,
        model=res.model[:120],
        prompt_version=ASK_PROMPT_VERSION,
        data_class="public_metadata",
        status="succeeded",
        item_count=1,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        duration_ms=res.duration_ms,
        attempts=res.attempts,
    )
    db.add(mr)
    db.flush()
    payload = {
        "type": "ai_answer",
        "advisory": True,
        "model_run_id": str(mr.id),
        "answerable_from_context": bool(data.get("answerable_from_context")),
        "paper_id": str(paper.id) if paper else None,
        "question_message_id": str(user_msg.id),
    }
    msg = conversation_service.append_workflow_message(
        db,
        conversation_id,
        role="assistant",
        author_id=None,
        content=answer,
        payload=payload,
        client_message_id=uuid.uuid4(),
    )
    audit_service.record(
        db,
        action="assistant.answered",
        entity_type="message",
        entity_id=msg.id,
        actor_id=user.id,
        project_id=project.id,
        after={"model_run_id": str(mr.id)},
    )
    db.commit()
    db.refresh(msg)
    return {"question_message_id": user_msg.id, "answer_message_id": msg.id, "answer": answer, "payload": payload}


# ---------- workspace overview (stage navigator) ----------


def _stage(key: str, label: str, tab: str, status: str, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "tab": tab, "status": status, "detail": detail}


def workspace_overview(db: Session, project: Project) -> dict[str, Any]:
    """Live status of every review stage from stored data (never from the UI). Used by the stage navigator."""
    from sqlalchemy import func

    from app.db.models import AppraisalRecord, DraftVersion, EvidenceItem, Job, SearchRun, SynthesisVersion
    from app.jobs import fsm
    from app.prisma import service as prisma_service

    stages: list[dict[str, Any]] = []
    approved = protocol_service.approved(db, project)
    draft = protocol_service.draft(db, project)
    run = db.scalar(
        select(WorkflowRun).where(WorkflowRun.project_id == project.id).order_by(WorkflowRun.created_at.desc()).limit(1)
    )
    if run is not None and run.status in ("failed", "needs_attention"):
        stages.append(
            _stage(
                "protocol", "Protocol", "protocol", "attention", run.error_summary or "The workflow needs attention."
            )
        )
    elif approved is not None:
        extra = " · a revision is in draft" if draft is not None else ""
        stages.append(_stage("protocol", "Protocol", "protocol", "done", f"Version {approved.version} approved{extra}"))
    elif draft is not None or run is not None:
        stages.append(
            _stage("protocol", "Protocol", "protocol", "in_progress", "Drafting; awaiting your answers or approval")
        )
    else:
        stages.append(
            _stage("protocol", "Protocol", "protocol", "not_started", "Set up the review question and criteria")
        )

    papers = db.scalar(select(func.count()).select_from(PaperRecord).where(PaperRecord.project_id == project.id)) or 0
    running = (
        db.scalar(
            select(func.count())
            .select_from(SearchRun)
            .where(SearchRun.project_id == project.id, SearchRun.status.in_(("queued", "running")))
        )
        or 0
    )
    if running:
        stages.append(_stage("search", "Search", "search", "in_progress", f"Searching · {papers} papers so far"))
    elif papers:
        stages.append(_stage("search", "Search", "search", "done", f"{papers} papers found"))
    else:
        stages.append(_stage("search", "Search", "search", "not_started", "No search run yet"))

    flow = prisma_service.flow_view(prisma_service.get_flow(db, project.id))
    screened = int(flow["screened"]) - int(flow["pending_ta"])
    if not papers:
        st, detail = "not_started", "Nothing to screen yet"
    elif flow["pending_ta"] or not flow["screened"]:
        st, detail = "in_progress", f"{screened} of {flow['screened']} screened · {flow['ta_included_a']} included"
    else:
        st, detail = "done", f"{flow['screened']} screened · {flow['ta_included_a']} included"
    stages.append(_stage("screening", "Screening", "screening", st, detail))

    sought = int(flow["sought_retrieval"])
    pending_ft = int(flow["pending_retrieval"]) + int(flow["pending_ft"])
    if not sought:
        stages.append(_stage("full_text", "Full text", "fulltext", "not_started", "No full texts sought yet"))
    else:
        stages.append(
            _stage(
                "full_text",
                "Full text",
                "fulltext",
                "in_progress" if pending_ft else "done",
                f"{flow['ft_assessed']} assessed · {pending_ft} pending · {flow['included_studies']} included",
            )
        )

    schema = schema_service.current_schema(db, project)
    included = schema_service.included_paper_ids(db, project)
    if schema is None:
        stages.append(_stage("extraction", "Extraction", "extraction", "not_started", "No approved extraction schema"))
    else:
        expected = len(included) * len(schema.field_definitions or [])
        reviewed = (
            db.scalar(
                select(func.count())
                .select_from(EvidenceItem)
                .where(
                    EvidenceItem.extraction_schema_id == schema.id,
                    EvidenceItem.review_status.in_(("approved", "edited")),
                )
            )
            or 0
        )
        unreviewed = (
            db.scalar(
                select(func.count())
                .select_from(EvidenceItem)
                .where(
                    EvidenceItem.extraction_schema_id == schema.id,
                    EvidenceItem.review_status == "unreviewed",
                    EvidenceItem.status != "pending",
                )
            )
            or 0
        )
        st = "done" if expected and reviewed >= expected else "in_progress"
        stages.append(
            _stage(
                "extraction",
                "Extraction",
                "tables",
                st,
                f"{reviewed} of {expected} values reviewed"
                + (f" · {unreviewed} AI values to check" if unreviewed else ""),
            )
        )

    approved_app = (
        db.scalar(
            select(func.count())
            .select_from(AppraisalRecord)
            .where(AppraisalRecord.project_id == project.id, AppraisalRecord.status == "approved")
        )
        or 0
    )
    if not included:
        stages.append(_stage("appraisal", "Appraisal", "appraisal", "not_started", "No included studies yet"))
    else:
        stages.append(
            _stage(
                "appraisal",
                "Appraisal",
                "appraisal",
                "done" if approved_app >= len(included) else ("in_progress" if approved_app else "not_started"),
                f"{approved_app} of {len(included)} studies appraised",
            )
        )

    syn = list(
        db.execute(
            select(SynthesisVersion.status, SynthesisVersion.version)
            .where(SynthesisVersion.project_id == project.id)
            .order_by(SynthesisVersion.version.desc())
        ).all()
    )
    if any(s == "draft" for s, _ in syn):
        stages.append(_stage("synthesis", "Synthesis", "synthesis", "in_progress", "Draft awaiting your review"))
    elif any(s == "accepted" for s, _ in syn):
        v = next(v for s, v in syn if s == "accepted")
        stages.append(_stage("synthesis", "Synthesis", "synthesis", "done", f"Version {v} accepted"))
    else:
        stages.append(_stage("synthesis", "Synthesis", "synthesis", "not_started", "No synthesis yet"))

    rep = db.scalar(
        select(DraftVersion).where(DraftVersion.project_id == project.id).order_by(DraftVersion.version.desc()).limit(1)
    )
    if rep is None:
        stages.append(_stage("report", "Report", "report", "not_started", "No draft yet"))
    else:
        s = (rep.validation or {}).get("summary") or {}
        stages.append(
            _stage(
                "report",
                "Report",
                "report",
                "attention" if s.get("errors") else "in_progress",
                f"Draft v{rep.version} · {s.get('errors', 0)} error(s) · not publication-ready",
            )
        )

    active = (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.project_id == project.id, Job.status.in_(tuple(fsm.ACTIVE_STATUSES)))
        )
        or 0
    )
    failed = (
        db.scalar(select(func.count()).select_from(Job).where(Job.project_id == project.id, Job.status == fsm.FAILED))
        or 0
    )
    current = next((s["key"] for s in stages if s["status"] in ("attention", "in_progress")), None) or next(
        (s["key"] for s in stages if s["status"] == "not_started"), stages[-1]["key"]
    )
    return {"stages": stages, "current": current, "active_jobs": active, "failed_jobs": failed}
