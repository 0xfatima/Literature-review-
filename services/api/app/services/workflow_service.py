"""The review workflow as seen by the API and the worker.

API side (start / resume / revise / recover): validate, apply the human decision through protocol_service, record
it once in workflow_events, append the chat messages, and queue a `workflow.advance` job - all in ONE transaction.
Worker side (sync_from_checkpoint, mark_needs_attention, fail): mirror the graph's position into workflow_runs and
post the next question to the protocol conversation. Writes are idempotent (keys derived from question ids), so a
redelivered or retried job cannot duplicate a message or an answer.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Project, User, WorkflowRun
from app.protocol import stages
from app.protocol.content import CRITERION_KINDS, FIELD_LABELS
from app.protocol.frameworks import FRAMEWORKS, KeywordFrameworkAdvisor
from app.protocol.suggestions import propose_criteria, propose_question
from app.repositories import workflows as repo
from app.services import audit_service, conversation_service, job_service, protocol_service
from app.services.job_service import Dispatcher
from app.workflows import questions as qs
from app.workflows.graph import GRAPH_NAME, GRAPH_VERSION

JOB_TYPE = "workflow.advance"
CONVERSATION_TITLE = "Protocol setup"
SCOPE_FIELDS = ("date_from", "date_to", "languages", "study_designs", "sources", "search_strategy")
ALLOWED_ACTIONS = {
    "framework": ("accept", "choose", "skip"),
    "review": ("approve", "edit"),
}
SECTION_LABELS = {
    "objective": "objective",
    "framework": "question framework",
    "elements": "framework elements",
    "questions": "review questions",
    "scope": "scope",
    "criteria": "eligibility criteria",
}
_ANSWER_NS = uuid.UUID("5d1b0c34-8e0c-4c4e-9d55-0a7a9c1b2f10")


def _now() -> datetime:
    return datetime.now(UTC)


def get(db: Session, project: Project) -> WorkflowRun | None:
    return repo.for_project(db, project.id)


def require(db: Session, project: Project, *, lock: bool = False) -> WorkflowRun:
    run = repo.for_project(db, project.id, lock=lock)
    if run is None:
        raise NotFound("This project has no review workflow yet.")
    return run


def events(db: Session, project: Project, limit: int) -> list[Any]:
    return repo.list_events(db, require(db, project).id, limit)


def _enqueue(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, run: WorkflowRun, payload: dict[str, Any]
) -> None:
    job_service.enqueue(
        db,
        settings,
        dispatch,
        user,
        job_type=JOB_TYPE,
        project_id=run.project_id,
        payload={"run_id": str(run.id), **payload},
        request_key=None,
        workflow_run_id=run.id,
    )  # commits the caller's pending changes together with the job row, then dispatches


# ---------- start ----------


def start(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project
) -> tuple[WorkflowRun, bool]:
    """Create the project's workflow (and its protocol conversation and first draft). Idempotent."""
    protocol_service.require_writable(project)
    existing = repo.for_project(db, project.id)
    if existing is not None:
        return existing, False
    conv = conversation_service.create(db, user, project, title=CONVERSATION_TITLE, commit=False)
    pv = protocol_service.create_first_draft(db, user, project)
    run = WorkflowRun(
        project_id=project.id,
        conversation_id=conv.id,
        thread_id=uuid.uuid4(),
        graph_name=GRAPH_NAME,
        graph_version=GRAPH_VERSION,
        stage="DRAFT",
        status="queued",
        protocol_version_id=pv.id,
        max_iterations=settings.workflow_max_iterations,
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    repo.add_event(db, run.id, "started", stage="DRAFT", actor_id=user.id)
    audit_service.record(
        db,
        action="workflow.started",
        entity_type="workflow_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={"graph": GRAPH_NAME, "graph_version": GRAPH_VERSION},
    )
    _enqueue(db, settings, dispatch, user, run, {"command": "start", "mode": "start"})
    db.refresh(run)
    return run, True


# ---------- resume (answer the pending question) ----------


def _canonical(answer: dict[str, Any]) -> str:
    return json.dumps(answer, sort_keys=True, separators=(",", ":"), default=str)


def resume(
    db: Session,
    settings: Settings,
    dispatch: Dispatcher,
    user: User,
    project: Project,
    *,
    question_id: uuid.UUID,
    action: str,
    values: dict[str, Any],
    expected_row_version: int | None,
) -> tuple[WorkflowRun, bool]:
    """Answer the pending question. Returns (run, created); created=False means this exact answer was already
    recorded (a duplicate resume is a no-op). The answer is validated and applied BEFORE the graph moves; if it
    is invalid nothing changes and the run keeps waiting on the same question."""
    protocol_service.require_writable(project)
    request = {"action": action, "values": values, "expected_row_version": expected_row_version}
    run = require(db, project, lock=True)
    previous = repo.answer_for(db, run.id, question_id)
    if previous is not None:
        if _canonical(previous.payload.get("request", {})) == _canonical(request):
            db.rollback()
            return run, False
        raise Conflict("This question was already answered differently.", code="already_answered")
    if run.status != "awaiting_input" or run.pending_question is None:
        raise Conflict("The workflow is not waiting for an answer right now.", code="not_awaiting_input")
    if run.pending_question_id != question_id:
        raise Conflict("That question is no longer the current one. Reload to see it.", code="stale_question")

    q = run.pending_question
    kind = str(q["kind"])
    allowed = ALLOWED_ACTIONS.get(kind, ("save", "skip"))
    if action not in allowed:
        raise ValidationFailed(
            f"Action '{action}' is not valid here.",
            details=[{"field": "action", "issue": "invalid", "allowed": allowed}],
        )
    route: dict[str, Any] = {"question_id": str(question_id), "action": action}
    summary = _apply(db, user, project, run, q, action, values, expected_row_version, route)

    repo.add_event(
        db,
        run.id,
        "answer",
        question_id=question_id,
        stage=run.stage,
        actor_id=user.id,
        payload={"kind": kind, "request": request, "route": route},
    )
    if run.conversation_id is not None:
        conversation_service.append_workflow_message(
            db,
            run.conversation_id,
            role="user",
            author_id=user.id,
            content=summary,
            payload={"type": "workflow_answer", "question_id": str(question_id), "kind": kind, "action": action},
            client_message_id=uuid.uuid5(_ANSWER_NS, str(question_id)),
        )
    run.status, run.pending_question, run.pending_question_id = "queued", None, None
    run.row_version += 1
    run.updated_at = _now()
    _enqueue(db, settings, dispatch, user, run, {"command": "resume", "question_id": str(question_id)})
    db.refresh(run)
    return run, True


def _need_version(expected: int | None) -> int:
    if expected is None:
        raise ValidationFailed(
            "expected_row_version is required.", details=[{"field": "expected_row_version", "issue": "required"}]
        )
    return expected


def _apply(
    db: Session,
    user: User,
    project: Project,
    run: WorkflowRun,
    q: dict[str, Any],
    action: str,
    values: dict[str, Any],
    expected: int | None,
    route: dict[str, Any],
) -> str:
    """Apply one answer to the protocol draft (via protocol_service). Returns the chat summary of the answer."""
    kind = q["kind"]

    def patch(p: dict[str, Any], rationale: str | None = None) -> None:
        protocol_service.update_draft(
            db, user, project, expected_row_version=_need_version(expected), patch=p, framework_rationale=rationale
        )

    if kind == "review":
        if action == "approve":
            pv = protocol_service.approve(
                db,
                user,
                project,
                version_id=run.protocol_version_id or uuid.uuid4(),
                expected_row_version=_need_version(expected),
            )
            earliest = (pv.impact or {}).get("earliest_stage")
            nxt = stages.backtrack_target(run.stage_before_revision or "SEARCH", earliest)
            if stages.index(nxt) < stages.index("SEARCH"):
                nxt = "SEARCH"
            route.update(protocol_version_id=str(pv.id), next_stage=nxt)
            run.stage_before_revision = None
            return f"Approved protocol version {pv.version}."
        section = values.get("section")
        if section not in qs.EDITABLE_SECTIONS:
            raise ValidationFailed(
                "Choose a section to edit.",
                details=[{"field": "values.section", "issue": "invalid", "allowed": list(qs.EDITABLE_SECTIONS)}],
            )
        route["section"] = section
        return f"Edit the {SECTION_LABELS[section]}."

    if action == "skip":
        if kind == "framework":
            patch({"framework": "none", "framework_source": "skipped"})
            route["framework"] = "none"
            return "No question framework (not applicable)."
        return "Skipped for now."

    if kind == "objective":
        patch({"objective": values.get("objective")})
        return str(values.get("objective") or "")
    if kind == "framework":
        if action == "accept":
            sug = (q.get("data") or {}).get("suggestion") or {}
            fw = sug.get("framework")
            if fw not in FRAMEWORKS:
                raise ValidationFailed("There is no suggestion to accept.", details=[{"field": "action"}])
            patch({"framework": fw, "framework_source": "suggested"}, rationale=sug.get("rationale"))
            route["framework"] = fw
            return f"Use {FRAMEWORKS[fw].label} (the suggested framework)."
        fw = values.get("framework")
        patch({"framework": fw, "framework_source": "user"})
        route["framework"] = fw
        return f"Use {FRAMEWORKS[str(fw)].label}." if fw in FRAMEWORKS else "Framework chosen."
    if kind == "elements":
        patch({"framework_elements": values.get("framework_elements", [])})
        filled = [e for e in values.get("framework_elements", []) if isinstance(e, dict) and e.get("value")]
        return "\n".join(f"{e.get('label') or e.get('key')}: {e['value']}" for e in filled) or "No elements filled in."
    if kind == "questions":
        patch({"questions": values.get("questions", [])})
        items = [x.get("text", "") for x in values.get("questions", []) if isinstance(x, dict)]
        return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items)) or "No questions."
    if kind == "scope":
        patch({k: values[k] for k in SCOPE_FIELDS if k in values})
        return "Scope saved."
    if kind == "criteria":
        patch({"criteria": values.get("criteria", [])})
        crit = [x for x in values.get("criteria", []) if isinstance(x, dict)]
        counts = {
            k: sum(1 for x in crit if x.get("kind") == k and x.get("state", "accepted") == "accepted")
            for k in CRITERION_KINDS
        }
        rejected = sum(1 for x in crit if x.get("state") == "rejected")
        undecided = sum(1 for x in crit if x.get("state") == "proposed" and x.get("origin") == "suggested")
        return (
            f"Criteria saved: {counts['inclusion']} inclusion and {counts['exclusion']} exclusion accepted, "
            f"{rejected} rejected, {undecided} undecided."
        )
    raise ValidationFailed(f"Unknown question kind {kind!r}.")


# ---------- revise (new protocol version after approval) ----------


def revise(
    db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project, *, reason: str
) -> WorkflowRun:
    run = require(db, project, lock=True)
    if run.status != "idle":
        raise Conflict("Finish or restart the current workflow step before revising.", code="workflow_busy")
    revised_from = run.protocol_version_id
    pv = protocol_service.start_revision(db, user, project, reason=reason)
    run.stage_before_revision = run.stage
    run.stage, run.status, run.protocol_version_id = "PROTOCOL_REVIEW", "queued", pv.id
    run.row_version += 1
    run.updated_at = _now()
    repo.add_event(
        db,
        run.id,
        "revision",
        stage="PROTOCOL_REVIEW",
        actor_id=user.id,
        payload={"protocol_version_id": str(pv.id), "version": pv.version, "from_stage": run.stage_before_revision},
    )
    if run.conversation_id is not None:
        conversation_service.append_workflow_message(
            db,
            run.conversation_id,
            role="user",
            author_id=user.id,
            content=f"Revise the protocol (version {pv.version}). Reason: {pv.change_reason}",
            payload={"type": "workflow_revision", "protocol_version_id": str(pv.id)},
            client_message_id=uuid.uuid5(_ANSWER_NS, f"revision:{pv.id}"),
        )
    # The checkpoint still names the version being revised; the worker checks it against that, not the new draft.
    _enqueue(
        db,
        settings,
        dispatch,
        user,
        run,
        {"command": "revise", "protocol_version_id": str(pv.id), "checkpoint_version_id": str(revised_from)},
    )
    db.refresh(run)
    return run


# ---------- recover (tables win) ----------


def recover(db: Session, settings: Settings, dispatch: Dispatcher, user: User, project: Project) -> WorkflowRun:
    """Start a fresh checkpoint thread whose position is derived from the tables. Only for failed runs and runs
    that need attention; earlier checkpoints are left untouched (not deleted) for inspection."""
    protocol_service.require_writable(project)
    run = require(db, project, lock=True)
    if run.status not in ("failed", "needs_attention"):
        raise Conflict("Only a stopped or inconsistent workflow can be restarted.", code="not_recoverable")
    draft = protocol_service.draft(db, project)
    approved = protocol_service.approved(db, project)
    if draft is not None:
        mode, stage, pv_id = "rebuild_review", "PROTOCOL_REVIEW", draft.id
    elif approved is not None:
        mode = "rebuild_done"
        stage = run.stage if stages.index(run.stage) >= stages.index("SEARCH") else "SEARCH"
        pv_id = approved.id
    else:
        pv = protocol_service.create_first_draft(db, user, project)
        mode, stage, pv_id = "start", "DRAFT", pv.id
    old_thread = run.thread_id
    run.thread_id = uuid.uuid4()
    run.stage, run.status, run.protocol_version_id = stage, "queued", pv_id
    run.iteration_count, run.stop_reason, run.error_summary = 0, None, None
    run.pending_question, run.pending_question_id = None, None
    run.row_version += 1
    run.updated_at = _now()
    if project.status == "needs_attention":
        project.status = "setup"
    repo.add_event(
        db,
        run.id,
        "recovered",
        stage=stage,
        actor_id=user.id,
        payload={"old_thread_id": str(old_thread), "new_thread_id": str(run.thread_id), "mode": mode},
    )
    audit_service.record(
        db,
        action="workflow.recovered",
        entity_type="workflow_run",
        entity_id=run.id,
        actor_id=user.id,
        project_id=project.id,
        after={"mode": mode},
    )
    _enqueue(db, settings, dispatch, user, run, {"command": "rebuild", "mode": mode, "thread": str(run.thread_id)})
    db.refresh(run)
    return run


# ---------- worker side ----------


def _enrich(db: Session, run: WorkflowRun, q: dict[str, Any]) -> dict[str, Any]:
    """Add the data a question card needs (framework suggestion, proposed criteria...). Read-only."""
    project = db.get(Project, run.project_id)
    pv = protocol_service.draft(db, project) if project else None
    if project is None or pv is None:
        return q
    c = protocol_service.to_content(pv)
    data: dict[str, Any] = {}
    if q["kind"] == "framework":
        s = KeywordFrameworkAdvisor().suggest(f"{project.topic_original}\n{c.get('objective') or ''}")
        data["suggestion"] = {
            "framework": s.framework,
            "label": FRAMEWORKS[s.framework].label,
            "rationale": s.rationale,
            "matched_terms": list(s.matched_terms),
            "method": s.method,
            "method_note": "Rule-based keyword match on your own wording. Not an AI judgement.",
        }
        data["options"] = [{"key": f.key, "label": f.label, "description": f.description} for f in FRAMEWORKS.values()]
    elif q["kind"] == "elements":
        fw = c.get("framework")
        if fw in FRAMEWORKS:
            data["elements"] = [{"key": e.key, "label": e.label, "help": e.help} for e in FRAMEWORKS[fw].elements]
    elif q["kind"] == "questions":
        data["suggested_question"] = propose_question(c)
    elif q["kind"] == "criteria":
        data["suggestions"] = propose_criteria(c, run.id)
    return {**q, "data": data}


def sync_from_checkpoint(
    db: Session, run: WorkflowRun, *, values: dict[str, Any], interrupts: list[dict[str, Any]]
) -> None:
    if interrupts:
        q = _enrich(db, run, interrupts[0])
        qid = uuid.UUID(q["id"])
        run.status, run.pending_question, run.pending_question_id = "awaiting_input", q, qid
        run.stage = q["stage"]
        repo.add_event(db, run.id, "question", question_id=qid, stage=q["stage"], payload={"kind": q["kind"]})
        if run.conversation_id is not None:
            conversation_service.append_workflow_message(
                db,
                run.conversation_id,
                role="assistant",
                author_id=None,
                content=q["prompt"],
                payload={"type": "workflow_question", "question_id": q["id"], "kind": q["kind"]},
                client_message_id=qid,
            )
    else:
        run.pending_question, run.pending_question_id = None, None
        stage = str(values.get("stage") or run.stage)
        if values.get("stop_reason"):
            run.status, run.stop_reason = "failed", str(values["stop_reason"])
            run.error_summary = (
                f"Stopped after {values.get('iteration_count', 0)} answers without reaching approval "
                f"(limit {values.get('max_iterations')}). Restart the workflow to continue from the saved protocol."
            )
            repo.add_event(db, run.id, "error", stage=stage, payload={"stop_reason": run.stop_reason})
        else:
            if stage != run.stage:
                repo.add_event(db, run.id, "stage", stage=stage, payload={"from": run.stage})
            run.status, run.stage = "idle", stage
    run.iteration_count = int(values.get("iteration_count", run.iteration_count) or 0)
    run.row_version += 1
    run.updated_at = _now()


def mark_needs_attention(db: Session, run: WorkflowRun, summary: str) -> None:
    run.status, run.error_summary, run.pending_question, run.pending_question_id = (
        "needs_attention",
        summary,
        None,
        None,
    )
    run.row_version += 1
    run.updated_at = _now()
    project = db.get(Project, run.project_id)
    if project is not None:
        project.status = "needs_attention"
    repo.add_event(db, run.id, "needs_attention", stage=run.stage, payload={"summary": summary})


def fail(db: Session, run_id: uuid.UUID, summary: str) -> None:
    run = repo.get_by_id(db, run_id, lock=True)
    if run is None:
        return
    run.status, run.error_summary, run.pending_question, run.pending_question_id = "failed", summary, None, None
    run.stop_reason = "error"
    run.row_version += 1
    run.updated_at = _now()
    repo.add_event(db, run.id, "error", stage=run.stage, payload={"summary": summary})


def stage_labels() -> dict[str, str]:
    return dict(stages.STAGE_LABELS)


def field_labels() -> dict[str, str]:
    return dict(FIELD_LABELS)
