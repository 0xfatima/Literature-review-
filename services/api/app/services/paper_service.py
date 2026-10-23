"""Paper records discovered by search: list, detail with full provenance, and identification counts.
Read-only in this phase (screening decisions arrive with the screening phase)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.models import ACCESS_STATUSES, PaperRecord, Project
from app.repositories import search as repo
from app.services import search_service

SORTS = ("recent", "year", "title")
ACCESS_LABELS = {
    "open_pdf_link": "Open-access PDF link reported by the provider (not downloaded or verified)",
    "open_landing_page": "Reported open access; a landing page is linked, no direct PDF link",
    "metadata_only": "Metadata only: no open full text reported. Obtain a copy you are authorised to use.",
}
FULL_TEXT_LABELS = {
    "not_obtained": "Full text not obtained: only the title, abstract and metadata are available here",
    "uploaded": "Full text uploaded",
    "parsed": "Full text uploaded and parsed",
    "failed": "Full text upload could not be processed",
}


def _brief(p: PaperRecord) -> dict[str, Any]:
    return {
        "id": p.id,
        "title": p.title,
        "authors": [a.get("name") for a in (p.authors or []) if a.get("name")][:12],
        "author_count": len(p.authors or []),
        "year": p.year,
        "venue": p.venue,
        "publication_type": p.publication_type,
        "doi": p.doi,
        "pmid": p.pmid,
        "pmcid": p.pmcid,
        "arxiv_id": p.arxiv_id,
        "openalex_id": p.openalex_id,
        "is_preprint": p.is_preprint,
        "has_preprint_version": p.has_preprint_version,
        "is_retracted": p.is_retracted,
        "oa_status": p.oa_status,
        "access_status": p.access_status,
        "access_label": ACCESS_LABELS[p.access_status],
        "full_text_status": p.full_text_status,
        "full_text_label": FULL_TEXT_LABELS.get(p.full_text_status, p.full_text_status),
        "oa_url": p.oa_url,
        "pdf_url": p.pdf_url,
        "has_abstract": bool(p.abstract),
        "providers": p.providers,
        "source_count": p.source_count,
        "possible_duplicate_of_id": p.possible_duplicate_of_id,
        "possible_duplicate_reason": p.possible_duplicate_reason,
        "created_at": p.created_at,
    }


def list_(
    db: Session,
    project: Project,
    *,
    text: str | None,
    access: str | None,
    provider: str | None,
    run_id: uuid.UUID | None,
    flagged: bool | None,
    preprint: bool | None,
    sort: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    access, provider = access or None, provider or None  # "" means "no filter"
    if access is not None and access not in ACCESS_STATUSES:
        raise ValidationFailed(
            "Unknown access filter.", details=[{"field": "access", "allowed": list(ACCESS_STATUSES)}]
        )
    if sort not in SORTS:
        raise ValidationFailed("Unknown sort.", details=[{"field": "sort", "allowed": list(SORTS)}])
    if text is not None and len(text) > 200:
        raise ValidationFailed("Search text is too long.", details=[{"field": "q", "issue": "too_long"}])
    items, total = repo.list_papers(
        db,
        project.id,
        offset=offset,
        limit=limit,
        sort=sort,
        text=text or None,
        access=access,
        provider=provider,
        run_id=run_id,
        flagged=flagged,
        preprint=preprint,
    )
    return {"items": [_brief(p) for p in items], "total": total, "offset": offset, "limit": limit}


def detail(db: Session, project: Project, paper_id: uuid.UUID) -> dict[str, Any]:
    p = repo.get_paper(db, project.id, paper_id)
    if p is None:
        raise NotFound("Paper not found.")
    dup = repo.get_paper(db, project.id, p.possible_duplicate_of_id) if p.possible_duplicate_of_id else None
    sources = []
    for src, query, run in repo.sources_for(db, p.id):
        sources.append(
            {
                "id": src.id,
                "provider": src.provider,
                "provider_record_id": src.provider_record_id,
                "source_url": src.source_url,
                "rank": src.rank,
                "match_basis": src.match_basis,
                "retrieved_at": src.retrieved_at,
                "search_run_id": run.id,
                "search_run_created_at": run.created_at,
                "search_run_tier": run.tier,
                "protocol_version_id": run.protocol_version_id,
                "query_text": query.query_text,
                "normalized": src.normalized,
                "raw": src.raw,
            }
        )
    return {
        **_brief(p),
        "abstract": p.abstract,
        "authors_full": p.authors,
        "keywords": p.keywords,
        "language": p.language,
        "license": p.license,
        "other_ids": p.other_ids,
        "field_sources": p.field_sources,
        "possible_duplicate_of": {"id": dup.id, "title": dup.title, "year": dup.year, "doi": dup.doi} if dup else None,
        "sources": sources,
    }


def summary(db: Session, project: Project) -> dict[str, Any]:
    approved = search_service.approved_id(db, project)
    s = repo.project_summary(db, project.id, approved)
    runs = repo.list_runs(db, project.id, 200)
    last_oa = next((r for r in runs if r.tier == "open_access"), None)
    return {
        **s,
        "approved_protocol_version_id": approved,
        "run_count": len(runs),
        "incomplete_runs": sum(1 for r in runs if r.status in ("partial", "failed", "cancelled")),
        "stale_runs": sum(1 for r in runs if approved is not None and r.protocol_version_id != approved),
        "broaden_prompt": (
            {"search_run_id": last_oa.id, "decision": last_oa.broaden_decision}
            if last_oa is not None and last_oa.status not in ("queued", "running")
            else None
        ),
        "counting_note": (
            "Counts are computed from stored records. 'Records identified' counts every record each provider "
            "returned; 'duplicates removed' is records identified minus unique papers."
        ),
    }
