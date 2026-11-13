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
from app.services import audit_service, open_access


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


def _current_event(db: Session, project_id: uuid.UUID, paper_id: uuid.UUID) -> RecordStatusEvent | None:
    superseded_ids = (
        select(RecordStatusEvent.supersedes_id)
        .where(RecordStatusEvent.project_id == project_id, RecordStatusEvent.supersedes_id.is_not(None))
        .scalar_subquery()
    )
    return db.scalar(
        select(RecordStatusEvent)
        .where(
            RecordStatusEvent.paper_id == paper_id,
            RecordStatusEvent.project_id == project_id,
            RecordStatusEvent.id.not_in(superseded_ids),
        )
        .order_by(RecordStatusEvent.created_at.desc())
        .limit(1)
    )


TA_OUTCOMES = ("ta_included", "ta_excluded")


def reopen_title_abstract(db: Session, decision: ScreeningDecision, pv: ProtocolVersion) -> None:
    """A title/abstract decision changed to uncertain, or was undone: the paper is awaiting screening again. Only a
    current title/abstract outcome is reopened; later (full-text) outcomes are left alone."""
    old = _current_event(db, decision.project_id, decision.paper_id)
    if old is None or old.stage not in TA_OUTCOMES:
        return
    emit_event(
        db,
        project_id=decision.project_id,
        paper_id=decision.paper_id,
        protocol_version_id=pv.id,
        pathway=old.pathway,
        stage="identified",
        actor_id=decision.reviewer_id,
        source_tag=old.source_tag,
        supersedes_id=old.id,
        screening_decision_id=decision.id,
    )


def resync_title_abstract(db: Session, project_id: uuid.UUID) -> int:
    """Repair: make every paper's PRISMA title/abstract outcome match its current screening decision (decisions made
    in bulk or by the AI before this was kept in sync). Idempotent; never touches full-text outcomes. Returns the
    number of papers corrected. The caller commits."""
    from app.db.models import ScreeningDecision as Decision
    from app.db.models import ScreeningState

    rows = db.execute(
        select(ScreeningState, Decision)
        .join(Decision, Decision.id == ScreeningState.decision_id)
        .where(ScreeningState.project_id == project_id, ScreeningState.stage == "title_abstract")
    ).all()
    fixed = 0
    for st, d in rows:
        pv = db.get(ProtocolVersion, d.protocol_version_id)
        if pv is None:
            continue
        cur = _current_event(db, project_id, st.paper_id)
        stage = cur.stage if cur is not None else None
        want = {"include": "ta_included", "exclude": "ta_excluded"}.get(st.decision, "identified")
        if stage == want or (stage is not None and stage not in ("identified", *TA_OUTCOMES)):
            continue
        if want == "identified":
            if stage in TA_OUTCOMES:
                reopen_title_abstract(db, d, pv)
                fixed += 1
            continue
        sync_from_screening_decision(db, d, pv)
        fixed += 1
    db.flush()
    return fixed


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
    proj, pv = _ft_context(db, project_id)
    paper = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project_id))
    if paper is None:
        raise NotFound("Paper not found.")
    new_dec = _ft_record(
        db,
        pv,
        project_id=project_id,
        paper_id=paper_id,
        user_id=user_id,
        decision=decision,
        reason_code=reason_code,
        note=note,
        expected_row_version=expected_row_version,
    )
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


def _ft_context(db: Session, project_id: uuid.UUID) -> tuple[Any, ProtocolVersion]:
    from app.db.models import Project
    from app.services import protocol_service as ps

    proj = db.get(Project, project_id)
    if proj is None:
        raise NotFound("Project not found.")
    pv = ps.approved(db, proj)
    if pv is None:
        raise Conflict("Approve the protocol before screening.", code="protocol_not_approved")
    return proj, pv


def _ft_record(
    db: Session,
    pv: ProtocolVersion,
    *,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user_id: uuid.UUID,
    decision: str,
    reason_code: str | None,
    note: str | None,
    expected_row_version: int | None = None,
    action: str = "decide",
    batch_id: uuid.UUID | None = None,
) -> ScreeningDecision:
    """Append one full-text decision and keep the current-state row and the PRISMA flow in step. No commit."""
    if decision == "exclude" and not reason_code:
        raise ValidationFailed(
            "A reason is required for full-text exclusion.",
            details=[{"field": "reason_code", "issue": "required"}],
        )
    if decision not in ("include", "exclude", "uncertain"):
        raise ValidationFailed("Decision must be include, exclude or uncertain.")
    state = db.scalar(
        select(ScreeningState).where(ScreeningState.paper_id == paper_id, ScreeningState.stage == FT_STAGE)
    )
    if state and expected_row_version is not None and state.row_version != expected_row_version:
        raise Conflict("This record was changed elsewhere. Reload and try again.", code="version_conflict")
    reason_label: str | None = None
    if reason_code:
        opts = {o["code"]: o["label"] for o in ft_reason_options(pv)}
        reason_label = opts.get(reason_code)
        if reason_label is None:
            raise ValidationFailed(f"Unknown reason code '{reason_code}'.")
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
        action=action,
        batch_id=batch_id,
        supersedes_id=state.decision_id if state else None,
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
    db.flush()
    sync_from_screening_decision(db, new_dec, pv)
    return new_dec


FT_BULK_MAX = 1000


def ft_bulk(
    db: Session,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    paper_ids: list[uuid.UUID],
    decision: str,
    reason_code: str | None,
    note: str | None,
) -> dict[str, Any]:
    """One full-text decision for many reports at once (only reports in the full-text queue). Shares a batch id."""
    from app.services import protocol_service as ps

    proj, pv = _ft_context(db, project_id)
    ps.require_writable(proj)
    ids = list(dict.fromkeys(paper_ids))
    if not ids or len(ids) > FT_BULK_MAX:
        raise ValidationFailed(f"Select 1-{FT_BULK_MAX} reports.", details=[{"field": "paper_ids"}])
    in_queue = {i["id"] for i in ft_queue(db, project_id)["items"]}
    batch_id = uuid.uuid4()
    changed = 0
    for pid in ids:
        if str(pid) not in in_queue:
            continue
        _ft_record(
            db,
            pv,
            project_id=project_id,
            paper_id=pid,
            user_id=user_id,
            decision=decision,
            reason_code=reason_code,
            note=note,
            action="bulk",
            batch_id=batch_id,
        )
        changed += 1
    audit_service.record(
        db,
        action="full_text_screening.bulk_decided",
        entity_type="project",
        entity_id=project_id,
        actor_id=user_id,
        project_id=project_id,
        after={"decision": decision, "reason_code": reason_code, "count": changed, "batch_id": str(batch_id)},
    )
    db.commit()
    return {"batch_id": str(batch_id), "changed": changed}


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
    current = _current_stage_by_paper(db, project_id)
    not_included = [str(p.id) for p in papers if (ev := current.get(p.id)) is None or ev.stage != "included"]
    if not_included:
        raise ValidationFailed(
            "Only reports included at full text can be grouped into a study.",
            details=[{"field": "paper_ids", "issue": "not_included", "paper_ids": not_included}],
        )
    already = [str(p.id) for p in papers if p.study_id is not None]
    if already:
        raise Conflict(
            "A report is already part of a study. Remove that grouping first.",
            code="already_grouped",
            details=[{"paper_ids": already}],
        )

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


def ungroup_study(db: Session, project_id: uuid.UUID, study_id: uuid.UUID, *, user_id: uuid.UUID) -> None:
    """Dissolve a study grouping: its reports count as separate studies again. Nothing else changes."""
    study = db.scalar(select(Study).where(Study.id == study_id, Study.project_id == project_id))
    if study is None:
        raise NotFound("Study not found.")
    papers = db.scalars(
        select(PaperRecord).where(PaperRecord.project_id == project_id, PaperRecord.study_id == study_id)
    ).all()
    for p in papers:
        p.study_id = None
    db.flush()
    db.delete(study)
    audit_service.record(
        db,
        action="study.ungrouped",
        entity_type="study",
        entity_id=study_id,
        actor_id=user_id,
        project_id=project_id,
        after={"paper_count": len(papers)},
    )
    db.commit()


def list_studies(db: Session, project_id: uuid.UUID) -> dict[str, Any]:
    """Included reports and how they are grouped into studies. A report outside any group counts as its own
    study (the 1:1 assumption), which the response says explicitly."""
    current = _current_stage_by_paper(db, project_id)
    included_ids = [pid for pid, ev in current.items() if ev.stage == "included"]
    papers = db.scalars(select(PaperRecord).where(PaperRecord.id.in_(included_ids))).all() if included_ids else []
    studies = db.scalars(select(Study).where(Study.project_id == project_id).order_by(Study.created_at)).all()
    by_study: dict[uuid.UUID, list[dict[str, Any]]] = {s.id: [] for s in studies}
    ungrouped: list[dict[str, Any]] = []
    for p in sorted(papers, key=lambda x: (x.year or 0, x.title)):
        brief = _paper_brief(p)
        if p.study_id is not None and p.study_id in by_study:
            by_study[p.study_id].append(brief)
        else:
            ungrouped.append(brief)
    return {
        "studies": [
            {"id": str(s.id), "short_name": s.short_name, "notes": s.notes, "reports": by_study[s.id]} for s in studies
        ],
        "ungrouped_reports": ungrouped,
        "included_reports": len(papers),
        "included_studies": len([s for s in studies if by_study[s.id]]) + len(ungrouped),
    }


# ---------- full-text queue and retrieval ----------

FT_QUEUE_STATUSES = ("awaiting", "uncertain", "included", "excluded", "not_retrieved")


def _current_stage_by_paper(db: Session, project_id: uuid.UUID) -> dict[uuid.UUID, RecordStatusEvent]:
    """The current (not superseded) record_status_event of every paper in the project."""
    superseded = (
        select(RecordStatusEvent.supersedes_id)
        .where(RecordStatusEvent.project_id == project_id, RecordStatusEvent.supersedes_id.is_not(None))
        .scalar_subquery()
    )
    rows = db.scalars(
        select(RecordStatusEvent)
        .where(RecordStatusEvent.project_id == project_id, RecordStatusEvent.id.not_in(superseded))
        .order_by(RecordStatusEvent.created_at.asc())
    ).all()
    out: dict[uuid.UUID, RecordStatusEvent] = {}
    for ev in rows:  # later rows win: the last current event is where the paper is now
        out[ev.paper_id] = ev
    return out


def _paper_brief(p: PaperRecord) -> dict[str, Any]:
    first = (p.authors or [{}])[0] if p.authors else {}
    return {
        "id": str(p.id),
        "title": p.title,
        "year": p.year,
        "venue": p.venue,
        "doi": p.doi,
        "first_author": (first or {}).get("name") or (first or {}).get("family"),
        "author_count": len(p.authors or []),
        "full_text_status": p.full_text_status,
        "study_id": str(p.study_id) if p.study_id else None,
        **open_access.view(p),
    }


def ft_queue(db: Session, project_id: uuid.UUID, *, status: str | None = None) -> dict[str, Any]:
    """Records that passed title/abstract screening, with their full-text status. Derived from stored events and
    decisions only. Status: awaiting (no full-text outcome yet), uncertain, included, excluded, not_retrieved."""
    if status is not None and status not in FT_QUEUE_STATUSES:
        raise ValidationFailed("Unknown status filter.", details=[{"field": "status", "issue": "invalid"}])
    stage_to_status = {
        "ta_included": "awaiting",
        "not_retrieved": "not_retrieved",
        "included": "included",
        "ft_excluded": "excluded",
    }
    current = {pid: ev for pid, ev in _current_stage_by_paper(db, project_id).items() if ev.stage in stage_to_status}
    if not current:
        return {"items": [], "counts": dict.fromkeys(FT_QUEUE_STATUSES, 0)}
    papers = {p.id: p for p in db.scalars(select(PaperRecord).where(PaperRecord.id.in_(list(current)))).all()}
    states = {
        s.paper_id: s
        for s in db.scalars(
            select(ScreeningState).where(
                ScreeningState.project_id == project_id,
                ScreeningState.stage == FT_STAGE,
                ScreeningState.paper_id.in_(list(current)),
            )
        ).all()
    }
    decisions = (
        {
            d.id: d
            for d in db.scalars(
                select(ScreeningDecision).where(ScreeningDecision.id.in_([s.decision_id for s in states.values()]))
            ).all()
        }
        if states
        else {}
    )
    items: list[dict[str, Any]] = []
    counts = dict.fromkeys(FT_QUEUE_STATUSES, 0)
    for pid, ev in current.items():
        p = papers.get(pid)
        if p is None:
            continue
        st = states.get(pid)
        s = stage_to_status[ev.stage]
        if s == "awaiting" and st is not None and st.decision == "uncertain":
            s = "uncertain"
        counts[s] += 1
        if status is not None and s != status:
            continue
        dec = decisions.get(st.decision_id) if st else None
        items.append(
            {
                **_paper_brief(p),
                "status": s,
                "reason_code": ev.reason_code if ev.stage in ("ft_excluded", "not_retrieved") else None,
                "reason_label": ev.reason_label if ev.stage in ("ft_excluded", "not_retrieved") else None,
                "note": (dec.note if dec else None) if ev.stage != "not_retrieved" else ev.note,
                "row_version": st.row_version if st else None,
                "decided_at": ev.created_at.isoformat() if s not in ("awaiting", "uncertain") else None,
            }
        )
    order = {k: i for i, k in enumerate(("awaiting", "uncertain", "not_retrieved", "excluded", "included"))}
    items.sort(key=lambda i: (order[i["status"]], -(i["year"] or 0), i["title"]))
    return {"items": items, "counts": counts}


def mark_retrieval(
    db: Session,
    *,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    user_id: uuid.UUID,
    retrieved: bool,
    note: str | None,
) -> dict[str, Any]:
    """Record that a report sought for retrieval could not be obtained (PRISMA 'reports not retrieved'), or undo
    that. Both are new events that supersede the current one; nothing is deleted."""
    pv = _approved(db, project_id)
    if pv is None:
        raise Conflict("Approve the protocol before screening.", code="protocol_not_approved")
    paper = db.scalar(select(PaperRecord).where(PaperRecord.id == paper_id, PaperRecord.project_id == project_id))
    if paper is None:
        raise NotFound("Paper not found.")
    cur = _current_stage_by_paper(db, project_id).get(paper_id)
    if cur is None or cur.stage not in ("ta_included", "not_retrieved"):
        raise Conflict(
            "Only records that passed title/abstract screening and have no full-text decision can be marked.",
            code="not_awaiting_full_text",
        )
    if not retrieved and cur.stage == "not_retrieved":
        raise Conflict("This record is already marked as not retrieved.", code="already_not_retrieved")
    if retrieved and cur.stage == "ta_included":
        raise Conflict("This record is not marked as not retrieved.", code="not_marked")
    note = (note or "").strip() or None
    if note is not None and len(note) > 2000:
        raise ValidationFailed("The note is too long (2,000 characters at most).")
    emit_event(
        db,
        project_id=project_id,
        paper_id=paper_id,
        protocol_version_id=pv.id,
        pathway=cur.pathway,
        stage="ta_included" if retrieved else "not_retrieved",
        actor_id=user_id,
        reason_code=None if retrieved else "not_retrieved",
        reason_label=None if retrieved else "Full text could not be obtained",
        note=None if retrieved else note,
        supersedes_id=cur.id,
    )
    audit_service.record(
        db,
        action="full_text.retrieval_restored" if retrieved else "full_text.not_retrieved",
        entity_type="paper",
        entity_id=paper_id,
        actor_id=user_id,
        project_id=project_id,
        after={"note": note is not None},
    )
    db.commit()
    return {"paper_id": str(paper_id), "status": "awaiting" if retrieved else "not_retrieved"}
