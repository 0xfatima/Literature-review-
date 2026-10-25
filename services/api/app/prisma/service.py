"""PRISMA flow service: compute, validate and return the flow for a project.

The flow is derived entirely from record_status_events (current rows only). No counts are typed in
by a person or produced by a model. The service also manages:
  - emitting events when a screening decision is made (via hooks called from screening_service),
  - corrections (new event supersedes old),
  - study grouping (paper → study),
  - full-text screening decisions.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import (
    PaperRecord,
    ProtocolVersion,
    RecordStatusEvent,
    ScreeningDecision,
    ScreeningState,
    Study,
)
from app.prisma.flow import PrismaFlow, compute
from app.services import audit_service


def _now() -> datetime:
    return datetime.now(UTC)


def _approved(db: Session, project_id: uuid.UUID) -> ProtocolVersion | None:
    from app.db.models import Project
    from app.services import protocol_service as ps

    proj = db.get(Project, project_id)
    return ps.approved(db, proj) if proj else None


# ---------- event emission ----------


def _pv_id(pv: ProtocolVersion) -> uuid.UUID:
    return pv.id


def emit_event(
    db: Session,
    *,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    protocol_version_id: uuid.UUID,
    pathway: str,
    stage: str,
    actor_id: uuid.UUID | None = None,
    reason_code: str | None = None,
    reason_label: str | None = None,
    note: str | None = None,
    source_tag: str | None = None,
    supersedes_id: uuid.UUID | None = None,
    screening_decision_id: uuid.UUID | None = None,
) -> RecordStatusEvent:
    """Add one record_status_event. Superseding is tracked via supersedes_id chain only."""
    ev = RecordStatusEvent(
        id=uuid.uuid4(),
        project_id=project_id,
        paper_id=paper_id,
        protocol_version_id=protocol_version_id,
        pathway=pathway,
        stage=stage,
        reason_code=reason_code,
        reason_label=reason_label,
        note=note,
        source_tag=source_tag,
        actor_id=actor_id,
        supersedes_id=supersedes_id,
        screening_decision_id=screening_decision_id,
    )
    db.add(ev)
    return ev


def sync_from_screening_decision(
    db: Session,
    decision: ScreeningDecision,
    pv: ProtocolVersion,
    *,
    supersedes_paper_event: bool = True,
) -> None:
    """Create or update the record_status_event for a paper based on a finalised screening decision.
    Called by screening_service after committing a decision."""
    stage_map = {
        ("title_abstract", "include"): "ta_included",
        ("title_abstract", "exclude"): "ta_excluded",
        ("full_text", "include"): "included",
        ("full_text", "exclude"): "ft_excluded",
    }
    stage = stage_map.get((decision.stage, decision.decision))
    if stage is None:
        return  # uncertain / cleared / undo → no PRISMA event (the paper is still pending)

    # Find the current event for this paper to supersede it
    # "Current" = not yet superseded
    superseded_ids = (
        select(RecordStatusEvent.supersedes_id)
        .where(
            RecordStatusEvent.project_id == decision.project_id,
            RecordStatusEvent.supersedes_id.is_not(None),
        )
        .scalar_subquery()
    )
    old = db.scalar(
        select(RecordStatusEvent)
        .where(
            RecordStatusEvent.paper_id == decision.paper_id,
            RecordStatusEvent.project_id == decision.project_id,
            RecordStatusEvent.id.not_in(superseded_ids),
        )
        .order_by(RecordStatusEvent.created_at.desc())
    )
    emit_event(
        db,
        project_id=decision.project_id,
        paper_id=decision.paper_id,
        protocol_version_id=pv.id,
        pathway="A",
        stage=stage,
        actor_id=decision.reviewer_id,
        reason_code=decision.reason_code,
        reason_label=decision.reason_label,
        note=decision.note,
        supersedes_id=old.id if (old and supersedes_paper_event) else None,
        screening_decision_id=decision.id,
    )


def seed_identified_events(
    db: Session,
    project_id: uuid.UUID,
    pv_id: uuid.UUID,
    paper_ids: Sequence[uuid.UUID],
    *,
    source_tag: str = "database",
) -> int:
    """Emit 'identified' events for papers that don't have one yet (idempotent)."""
    existing = set(
        db.scalars(
            select(RecordStatusEvent.paper_id).where(
                RecordStatusEvent.project_id == project_id,
                RecordStatusEvent.stage == "identified",
            )
        )
    )
    count = 0
    for pid in paper_ids:
        if pid not in existing:
            emit_event(
                db,
                project_id=project_id,
                paper_id=pid,
                protocol_version_id=pv_id,
                pathway="A",
                stage="identified",
                source_tag=source_tag,
            )
            count += 1
    return count


# ---------- flow retrieval ----------


def get_flow(db: Session, project_id: uuid.UUID, protocol_version_id: uuid.UUID | None = None) -> PrismaFlow:
    """Compute the PRISMA flow for a project from current (not superseded) events.

    "Current" = any event whose id does not appear as the supersedes_id of a later event.
    Events are ordered by created_at ASC so compute() receives each paper's journey in order.
    """
    superseded = (
        select(RecordStatusEvent.supersedes_id)
        .where(
            RecordStatusEvent.project_id == project_id,
            RecordStatusEvent.supersedes_id.is_not(None),
        )
        .scalar_subquery()
    )
    q = (
        select(
            RecordStatusEvent.paper_id,
            RecordStatusEvent.stage,
            RecordStatusEvent.pathway,
            RecordStatusEvent.source_tag,
            RecordStatusEvent.reason_code,
            PaperRecord.study_id,
        )
        .join(PaperRecord, PaperRecord.id == RecordStatusEvent.paper_id)
        .where(
            RecordStatusEvent.project_id == project_id,
            RecordStatusEvent.id.not_in(superseded),
        )
        .order_by(RecordStatusEvent.created_at)
    )

    if protocol_version_id:
        q = q.where(RecordStatusEvent.protocol_version_id == protocol_version_id)

    rows = db.execute(q).mappings().all()
    raw: list[dict[str, Any]] = [dict(r) for r in rows]
    flow = compute(raw)
    flow.protocol_version_id = str(protocol_version_id) if protocol_version_id else None
    return flow


def flow_view(flow: PrismaFlow) -> dict[str, Any]:
    return {
        "identified_a": flow.identified_a,
        "identified_a_by_source": flow.identified_a_by_source,
        "removed_duplicates": flow.removed_duplicates,
        "removed_automation": flow.removed_automation,
        "removed_other": flow.removed_other,
        "screened": flow.screened,
        "pending_ta": flow.pending_ta,
        "ta_excluded": flow.ta_excluded,
        "ta_included_a": flow.ta_included_a,
        "identified_b": flow.identified_b,
        "identified_b_by_source": flow.identified_b_by_source,
        "ta_included_b": flow.ta_included_b,
        "sought_retrieval": flow.sought_retrieval,
        "pending_retrieval": flow.pending_retrieval,
        "not_retrieved": flow.not_retrieved,
        "ft_assessed": flow.ft_assessed,
        "pending_ft": flow.pending_ft,
        "ft_excluded": flow.ft_excluded,
        "ft_excluded_by_reason": flow.ft_excluded_by_reason,
        "included_reports": flow.included_reports,
        "included_studies": flow.included_studies,
        "violations": flow.violations,
        "is_final": flow.is_final,
        "protocol_version_id": flow.protocol_version_id,
    }


# ---------- full-text screening ----------


FT_STAGE = "full_text"


def ft_reason_options(pv: ProtocolVersion) -> list[dict[str, Any]]:
    """Full-text exclusion reasons: protocol criteria + fixed methodological reasons."""
    from app.services import protocol_service as _ps

    content = _ps.to_content(pv)
    criteria = content.get("criteria") or []
    out: list[dict[str, Any]] = []
    for c in criteria:
        if c.get("state", "accepted") != "accepted":
            continue
        if c.get("kind") == "exclusion":
            out.append(
                {
                    "code": f"exclusion:{c['stable_key']}",
                    "label": f"Meets exclusion: {c['text']}",
                    "group": "exclusion_criterion",
                }
            )
        elif c.get("kind") == "inclusion":
            out.append(
                {
                    "code": f"inclusion:{c['stable_key']}",
                    "label": f"Does not meet inclusion: {c['text']}",
                    "group": "inclusion_not_met",
                }
            )
    # Fixed methodological reasons (PRISMA: reason per excluded full text is required)
    for code, label in [
        ("wrong_study_design", "Wrong study design"),
        ("wrong_population", "Wrong population"),
        ("wrong_intervention", "Wrong intervention or exposure"),
        ("wrong_comparator", "Wrong comparator"),
        ("wrong_outcome", "Wrong outcome or measure"),
        ("wrong_publication_type", "Wrong publication type"),
        ("duplicate_report", "Duplicate report of an included study"),
        ("other", "Other (explain in the note)"),
    ]:
        out.append({"code": code, "label": label, "group": "generic"})
    return out


def ft_decide(
    db: Session,
    *,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user_id: uuid.UUID,
    decision: str,
    reason_code: str | None,
    note: str | None,
    expected_row_version: int | None,
) -> ScreeningDecision:
    """Record a full-text screening decision. Exclusion at full_text always requires a reason_code."""
    from app.db.models import Project
    from app.services import protocol_service as ps

    proj = db.get(Project, project_id)
    if proj is None:
        raise NotFound("Project not found.")
    pv = ps.approved(db, proj)
    if pv is None:
        raise Conflict("Approve the protocol before screening.", code="protocol_not_approved")

    paper = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project_id))
    if paper is None:
        raise NotFound("Paper not found.")

    if decision == "exclude" and not reason_code:
        raise ValidationFailed(
            "A reason is required for full-text exclusion.",
            details=[{"field": "reason_code", "issue": "required"}],
        )
    if decision not in ("include", "exclude", "uncertain"):
        raise ValidationFailed("Decision must be include, exclude or uncertain.")

    # optimistic concurrency via screening_states
    state = db.scalar(
        select(ScreeningState).where(ScreeningState.paper_id == paper_id, ScreeningState.stage == FT_STAGE)
    )
    if state and expected_row_version is not None and state.row_version != expected_row_version:
        raise Conflict("This record was changed elsewhere. Reload and try again.", code="version_conflict")

    # resolve reason label
    reason_label: str | None = None
    if reason_code:
        opts = {o["code"]: o["label"] for o in ft_reason_options(pv)}
        reason_label = opts.get(reason_code)
        if reason_label is None:
            raise ValidationFailed(f"Unknown reason code '{reason_code}'.")

    old_decision_id: uuid.UUID | None = state.decision_id if state else None
    new_dec = ScreeningDecision(
        id=uuid.uuid4(),
        project_id=project_id,
        paper_id=paper_id,
        protocol_version_id=pv.id,
        stage=FT_STAGE,
        decision=decision,
        reason_code=reason_code,
        reason_label=reason_label,
        note=note,
        reviewer_id=user_id,
        action="decide",
        supersedes_id=old_decision_id,
        assessment_ids=[],
    )
    db.add(new_dec)
    db.flush()

    if state:
        state.decision = decision
        state.decision_id = new_dec.id
        state.protocol_version_id = pv.id
        state.row_version += 1
        state.updated_at = _now()
    else:
        db.add(
            ScreeningState(
                id=uuid.uuid4(),
                project_id=project_id,
                paper_id=paper_id,
                stage=FT_STAGE,
                decision=decision,
                decision_id=new_dec.id,
                protocol_version_id=pv.id,
            )
        )

    sync_from_screening_decision(db, new_dec, pv)
    audit_service.record(
        db,
        action="full_text_screening.decided",
        entity_type="paper",
        entity_id=paper_id,
        actor_id=user_id,
        project_id=project_id,
        after={"decision": decision, "reason_code": reason_code},
    )
    db.commit()
    db.refresh(new_dec)
    return new_dec


# ---------- studies ----------


def group_into_study(
    db: Session,
    project_id: uuid.UUID,
    paper_ids: list[uuid.UUID],
    *,
    user_id: uuid.UUID,
    short_name: str | None = None,
    notes: str | None = None,
) -> Study:
    """Group a set of included paper records into one study. All must be included."""
    if not paper_ids:
        raise ValidationFailed("At least one paper is required.")
    papers = db.scalars(
        select(PaperRecord).where(
            PaperRecord.project_id == project_id,
            PaperRecord.id.in_(paper_ids),
        )
    ).all()
    if len(papers) != len(paper_ids):
        raise NotFound("One or more papers not found.")

    study = Study(id=uuid.uuid4(), project_id=project_id, short_name=short_name, notes=notes)
    db.add(study)
    db.flush()
    for p in papers:
        p.study_id = study.id
    audit_service.record(
        db,
        action="study.created",
        entity_type="study",
        entity_id=study.id,
        actor_id=user_id,
        project_id=project_id,
        after={"paper_count": len(paper_ids), "short_name": short_name},
    )
    db.commit()
    db.refresh(study)
    return study
