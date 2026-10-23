"""Relevance assessments, screening decisions/states and model runs. Lookups are scoped by project_id (callers have
done the owner check). The queue query joins each paper with its current screening state and its lexical and AI
assessments for the CURRENT approved protocol version."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.db.models import (
    ModelRun,
    PaperRecord,
    RelevanceAssessment,
    ScreeningDecision,
    ScreeningState,
)
from app.screening import ai_assessment, relevance

STAGE = "title_abstract"


def upsert_assessment(db: Session, values: dict[str, Any]) -> None:
    stmt = insert(RelevanceAssessment).values(id=uuid.uuid4(), **values)
    keep = (
        "score",
        "band",
        "confidence",
        "explanation",
        "eligibility_suggestion",
        "suggestion_detail",
        "warnings",
        "model_run_id",
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_relevance_assessments_paper_id",
        set_={**{k: getattr(stmt.excluded, k) for k in keep}, "created_at": func.now()},
    )
    db.execute(stmt)


def papers_needing(
    db: Session, project_id: uuid.UUID, pv_id: uuid.UUID, method: str, version: str, *, force: bool, limit: int | None
) -> list[PaperRecord]:
    """Papers without an assessment of this method version (all papers when force). LLM order: best lexical first."""
    q = select(PaperRecord).where(PaperRecord.project_id == project_id)
    if not force:
        done = select(RelevanceAssessment.paper_id).where(
            RelevanceAssessment.project_id == project_id,
            RelevanceAssessment.protocol_version_id == pv_id,
            RelevanceAssessment.method == method,
            RelevanceAssessment.method_version == version,
        )
        q = q.where(PaperRecord.id.notin_(done))
    if method == "llm":
        lex = aliased(RelevanceAssessment)
        q = q.outerjoin(
            lex,
            and_(
                lex.paper_id == PaperRecord.id,
                lex.protocol_version_id == pv_id,
                lex.method == "lexical",
                lex.method_version == relevance.METHOD_VERSION,
            ),
        ).order_by(func.coalesce(lex.score, -1).desc(), PaperRecord.id)
    else:
        q = q.order_by(PaperRecord.created_at, PaperRecord.id)
    if limit is not None:
        q = q.limit(limit)
    return list(db.scalars(q))


def assessment_counts(db: Session, project_id: uuid.UUID, pv_id: uuid.UUID) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"lexical": {}, "llm": {}}
    rows = db.execute(
        select(RelevanceAssessment.method, RelevanceAssessment.band, func.count())
        .where(
            RelevanceAssessment.project_id == project_id,
            RelevanceAssessment.protocol_version_id == pv_id,
            RelevanceAssessment.method_version.in_((relevance.METHOD_VERSION, ai_assessment.PROMPT_VERSION)),
        )
        .group_by(RelevanceAssessment.method, RelevanceAssessment.band)
    ).all()
    for method, band, n in rows:
        out[str(method)][str(band)] = int(n)
    return out


def recent_model_runs(db: Session, project_id: uuid.UUID, limit: int) -> list[ModelRun]:
    return list(
        db.scalars(
            select(ModelRun).where(ModelRun.project_id == project_id).order_by(ModelRun.created_at.desc()).limit(limit)
        )
    )


# ---------- queue ----------


def _joined(project_id: uuid.UUID, pv_id: uuid.UUID | None) -> tuple[Any, Any, Any, Any]:
    lex = aliased(RelevanceAssessment, name="lex")
    ai = aliased(RelevanceAssessment, name="ai")
    st = aliased(ScreeningState, name="st")
    q = (
        select(PaperRecord, st, lex, ai)
        .outerjoin(st, and_(st.paper_id == PaperRecord.id, st.stage == STAGE))
        .outerjoin(
            lex,
            and_(
                lex.paper_id == PaperRecord.id,
                lex.protocol_version_id == pv_id,
                lex.method == "lexical",
                lex.method_version == relevance.METHOD_VERSION,
            ),
        )
        .outerjoin(
            ai,
            and_(
                ai.paper_id == PaperRecord.id,
                ai.protocol_version_id == pv_id,
                ai.method == "llm",
                ai.method_version == ai_assessment.PROMPT_VERSION,
            ),
        )
        .where(PaperRecord.project_id == project_id)
    )
    return q, st, lex, ai


def queue(
    db: Session,
    project_id: uuid.UUID,
    pv_id: uuid.UUID | None,
    *,
    decision: str | None,
    band: str | None,
    suggestion: str | None,
    text: str | None,
    flagged: bool | None,
    sort: str,
    offset: int,
    limit: int,
) -> tuple[list[tuple[PaperRecord, Any, Any, Any]], int]:
    q, st, lex, ai = _joined(project_id, pv_id)
    effective_band = func.coalesce(ai.band, lex.band, "not_assessed")
    conds: list[Any] = []
    if decision == "undecided":
        conds.append(or_(st.id.is_(None), st.decision == "cleared"))
    elif decision:
        conds.append(st.decision == decision)
    if band:
        conds.append(effective_band == band)
    if suggestion == "none":
        conds.append(ai.eligibility_suggestion.is_(None))
    elif suggestion:
        conds.append(ai.eligibility_suggestion == suggestion)
    if text:
        like = f"%{text.strip().lower()}%"
        conds.append(
            or_(
                func.lower(PaperRecord.title).like(like), func.lower(func.coalesce(PaperRecord.abstract, "")).like(like)
            )
        )
    if flagged is not None:
        conds.append(
            PaperRecord.possible_duplicate_of_id.isnot(None)
            if flagged
            else PaperRecord.possible_duplicate_of_id.is_(None)
        )
    if conds:
        q = q.where(and_(*conds))
    total = db.scalar(select(func.count()).select_from(q.with_only_columns(PaperRecord.id).subquery())) or 0
    rank = func.coalesce(ai.score, lex.score, -1)
    order = {
        "relevance": (rank.desc(), PaperRecord.id),
        "relevance_asc": (rank.asc(), PaperRecord.id),
        "year": (PaperRecord.year.desc().nulls_last(), PaperRecord.id),
        "title": (PaperRecord.title_key, PaperRecord.id),
        "recent_decision": (func.coalesce(st.updated_at, PaperRecord.created_at).desc(), PaperRecord.id),
    }[sort]
    rows = db.execute(q.order_by(*order).offset(offset).limit(limit)).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows], int(total)


def progress(db: Session, project_id: uuid.UUID) -> dict[str, int]:
    total = db.scalar(select(func.count()).where(PaperRecord.project_id == project_id)) or 0
    decided = case((ScreeningState.decision == "cleared", "undecided"), else_=ScreeningState.decision)
    rows = db.execute(
        select(decided, func.count())
        .where(ScreeningState.project_id == project_id, ScreeningState.stage == STAGE)
        .group_by(decided)
    ).all()
    counts = {"include": 0, "exclude": 0, "uncertain": 0}
    for d, n in rows:
        if d in counts:
            counts[str(d)] = int(n)
    counts["total"] = int(total)
    counts["undecided"] = int(total) - counts["include"] - counts["exclude"] - counts["uncertain"]
    return counts


# ---------- decisions ----------


def state_for(db: Session, paper_id: uuid.UUID, *, lock: bool = False) -> ScreeningState | None:
    q = select(ScreeningState).where(ScreeningState.paper_id == paper_id, ScreeningState.stage == STAGE)
    if lock:
        q = q.with_for_update()
    return db.scalar(q)


def papers_in_project(db: Session, project_id: uuid.UUID, ids: list[uuid.UUID]) -> list[PaperRecord]:
    return list(db.scalars(select(PaperRecord).where(PaperRecord.project_id == project_id, PaperRecord.id.in_(ids))))


def assessments_for(db: Session, paper_id: uuid.UUID, pv_id: uuid.UUID) -> list[RelevanceAssessment]:
    return list(
        db.scalars(
            select(RelevanceAssessment).where(
                RelevanceAssessment.paper_id == paper_id, RelevanceAssessment.protocol_version_id == pv_id
            )
        )
    )


def history(db: Session, paper_id: uuid.UUID) -> list[ScreeningDecision]:
    return list(
        db.scalars(
            select(ScreeningDecision)
            .where(ScreeningDecision.paper_id == paper_id)
            .order_by(ScreeningDecision.created_at, ScreeningDecision.id)
        )
    )


def batch(db: Session, project_id: uuid.UUID, batch_id: uuid.UUID) -> list[ScreeningDecision]:
    return list(
        db.scalars(
            select(ScreeningDecision).where(
                ScreeningDecision.project_id == project_id, ScreeningDecision.batch_id == batch_id
            )
        )
    )


def decision(db: Session, project_id: uuid.UUID, decision_id: uuid.UUID) -> ScreeningDecision | None:
    return db.scalar(
        select(ScreeningDecision).where(ScreeningDecision.project_id == project_id, ScreeningDecision.id == decision_id)
    )
