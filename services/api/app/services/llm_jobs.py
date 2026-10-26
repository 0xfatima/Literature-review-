"""Helpers shared by the AI background jobs of Phase 10 (appraisal suggestions, synthesis drafts): the
private-document gate, `model_runs` bookkeeping and page-aligned document chunks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DocumentPage, Job, ModelRun, PaperDocument, Project
from app.extraction.extractor import PageText, chunk_pages
from app.providers.llm import ConsentToken, LLMGateway
from app.services import consent_service

STOP_KINDS = frozenset({"rate_limited", "rejected", "not_configured", "refused"})


def token_guard(db: Session, project_id: uuid.UUID, daily_budget: int) -> Callable[[], str | None] | None:
    """Phase 13: a guard that stops AI calls once the project's tokens recorded today reach the daily budget.
    Phase 15: also the deployment-wide budget (LLM_GLOBAL_DAILY_TOKEN_BUDGET), so many projects together cannot
    exhaust the provider quota."""
    from app.core.config import get_settings

    global_budget = int(get_settings().llm_global_daily_token_budget or 0)
    if daily_budget <= 0 and global_budget <= 0:
        return None
    tokens = func.coalesce(
        func.sum(func.coalesce(ModelRun.input_tokens, 0) + func.coalesce(ModelRun.output_tokens, 0)), 0
    )

    def check() -> str | None:
        since = datetime.now(UTC) - timedelta(days=1)
        if global_budget > 0:
            all_used = int(db.scalar(select(tokens).where(ModelRun.created_at >= since)) or 0)
            if all_used >= global_budget:
                return (
                    "The service-wide AI token budget for the last 24 hours is used up. AI steps resume automatically "
                    "as older usage leaves the 24-hour window; nothing already saved is affected."
                )
        if daily_budget <= 0:
            return None
        used = (
            db.scalar(
                select(
                    func.coalesce(
                        func.sum(func.coalesce(ModelRun.input_tokens, 0) + func.coalesce(ModelRun.output_tokens, 0)), 0
                    )
                ).where(ModelRun.project_id == project_id, ModelRun.created_at >= since)
            )
            or 0
        )
        if int(used) >= daily_budget:
            return f"The project's AI token budget for the last 24 hours is used up ({int(used)} of {daily_budget})."
        return None

    return check


def gate(db: Session, project: Project, gw: LLMGateway) -> tuple[ConsentToken | None, str | None]:
    """(consent token, block reason). block is None when private document text may be sent (gates G1-G3)."""
    from app.core.config import get_settings

    if gw.budget_guard is None:
        gw.budget_guard = token_guard(db, project.id, get_settings().llm_daily_token_budget)
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
