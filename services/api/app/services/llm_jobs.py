"""Helpers shared by the AI background jobs of Phase 10 (appraisal suggestions, synthesis drafts): the
private-document gate, `model_runs` bookkeeping and page-aligned document chunks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentPage, Job, ModelRun, PaperDocument, Project
from app.extraction.extractor import PageText, chunk_pages
from app.providers.llm import ConsentToken, LLMGateway
from app.services import consent_service

STOP_KINDS = frozenset({"rate_limited", "rejected", "not_configured", "refused"})


def gate(db: Session, project: Project, gw: LLMGateway) -> tuple[ConsentToken | None, str | None]:
    """(consent token, block reason). block is None when private document text may be sent (gates G1-G3)."""
    consent = consent_service.token_for(db, project, gw.provider.name, gw.provider.model) if gw.provider else None
    return consent, gw.private_document_block(consent)


def model_run(
    db: Session,
    job: Job,
    project_id: uuid.UUID,
    gw: LLMGateway,
    *,
    purpose: str,
    prompt_version: str,
    n: int,
    **kw: Any,
) -> ModelRun:
    provider = gw.provider
    mr = ModelRun(
        project_id=project_id,
        job_id=job.id,
        purpose=purpose,
        provider=provider.name if provider else "none",
        model=(kw.pop("model", None) or (provider.model if provider else "none"))[:120],
        prompt_version=prompt_version,
        data_class="private_document",
        settings={"temperature": 0.0},
        item_count=n,
        **kw,
    )
    db.add(mr)
    db.flush()
    return mr


def document_chunks(db: Session, doc: PaperDocument, max_chars: int) -> list[list[PageText]]:
    rows = db.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == doc.id, DocumentPage.text_version == doc.text_version)
        .order_by(DocumentPage.page_no)
    )
    return chunk_pages([PageText(str(p.id), p.page_no, p.text or "") for p in rows], max(2000, max_chars))
