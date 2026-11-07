"""LLM providers. `build_gateway(settings)` is the only place a provider is constructed."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.core.distributed import RedisSemaphore, Slots
from app.providers.llm.base import ConsentToken, LLMError, LLMGateway, LLMProvider, LLMResult

__all__ = [
    "ConsentToken",
    "LLMError",
    "LLMGateway",
    "LLMProvider",
    "LLMResult",
    "build_gateway",
    "build_provider",
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


SUPPORTED_PROVIDERS = ("gemini", "openrouter", "groq", "openai", "anthropic")


def _configured(settings: Any) -> bool:
    return settings.llm_provider != "none" and bool(settings.llm_api_key) and bool(settings.llm_model)


def describe(settings: Any) -> dict[str, Any]:
    """What the UI may show about the configured model. Never includes the key."""
    from app.providers.llm import catalog

    configured = _configured(settings)
    info = catalog.provider(settings.llm_provider)
    return {
        "configured": configured,
        "provider": settings.llm_provider if configured else None,
        "model": settings.llm_model if configured else None,
        "key_source": getattr(settings, "llm_key_source", "server") if configured else None,
        "pricing": catalog.static_pricing(settings.llm_provider, settings.llm_model or "") if configured else None,
        "data_sent": "Titles, abstracts, keywords and bibliographic details of the papers, plus your approved "
        "research questions and criteria. No uploaded documents.",
        "provider_notice": (info.data_notice or None) if (info is not None and configured) else None,
    }


def _base_url(settings: Any, provider_key: str) -> str:
    from app.providers.llm import catalog

    info = catalog.PROVIDERS[provider_key]
    if getattr(settings, "llm_key_source", "server") == "user":
        return info.base_url  # users can never choose a URL
    configured = str(getattr(settings, "llm_base_url", "") or "").strip()
    gemini_default = catalog.PROVIDERS["gemini"].base_url
    if not configured or (provider_key != "gemini" and configured.rstrip("/") == gemini_default):
        return info.base_url
    return configured


def key_fingerprint(api_key: str) -> str:
    import hashlib

    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def build_provider(settings: Any, *, client: httpx.Client | None = None, throttle: Any = None) -> LLMProvider:
    """The provider described by settings (llm_provider/llm_model/llm_api_key). Raises LLMError."""
    from app.providers.llm import catalog
    from app.providers.scholarly.throttle import LocalThrottle, RedisThrottle

    name = settings.llm_provider
    if name not in SUPPORTED_PROVIDERS:
        raise LLMError("not_configured", f"Unknown LLM_PROVIDER {name!r}.")
    if throttle is None:
        try:
            import redis

            throttle = RedisThrottle(
                redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
            )
        except Exception:  # noqa: BLE001
            throttle = LocalThrottle()
    user = getattr(settings, "llm_key_source", "server") == "user"
    interval = catalog.PROVIDERS[name].min_interval_s if user else float(settings.llm_min_interval_s)
    tkey = f"llm:{name}:{settings.llm_model}:{key_fingerprint(settings.llm_api_key)}" if user else None
    http = client or httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_s, connect=10.0))
    base = _base_url(settings, name)
    if name == "gemini":
        from app.providers.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=base,
            client=http,
            throttle=throttle,
            min_interval_s=interval,
            max_retries=settings.llm_max_retries,
            throttle_key=tkey,
        )
    if name == "anthropic":
        from app.providers.llm.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=base,
            client=http,
            throttle=throttle,
            throttle_key=tkey,
            min_interval_s=interval,
            max_retries=settings.llm_max_retries,
        )
    from app.providers.llm.openai_compat import OpenAICompatProvider

    extra = {"X-Title": "Systematic Review Scientist"} if name == "openrouter" else {}
    if name == "openrouter" and getattr(settings, "app_base_url", None):
        extra["HTTP-Referer"] = str(settings.app_base_url)
    return OpenAICompatProvider(
        name=name,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=base,
        client=http,
        throttle=throttle,
        throttle_key=tkey,
        min_interval_s=interval,
        max_retries=settings.llm_max_retries,
        extra_headers=extra,
    )


def build_gateway(settings: Any, *, client: httpx.Client | None = None) -> LLMGateway:
    if not _configured(settings):
        return LLMGateway(None, send_private_documents=False)
    provider = build_provider(settings, client=client)
    return LLMGateway(
        provider,
        send_private_documents=settings.llm_send_private_documents,
        terms_reviewed=terms_record_ok(settings),
        slots=_slot_limiter(settings),
    )


def _slot_limiter(settings: Any) -> Slots:
    local = _slots(int(getattr(settings, "llm_max_concurrent_calls", 4) or 4))
    n = int(getattr(settings, "llm_global_max_concurrent_calls", 0) or 0)
    if n <= 0:
        return Slots(local, None)
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
    except Exception:  # noqa: BLE001 - see RedisSemaphore: degrade to the per-process limit
        return Slots(local, None)
    return Slots(local, RedisSemaphore(client, f"llm:{settings.llm_provider}", n))


_SLOTS: dict[int, threading.BoundedSemaphore] = {}


def _slots(n: int) -> threading.BoundedSemaphore:
    """One process-wide semaphore per configured size: bounds concurrent AI calls from all gateways."""
    if n not in _SLOTS:
        _SLOTS[n] = threading.BoundedSemaphore(max(1, n))
    return _SLOTS[n]
