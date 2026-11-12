"""AI-built search expressions, cached per approved protocol version (search_expression_suggestions).

`get_or_create` is called while building a search plan. It never raises: without a configured model, when the
protocol already has a Boolean strategy, or when the model fails, it returns (None, note) and the rule-based builder
is used. Failures are remembered for a few minutes per protocol version so a broken key or an exhausted free quota
does not cost one model call per page view.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Project, ProtocolVersion, SearchExpressionSuggestion, User
from app.providers.llm import LLMError, LLMGateway, build_gateway
from app.search import derive, llm_query
from app.services import audit_service

log = logging.getLogger(__name__)
FAILURE_TTL_S = 600.0
_failures: dict[uuid.UUID, tuple[float, str]] = {}
_lock = threading.Lock()


def _as_derived(row: SearchExpressionSuggestion) -> derive.Derived:
    why = [
        f"Built by the AI model ({row.provider} {row.model}) from your approved protocol: synonyms are joined "
        "with OR inside each concept, concepts with AND. Review it before running the search."
    ]
    why += [str(n) for n in (row.notes or [])]
    return derive.Derived(row.expression, "ai", why)


def _enabled(settings: Settings, content: dict[str, Any]) -> bool:
    if str(settings.search_query_builder).lower() != "auto":
        return False
    strategy = (content.get("search_strategy") or "").strip()
    return not (strategy and derive._looks_boolean(strategy))  # noqa: SLF001  same rule derive() applies


def cached(db: Session, approved: ProtocolVersion) -> SearchExpressionSuggestion | None:
    return db.get(SearchExpressionSuggestion, approved.id)


def get_or_create(
    db: Session,
    settings: Settings,
    project: Project,
    approved: ProtocolVersion,
    content: dict[str, Any],
    *,
    user: User | None = None,
    force: bool = False,
    gateway: LLMGateway | None = None,
    guidance: str | None = None,
) -> tuple[derive.Derived | None, str | None]:
    if not _enabled(settings, content):
        return None, None
    row = cached(db, approved)
    if row is not None and not force:
        return _as_derived(row), None
    if not force:
        with _lock:
            failed = _failures.get(approved.id)
        if failed and time.monotonic() - failed[0] < FAILURE_TTL_S:
            return None, failed[1]
    try:
        gw = gateway or build_gateway(settings)
    except LLMError as e:
        return None, f"The AI model could not be used to build the query ({e.message})."
    if not gw.available:
        return None, (
            "Tip: connect an AI model (Settings, AI model) and the search expression is designed from your whole "
            "protocol, with synonyms for every concept."
        )
    try:
        sug = llm_query.suggest(gw, content, project.topic_original, guidance)
    except LLMError as e:
        note = f"The AI model could not build the search expression ({e.message}); the rule-based query is shown."
        with _lock:
            _failures[approved.id] = (time.monotonic(), note)
        return None, note
    except Exception:  # noqa: BLE001  never break a search plan because of the optional AI step
        log.exception("AI search expression failed")
        return None, "The AI model could not build the search expression; the rule-based query is shown."
    if sug is None:
        note = (
            "The AI model's answer could not be turned into a valid search expression; the rule-based query is shown."
        )
        with _lock:
            _failures[approved.id] = (time.monotonic(), note)
        return None, note
    with _lock:
        _failures.pop(approved.id, None)
    if row is None:
        row = SearchExpressionSuggestion(protocol_version_id=approved.id)
        db.add(row)
    if guidance and guidance.strip():
        sug.notes.append(f"Adjusted to your request: {' '.join(guidance.split())[:200]}")
    row.expression, row.concepts, row.notes = sug.expression, sug.concepts, sug.notes
    row.provider, row.model = (sug.provider or "unknown")[:32], (sug.model or "unknown")[:200]
    audit_service.record(
        db,
        action="search.expression_suggested",
        entity_type="protocol_version",
        entity_id=approved.id,
        actor_id=user.id if user else None,
        project_id=project.id,
        after={"expression": sug.expression, "provider": row.provider, "model": row.model},
    )
    db.commit()
    return _as_derived(row), None
