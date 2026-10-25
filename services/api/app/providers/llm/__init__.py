"""LLM providers. `build_gateway(settings)` is the only place a provider is constructed."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.providers.llm.base import ConsentToken, LLMError, LLMGateway, LLMProvider, LLMResult

__all__ = [
    "ConsentToken",
    "LLMError",
    "LLMGateway",
    "LLMProvider",
    "LLMResult",
    "build_gateway",
    "describe",
    "terms_record_ok",
]


def terms_record_ok(settings: Any, today: date | None = None) -> bool:
    """Gate G2: the operator recorded a provider-terms review (version + date) that is not older than the interval."""
    version = str(getattr(settings, "llm_terms_record_version", "") or "").strip()
    reviewed = str(getattr(settings, "llm_terms_reviewed_on", "") or "").strip()
    if not version or not reviewed:
        return False
    try:
        on = date.fromisoformat(reviewed)
    except ValueError:
        return False
    today = today or datetime.now(UTC).date()
    return on <= today and (today - on).days <= int(getattr(settings, "llm_terms_review_interval_days", 365))


def describe(settings: Any) -> dict[str, Any]:
    """What the UI may show about the configured model. Never includes the key."""
    configured = settings.llm_provider != "none" and bool(settings.llm_api_key) and bool(settings.llm_model)
    return {
        "configured": configured,
        "provider": settings.llm_provider if configured else None,
        "model": settings.llm_model if configured else None,
        "data_sent": "Titles, abstracts, keywords and bibliographic details of the papers, plus your approved "
        "research questions and criteria. No uploaded documents.",
        "provider_notice": (
            "On Google's free tier, content sent to the Gemini API may be used to improve Google's products. "
            "Only public bibliographic metadata and your protocol are sent."
        )
        if settings.llm_provider == "gemini"
        else None,
    }


def build_gateway(settings: Any, *, client: httpx.Client | None = None) -> LLMGateway:
    if settings.llm_provider == "none" or not settings.llm_api_key or not settings.llm_model:
        return LLMGateway(None, send_private_documents=False)
    if settings.llm_provider != "gemini":
        raise LLMError("not_configured", f"Unknown LLM_PROVIDER {settings.llm_provider!r}.")
    from app.providers.llm.gemini import GeminiProvider
    from app.providers.scholarly.throttle import LocalThrottle, RedisThrottle, Throttle

    throttle: Throttle
    try:
        import redis

        throttle = RedisThrottle(redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2))
    except Exception:  # noqa: BLE001
        throttle = LocalThrottle()
    provider = GeminiProvider(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        client=client or httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_s, connect=10.0)),
        throttle=throttle,
        min_interval_s=settings.llm_min_interval_s,
        max_retries=settings.llm_max_retries,
    )
    return LLMGateway(
        provider,
        send_private_documents=settings.llm_send_private_documents,
        terms_reviewed=terms_record_ok(settings),
    )
