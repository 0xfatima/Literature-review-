"""Title/abstract screening: human decisions on discovered papers, with relevance shown alongside.

Rules enforced here (and, for immutability, by the database trigger on screening_decisions):
  * only a person decides: AI output is shown as a suggestion and is never written as a decision;
  * every decision records reviewer, time, reason, note, the approved protocol version and the assessments that were
    on screen; it is a new row, never an edit (revisions and undo append and supersede);
  * `screening_states` points at the current decision, with optimistic concurrency for single decisions;
  * undo is offered for a batch only where it is safe: a decision that has since been changed is left alone;
  * the first decision moves the workflow from SEARCH to RECORD_SCREENING and the project to "screening".
Audit events carry ids, decisions and reason codes only (never the note text).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import PaperRecord, Project, ProtocolVersion, RelevanceAssessment, ScreeningDecision, User
from app.protocol import stages
from app.repositories import screening as repo
from app.repositories import workflows as wf_repo
from app.screening import decisions as rules
from app.screening.relevance import BAND_LABELS, BANDS
from app.services import audit_service, protocol_service, workflow_service

STAGE = "title_abstract"
MAX_BULK = 200
SORTS = ("relevance", "relevance_asc", "year", "title", "recent_decision")
DECISION_FILTERS = ("undecided", "include", "exclude", "uncertain")
SUGGESTION_FILTERS = ("include", "exclude", "uncertain", "none")
DECISION_LABELS = {"include": "Include", "exclude": "Exclude", "uncertain": "Uncertain", "cleared": "No decision"}


def _now() -> datetime:
    return datetime.now(UTC)


def approved_or_fail(db: Session, project: Project) -> ProtocolVersion:
    pv = protocol_service.approved(db, project)
    if pv is None:
        raise Conflict("Approve the review protocol before screening.", code="protocol_not_approved")
    run = workflow_service.get(db, project)
    # During a revision the run is back at PROTOCOL_REVIEW; screening stays readable (writes are refused by
    # _require_quiet with a clearer message), so judge the stage the run will return to.
    reached = run is not None and max(
        stages.index(run.stage), stages.index(run.stage_before_revision) if run.stage_before_revision else -1
    ) >= stages.index("SEARCH")
    if not reached:
        raise Conflict("The workflow has not reached screening yet.", code="not_at_screening_stage")
    return pv


def _require_quiet(db: Session, project: Project) -> None:
    if protocol_service.draft(db, project) is not None:
        raise Conflict(
            "A protocol revision is in progress. Approve it (or finish it) before recording screening decisions.",
            code="workflow_busy",
        )


def reasons(db: Session, project: Project) -> list[rules.Reason]:
    return rules.reason_options(protocol_service.to_content(approved_or_fail(db, project)))


# ---------- views ----------


def _assessment_view(a: RelevanceAssessment | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "method": a.method,
        "method_version": a.method_version,
        "score": a.score,
        "band": a.band,
        "band_label": BAND_LABELS[a.band],
        "confidence": a.confidence,
        "explanation": a.explanation,
        "eligibility_suggestion": a.eligibility_suggestion,
        "suggestion_detail": a.suggestion_detail,
        "warnings": a.warnings,
        "model_run_id": a.model_run_id,
        "created_at": a.created_at,
    }


def _decision_view(d: ScreeningDecision) -> dict[str, Any]:
    return {
        "id": d.id,
        "decision": d.decision,
        "decision_label": DECISION_LABELS[d.decision],
        "reason_code": d.reason_code,
        "reason_label": d.reason_label,
        "note": d.note,
        "reviewer_id": d.reviewer_id,
        "action": d.action,
        "batch_id": d.batch_id,
        "supersedes_id": d.supersedes_id,
        "protocol_version_id": d.protocol_version_id,
        "assessment_ids": d.assessment_ids,
        "created_at": d.created_at,
    }


def _item(p: PaperRecord, st: Any, lex: Any, ai: Any, pv_id: uuid.UUID) -> dict[str, Any]:
    effective = ai or lex
    band = effective.band if effective else "not_assessed"
    current = st if st is not None and st.decision != "cleared" else None
    return {
        "paper_id": p.id,
        "title": p.title,
        "authors": [a.get("name") for a in (p.authors or []) if a.get("name")][:12],
        "author_count": len(p.authors or []),
        "year": p.year,
        "venue": p.venue,
        "publication_type": p.publication_type,
        "abstract": p.abstract,
        "keywords": (p.keywords or [])[:15],
        "doi": p.doi,
        "providers": p.providers,
        "is_preprint": p.is_preprint,
        "is_retracted": p.is_retracted,
        "access_status": p.access_status,
        "possible_duplicate_of_id": p.possible_duplicate_of_id,
        "band": band,
        "band_label": BAND_LABELS[band],
        "band_source": "ai" if ai else "lexical" if lex else None,
        "lexical": _assessment_view(lex),
        "ai": _assessment_view(ai),
        "decision": current.decision if current else None,
        "decision_label": DECISION_LABELS[current.decision] if current else "No decision",
        "decision_id": current.decision_id if current else None,
        "decision_protocol_version_id": current.protocol_version_id if current else None,
        "decision_is_current_protocol": (current.protocol_version_id == pv_id) if current else None,
        "state_row_version": st.row_version if st is not None else None,
        "decided_at": st.updated_at if current else None,
    }


def queue(
    db: Session,
    project: Project,
    *,
    decision: str | None,
    band: str | None,
    suggestion: str | None,
    text: str | None,
    flagged: bool | None,
    sort: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    pv = approved_or_fail(db, project)
    decision, band, suggestion = decision or None, band or None, suggestion or None  # "" means "no filter"
    if decision is not None and decision not in DECISION_FILTERS:
        raise ValidationFailed(
            "Unknown decision filter.", details=[{"field": "decision", "allowed": list(DECISION_FILTERS)}]
        )
    if band is not None and band not in BANDS:
        raise ValidationFailed("Unknown relevance band.", details=[{"field": "band", "allowed": list(BANDS)}])
    if suggestion is not None and suggestion not in SUGGESTION_FILTERS:
        raise ValidationFailed("Unknown suggestion filter.", details=[{"field": "suggestion"}])
    if sort not in SORTS:
        raise ValidationFailed("Unknown sort.", details=[{"field": "sort", "allowed": list(SORTS)}])
    rows, total = repo.queue(
        db,
        project.id,
        pv.id,
        decision=decision,
        band=band,
        suggestion=suggestion,
        text=text or None,
        flagged=flagged,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [_item(p, st, lex, ai, pv.id) for p, st, lex, ai in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "progress": repo.progress(db, project.id),
        "protocol_version_id": pv.id,
        "protocol_version": pv.version,
    }


def paper_history(db: Session, project: Project, paper_id: uuid.UUID) -> dict[str, Any]:
    if not repo.papers_in_project(db, project.id, [paper_id]):  # 404 before anything else: ids are per project
        raise NotFound("Paper not found.")
    pv = approved_or_fail(db, project)
    return {
        "paper_id": paper_id,
        "decisions": [_decision_view(d) for d in repo.history(db, paper_id)],
        "assessments": [_assessment_view(a) for a in repo.assessments_for(db, paper_id, pv.id)],
    }


# ---------- writes ----------


def _advance_stage(db: Session, project: Project, user: User) -> None:
    """First decision: SEARCH -> RECORD_SCREENING (workflow) and searching -> screening (project)."""
    run = workflow_service.get(db, project)
    if run is not None and run.status == "idle" and run.stage == "SEARCH":
        run.stage = "RECORD_SCREENING"
        run.row_version += 1
        run.updated_at = _now()
        wf_repo.add_event(db, run.id, "stage", stage="RECORD_SCREENING", actor_id=user.id, payload={"from": "SEARCH"})
    if project.status in ("setup", "searching"):
        project.status = "screening"
        project.row_version += 1


def _append(
    db: Session,
    project: Project,
    user: User,
    pv: ProtocolVersion,
    paper_id: uuid.UUID,
    decision: str,
    reason: tuple[str | None, str | None, str | None],
    *,
    action: str,
    batch_id: uuid.UUID | None,
) -> ScreeningDecision:
    st = repo.state_for(db, paper_id, lock=True)
    shown = [str(a.id) for a in repo.assessments_for(db, paper_id, pv.id)]
    d = ScreeningDecision(
        project_id=project.id,
        paper_id=paper_id,
        protocol_version_id=pv.id,
        stage=STAGE,
        decision=decision,
        reason_code=reason[0],
        reason_label=reason[1],
        note=reason[2],
        reviewer_id=user.id,
        action=action,
        batch_id=batch_id,
        supersedes_id=st.decision_id if st is not None else None,
        assessment_ids=shown,
    )
    db.add(d)
    db.flush()
    if st is None:
        from app.db.models import ScreeningState

        db.add(
            ScreeningState(
                project_id=project.id,
                paper_id=paper_id,
                stage=STAGE,
                decision=decision,
                decision_id=d.id,
                protocol_version_id=pv.id,
            )
        )
    else:
        st.decision, st.decision_id, st.protocol_version_id = decision, d.id, pv.id
        st.row_version += 1
        st.updated_at = _now()
    db.flush()
    return d


def _check(
    settings: Settings, decision: str, reason_code: str | None, note: str | None, options: list[rules.Reason]
) -> tuple[str | None, str | None, str | None]:
    try:
        return rules.check(
            decision, reason_code, note, options, require_reason=settings.screening_ta_require_exclusion_reason
        )
    except rules.DecisionError as e:
        raise ValidationFailed(str(e), details=[{"field": e.field, "issue": "invalid"}]) from None


def decide(
    db: Session,
    settings: Settings,
    user: User,
    project: Project,
    paper_id: uuid.UUID,
    *,
    decision: str,
    reason_code: str | None,
    note: str | None,
    expected_row_version: int | None,
) -> dict[str, Any]:
    if not repo.papers_in_project(db, project.id, [paper_id]):  # 404 before anything else: ids are per project
        raise NotFound("Paper not found.")
    protocol_service.require_writable(project)
    pv = approved_or_fail(db, project)
    _require_quiet(db, project)
    reason = _check(settings, decision, reason_code, note, rules.reason_options(protocol_service.to_content(pv)))
    st = repo.state_for(db, paper_id, lock=True)
    current = st.row_version if st is not None else None
    if expected_row_version is not None and expected_row_version != (current or 0):
        raise Conflict(
            "This record's decision was changed elsewhere. Reload to see the latest decision.", code="version_conflict"
        )
    if st is not None and st.decision == decision and decision != "exclude":
        db.rollback()
        return {"decision_id": st.decision_id, "unchanged": True}
    d = _append(db, project, user, pv, paper_id, decision, reason, action="decide", batch_id=None)
    _advance_stage(db, project, user)
    audit_service.record(
        db,
        action="screening.decided",
        entity_type="paper_record",
        entity_id=paper_id,
        actor_id=user.id,
        project_id=project.id,
        before={"decision": st.decision if st else None} if st else None,
        after={"decision": decision, "reason_code": reason[0], "stage": STAGE, "protocol_version": pv.version},
    )
    db.commit()
    return {"decision_id": d.id, "unchanged": False}


def bulk(
    db: Session,
    settings: Settings,
    user: User,
    project: Project,
    paper_ids: list[uuid.UUID],
    *,
    decision: str,
    reason_code: str | None,
    note: str | None,
) -> dict[str, Any]:
    protocol_service.require_writable(project)
    pv = approved_or_fail(db, project)
    _require_quiet(db, project)
    ids = list(dict.fromkeys(paper_ids))
    if not ids or len(ids) > MAX_BULK:
        raise ValidationFailed(f"Select 1-{MAX_BULK} records.", details=[{"field": "paper_ids", "issue": "count"}])
    found = {p.id for p in repo.papers_in_project(db, project.id, ids)}
    if len(found) != len(ids):
        raise NotFound("One or more selected records were not found in this project.")
    reason = _check(settings, decision, reason_code, note, rules.reason_options(protocol_service.to_content(pv)))
    batch_id = uuid.uuid4()
    changed = 0
    for pid in ids:
        st = repo.state_for(db, pid)
        if st is not None and st.decision == decision and decision != "exclude":
            continue
        _append(db, project, user, pv, pid, decision, reason, action="bulk", batch_id=batch_id)
        changed += 1
    if changed:
        _advance_stage(db, project, user)
        audit_service.record(
            db,
            action="screening.bulk_decided",
            entity_type="screening_batch",
            entity_id=batch_id,
            actor_id=user.id,
            project_id=project.id,
            after={"decision": decision, "reason_code": reason[0], "count": changed, "protocol_version": pv.version},
        )
    db.commit()
    return {"batch_id": batch_id if changed else None, "changed": changed, "unchanged": len(ids) - changed}


def undo(
    db: Session,
    user: User,
    project: Project,
    *,
    batch_id: uuid.UUID | None,
    decision_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Restore the decision each record had before the batch (or the single decision). Safe only where the record
    still has that decision: anything changed since is left alone and reported."""
    protocol_service.require_writable(project)
    pv = approved_or_fail(db, project)
    _require_quiet(db, project)
    if (batch_id is None) == (decision_id is None):
        raise ValidationFailed("Give either a batch or a decision to undo.", details=[{"field": "batch_id"}])
    if batch_id is not None:
        targets = repo.batch(db, project.id, batch_id)
    else:
        one = repo.decision(db, project.id, decision_id)  # type: ignore[arg-type]
        targets = [one] if one is not None else []
    if not targets:
        raise NotFound("Nothing to undo.")
    undo_batch = uuid.uuid4()
    undone = skipped = 0
    for d in targets:
        st = repo.state_for(db, d.paper_id, lock=True)
        if st is None or st.decision_id != d.id:
            skipped += 1
            continue
        prev = repo.decision(db, project.id, d.supersedes_id) if d.supersedes_id else None
        if prev is not None and prev.decision != "cleared":
            restored = (prev.decision, (prev.reason_code, prev.reason_label, prev.note))
        else:
            restored = ("cleared", (None, None, None))
        _append(db, project, user, pv, d.paper_id, restored[0], restored[1], action="undo", batch_id=undo_batch)
        undone += 1
    if undone:
        audit_service.record(
            db,
            action="screening.undone",
            entity_type="screening_batch",
            entity_id=batch_id or decision_id,
            actor_id=user.id,
            project_id=project.id,
            after={"undone": undone, "skipped": skipped, "undo_batch_id": str(undo_batch)},
        )
    db.commit()
    return {"undone": undone, "skipped_changed_since": skipped, "batch_id": undo_batch if undone else None}
