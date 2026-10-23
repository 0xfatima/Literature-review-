"""Search-run, query, paper and source-record persistence. API lookups are scoped by project_id (callers have done
the owner check through get_owned_project); `*_by_id` functions are for the worker only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import SEARCH_RUN_ACTIVE, PaperRecord, PaperSourceRecord, SearchQuery, SearchRun

# ---------- runs ----------


def _runs(project_id: uuid.UUID) -> Select[SearchRun]:
    return select(SearchRun).where(SearchRun.project_id == project_id).options(selectinload(SearchRun.queries))


def list_runs(db: Session, project_id: uuid.UUID, limit: int) -> list[SearchRun]:
    return list(db.scalars(_runs(project_id).order_by(SearchRun.created_at.desc(), SearchRun.id).limit(limit)))


def get_run(db: Session, project_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False) -> SearchRun | None:
    q = _runs(project_id).where(SearchRun.id == run_id)
    if lock:
        q = q.with_for_update(of=SearchRun)
    return db.scalar(q)


def get_run_by_id(db: Session, run_id: uuid.UUID) -> SearchRun | None:  # worker only
    return db.scalar(select(SearchRun).where(SearchRun.id == run_id).options(selectinload(SearchRun.queries)))


def active_run(db: Session, project_id: uuid.UUID) -> SearchRun | None:
    return db.scalar(
        select(SearchRun).where(SearchRun.project_id == project_id, SearchRun.status.in_(SEARCH_RUN_ACTIVE))
    )


def latest_open_access_run(db: Session, project_id: uuid.UUID) -> SearchRun | None:
    return db.scalar(
        select(SearchRun)
        .where(SearchRun.project_id == project_id, SearchRun.tier == "open_access")
        .order_by(SearchRun.created_at.desc())
        .limit(1)
    )


def run_counts(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Counts derived from stored records (never from provider claims or model output)."""
    records = db.scalar(select(func.count()).where(PaperSourceRecord.search_run_id == run_id)) or 0
    distinct = (
        db.scalar(
            select(func.count(func.distinct(PaperSourceRecord.paper_id))).where(
                PaperSourceRecord.search_run_id == run_id
            )
        )
        or 0
    )
    new = db.scalar(select(func.count()).where(PaperRecord.first_seen_run_id == run_id)) or 0
    flagged = (
        db.scalar(
            select(func.count()).where(
                PaperRecord.first_seen_run_id == run_id, PaperRecord.possible_duplicate_of_id.isnot(None)
            )
        )
        or 0
    )
    return {
        "records_retrieved": int(records),
        "distinct_papers": int(distinct),
        "papers_new": int(new),
        "papers_matched_existing": int(distinct - new),
        "duplicates_in_run": int(records - distinct),
        "possible_duplicates": int(flagged),
    }


# ---------- papers ----------


def paper_candidates(db: Session, project_id: uuid.UUID, rec: Any) -> list[PaperRecord]:
    """Existing papers sharing an identifier or the normalised title with `rec` (input to dedup.decide)."""
    conds = []
    for col, value in (
        (PaperRecord.doi, rec.doi),
        (PaperRecord.pmid, rec.pmid),
        (PaperRecord.arxiv_id, rec.arxiv_id),
        (PaperRecord.openalex_id, rec.openalex_id),
    ):
        if value:
            conds.append(col == value)
    if rec.title_key:
        conds.append(PaperRecord.title_key == rec.title_key)
    if not conds:
        return []
    return list(
        db.scalars(
            select(PaperRecord)
            .where(PaperRecord.project_id == project_id, or_(*conds))
            .order_by(PaperRecord.created_at)
            .limit(50)
        )
    )


def paper_holding(db: Session, project_id: uuid.UUID, field: str, value: str) -> PaperRecord | None:
    col = getattr(PaperRecord, field)
    return db.scalar(select(PaperRecord).where(PaperRecord.project_id == project_id, col == value))


def source_exists(db: Session, query_id: uuid.UUID, provider_record_id: str) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    PaperSourceRecord.search_query_id == query_id,
                    PaperSourceRecord.provider_record_id == provider_record_id,
                )
            )
        )
    )


def _paper_filters(
    project_id: uuid.UUID,
    *,
    text: str | None,
    access: str | None,
    provider: str | None,
    run_id: uuid.UUID | None,
    flagged: bool | None,
    preprint: bool | None,
) -> list[Any]:
    conds: list[Any] = [PaperRecord.project_id == project_id]
    if text:
        like = f"%{text.strip().lower()}%"
        conds.append(
            or_(
                func.lower(PaperRecord.title).like(like),
                func.lower(func.coalesce(PaperRecord.abstract, "")).like(like),
                PaperRecord.doi == text.strip().lower(),
            )
        )
    if access:
        conds.append(PaperRecord.access_status == access)
    if provider:
        conds.append(PaperRecord.providers.contains([provider]))
    if run_id:
        conds.append(
            exists().where(PaperSourceRecord.paper_id == PaperRecord.id, PaperSourceRecord.search_run_id == run_id)
        )
    if flagged is not None:
        conds.append(
            PaperRecord.possible_duplicate_of_id.isnot(None)
            if flagged
            else PaperRecord.possible_duplicate_of_id.is_(None)
        )
    if preprint is not None:
        conds.append(PaperRecord.is_preprint.is_(preprint))
    return conds


def list_papers(
    db: Session, project_id: uuid.UUID, *, offset: int, limit: int, sort: str = "recent", **filters: Any
) -> tuple[list[PaperRecord], int]:
    conds = _paper_filters(project_id, **filters)
    total = db.scalar(select(func.count()).select_from(PaperRecord).where(and_(*conds))) or 0
    order = (
        (PaperRecord.year.desc().nulls_last(), PaperRecord.id)
        if sort == "year"
        else (PaperRecord.title_key, PaperRecord.id)
        if sort == "title"
        else (PaperRecord.created_at.desc(), PaperRecord.id)
    )
    rows = db.scalars(select(PaperRecord).where(and_(*conds)).order_by(*order).offset(offset).limit(limit))
    return list(rows), int(total)


def get_paper(db: Session, project_id: uuid.UUID, paper_id: uuid.UUID) -> PaperRecord | None:
    return db.scalar(select(PaperRecord).where(PaperRecord.project_id == project_id, PaperRecord.id == paper_id))


def sources_for(db: Session, paper_id: uuid.UUID) -> list[tuple[PaperSourceRecord, SearchQuery, SearchRun]]:
    rows = db.execute(
        select(PaperSourceRecord, SearchQuery, SearchRun)
        .join(SearchQuery, SearchQuery.id == PaperSourceRecord.search_query_id)
        .join(SearchRun, SearchRun.id == PaperSourceRecord.search_run_id)
        .where(PaperSourceRecord.paper_id == paper_id)
        .order_by(PaperSourceRecord.retrieved_at, PaperSourceRecord.id)
    )
    return [(r[0], r[1], r[2]) for r in rows]


def project_summary(db: Session, project_id: uuid.UUID, protocol_version_id: uuid.UUID | None) -> dict[str, Any]:
    """Identification counts derived from stored records, overall and for runs of the current approved protocol."""

    def scope(run_filter: Any) -> dict[str, Any]:
        base = (
            select(PaperSourceRecord)
            .join(SearchRun, SearchRun.id == PaperSourceRecord.search_run_id)
            .where(PaperSourceRecord.project_id == project_id, run_filter)
        )
        sub = base.subquery()
        records = db.scalar(select(func.count()).select_from(sub)) or 0
        by_provider = {
            str(p): int(n) for p, n in db.execute(select(sub.c.provider, func.count()).group_by(sub.c.provider)).all()
        }
        by_tier = {
            str(t): int(n)
            for t, n in db.execute(
                select(SearchRun.tier, func.count())
                .select_from(PaperSourceRecord)
                .join(SearchRun, SearchRun.id == PaperSourceRecord.search_run_id)
                .where(PaperSourceRecord.project_id == project_id, run_filter)
                .group_by(SearchRun.tier)
            ).all()
        }
        papers = db.scalar(select(func.count(func.distinct(sub.c.paper_id)))) or 0
        access = {
            str(a): int(n)
            for a, n in db.execute(
                select(PaperRecord.access_status, func.count())
                .where(PaperRecord.id.in_(select(sub.c.paper_id)))
                .group_by(PaperRecord.access_status)
            ).all()
        }
        flagged = (
            db.scalar(
                select(func.count()).where(
                    PaperRecord.id.in_(select(sub.c.paper_id)), PaperRecord.possible_duplicate_of_id.isnot(None)
                )
            )
            or 0
        )
        preprints = (
            db.scalar(select(func.count()).where(PaperRecord.id.in_(select(sub.c.paper_id)), PaperRecord.is_preprint))
            or 0
        )
        return {
            "records_identified": int(records),
            "records_by_provider": by_provider,
            "records_by_tier": by_tier,
            "unique_papers": int(papers),
            "duplicates_removed": int(records - papers),
            "possible_duplicates": int(flagged),
            "preprints": int(preprints),
            "access": access,
        }

    current = scope(SearchRun.protocol_version_id == protocol_version_id) if protocol_version_id is not None else None
    return {"all_runs": scope(SearchRun.project_id == project_id), "current_protocol": current}
