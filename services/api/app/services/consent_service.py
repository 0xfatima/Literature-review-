"""Gate G3 of docs/architecture/provider-data-handling.md: per-project consent to sending private document text to one
AI provider/model. The notice is versioned; consent records the notice and terms-record versions the owner saw."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, ValidationFailed
from app.db.models import LlmConsent, Project, User
from app.providers.llm import ConsentToken, describe, terms_record_ok
from app.services import audit_service, protocol_service

DATA_CLASS = "private_document"
NOTICE_VERSION = "document-extraction-notice-v1"
NOTICE = (
    "To extract evidence automatically, the text of the PDFs you uploaded to this project will be sent, page by page, "
    "to the AI provider and model named below. Only papers you include are processed. The provider's data-use terms "
    "were recorded by the operator as version {terms}. Depending on the provider and tier, submitted content may be "
    "retained or used by the provider. Revoking consent stops further sending immediately but cannot recall text "
    "that was already sent. Every value the AI proposes is checked against the page text and must be reviewed by you."
)


def _now() -> datetime:
    return datetime.now(UTC)


def active(db: Session, project: Project, provider: str, model: str) -> LlmConsent | None:
    return db.scalar(
        select(LlmConsent).where(
            LlmConsent.project_id == project.id,
            LlmConsent.provider == provider,
            LlmConsent.model == model,
            LlmConsent.data_class == DATA_CLASS,
            LlmConsent.revoked_at.is_(None),
        )
    )


def token_for(db: Session, project: Project, provider: str | None, model: str | None) -> ConsentToken | None:
    if not provider or not model:
        return None
    c = active(db, project, provider, model)
    return ConsentToken(str(c.id), c.provider, c.model) if c else None


def status(db: Session, settings: Settings, project: Project) -> dict[str, Any]:
    llm = describe(settings)
    provider, model = llm.get("provider"), llm.get("model")
    c = active(db, project, provider, model) if provider and model else None
    block: str | None = None
    if not llm["configured"]:
        block = "No AI model is configured on this server."
    elif not settings.llm_send_private_documents:
        block = "Sending uploaded documents to an AI provider is disabled on this server (LLM_SEND_PRIVATE_DOCUMENTS)."
    elif not terms_record_ok(settings):
        block = "The AI provider's data-use terms have not been reviewed by the operator (LLM_TERMS_RECORD_*)."
    elif c is None:
        block = "The project owner has not consented to sending document text to this AI provider and model."
    return {
        "provider": provider,
        "model": model,
        "configured": llm["configured"],
        "operator_allows_documents": bool(settings.llm_send_private_documents),
        "terms_reviewed": terms_record_ok(settings),
        "terms_record_version": settings.llm_terms_record_version or None,
        "notice_version": NOTICE_VERSION,
        "notice": NOTICE.format(terms=settings.llm_terms_record_version or "(not reviewed)"),
        "consent": (
            {"id": c.id, "consented_at": c.consented_at, "terms_record_version": c.terms_record_version} if c else None
        ),
        "can_send_documents": block is None,
        "blocked_reason": block,
    }


def grant(db: Session, settings: Settings, user: User, project: Project, notice_version: str) -> dict[str, Any]:
    protocol_service.require_writable(project)
    llm = describe(settings)
    if not llm["configured"]:
        raise Conflict("No AI model is configured on this server.", code="llm_not_configured")
    if not settings.llm_send_private_documents or not terms_record_ok(settings):
        raise Conflict(
            "The server does not allow sending documents to an AI provider (operator gates G1/G2).",
            code="documents_not_allowed",
        )
    if notice_version != NOTICE_VERSION:
        raise ValidationFailed("Read the current notice before consenting.", details=[{"field": "notice_version"}])
    if active(db, project, llm["provider"], llm["model"]) is None:
        c = LlmConsent(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=user.id,
            provider=llm["provider"],
            model=llm["model"],
            data_class=DATA_CLASS,
            terms_record_version=settings.llm_terms_record_version,
            notice_version=NOTICE_VERSION,
        )
        db.add(c)
        audit_service.record(
            db,
            action="llm_consent.granted",
            entity_type="llm_consent",
            entity_id=c.id,
            actor_id=user.id,
            project_id=project.id,
            after={"provider": c.provider, "model": c.model, "notice_version": NOTICE_VERSION},
        )
        db.commit()
    return status(db, settings, project)


def revoke(db: Session, settings: Settings, user: User, project: Project) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(LlmConsent).where(
                LlmConsent.project_id == project.id,
                LlmConsent.data_class == DATA_CLASS,
                LlmConsent.revoked_at.is_(None),
            )
        )
    )
    for c in rows:
        c.revoked_at = _now()
        audit_service.record(
            db,
            action="llm_consent.revoked",
            entity_type="llm_consent",
            entity_id=c.id,
            actor_id=user.id,
            project_id=project.id,
        )
    db.commit()
    return status(db, settings, project)
