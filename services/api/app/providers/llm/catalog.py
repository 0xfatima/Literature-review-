"""Which AI providers and models users can choose, and which of them cost nothing.

"free" means the provider lets you call the model at no charge with a free account and API key, within rate limits
(Gemini free tier, Groq free plan, OpenRouter models priced at 0). "paid" means the provider bills every request.
Free tiers change often, so:
  * OpenRouter: the free/paid label comes LIVE from its public model list (price 0 = free);
  * Gemini, Groq, OpenAI, Anthropic: the model list comes live from the provider when a key is available, and the
    free/paid label follows each provider's published policy (summarised in `free_policy` and shown to users).
The static `models` lists are only a fallback when the live list cannot be fetched. Nothing here is ever sent a
user-supplied URL: base URLs are fixed per provider (users cannot point the server at arbitrary hosts).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    pricing: str  # free | paid
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "pricing": self.pricing, "note": self.note}


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    base_url: str
    key_url: str  # where a user gets an API key
    free_policy: str
    default_model: str
    min_interval_s: float  # request spacing per API key (protects free quotas)
    models: tuple[ModelInfo, ...] = field(default_factory=tuple)
    data_notice: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "key_url": self.key_url,
            "free_policy": self.free_policy,
            "default_model": self.default_model,
            "data_notice": self.data_notice,
            "models": [m.as_dict() for m in self.models],
        }


PROVIDERS: dict[str, ProviderInfo] = {
    "gemini": ProviderInfo(
        key="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        key_url="https://aistudio.google.com/app/apikey",
        free_policy=(
            "Free tier with a Google AI Studio key: Flash, Flash-Lite and Gemma models cost nothing within the "
            "per-project rate limits. Pro models need billing."
        ),
        default_model="gemini-flash-latest",
        min_interval_s=4.0,
        models=(
            ModelInfo("gemini-flash-latest", "Gemini Flash (latest)", "free", "Recommended default"),
            ModelInfo("gemini-flash-lite-latest", "Gemini Flash-Lite (latest)", "free", "Fastest, highest free quota"),
            ModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", "free"),
            ModelInfo("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", "free"),
            ModelInfo("gemini-pro-latest", "Gemini Pro (latest)", "paid", "Needs billing enabled"),
        ),
        data_notice=(
            "On Google's free tier, content sent to the Gemini API may be used to improve Google's products. Only "
            "public bibliographic metadata and your protocol are sent unless you consent to more."
        ),
    ),
    "groq": ProviderInfo(
        key="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        key_url="https://console.groq.com/keys",
        free_policy="Free plan: every model can be used at no cost within Groq's per-minute and per-day limits.",
        default_model="llama-3.3-70b-versatile",
        min_interval_s=2.5,
        models=(
            ModelInfo("llama-3.3-70b-versatile", "Llama 3.3 70B", "free"),
            ModelInfo("openai/gpt-oss-120b", "GPT-OSS 120B", "free"),
            ModelInfo("openai/gpt-oss-20b", "GPT-OSS 20B", "free"),
            ModelInfo("llama-3.1-8b-instant", "Llama 3.1 8B (fast)", "free"),
        ),
    ),
    "openrouter": ProviderInfo(
        key="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        key_url="https://openrouter.ai/settings/keys",
        free_policy=(
            "Models whose id ends in ':free' cost nothing (rate-limited; prompts may be logged by the free "
            "provider). All other models are billed per token from your OpenRouter credit."
        ),
        default_model="meta-llama/llama-3.3-70b-instruct:free",
        min_interval_s=3.5,
        models=(
            ModelInfo("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B (free)", "free"),
            ModelInfo("deepseek/deepseek-chat-v3.1:free", "DeepSeek V3.1 (free)", "free"),
            ModelInfo("qwen/qwen3-235b-a22b:free", "Qwen3 235B (free)", "free"),
            ModelInfo("openai/gpt-4.1-mini", "GPT-4.1 mini", "paid"),
        ),
        data_notice="Free OpenRouter models may log prompts. Only public metadata and your protocol are sent.",
    ),
    "openai": ProviderInfo(
        key="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        key_url="https://platform.openai.com/api-keys",
        free_policy="No free tier: every request is billed to your OpenAI account.",
        default_model="gpt-4.1-mini",
        min_interval_s=0.5,
        models=(
            ModelInfo("gpt-4.1-mini", "GPT-4.1 mini", "paid", "Low cost"),
            ModelInfo("gpt-4.1", "GPT-4.1", "paid"),
            ModelInfo("gpt-4o-mini", "GPT-4o mini", "paid", "Low cost"),
        ),
    ),
    "anthropic": ProviderInfo(
        key="anthropic",
        label="Anthropic Claude",
        base_url="https://api.anthropic.com/v1",
        key_url="https://console.anthropic.com/settings/keys",
        free_policy="No free tier: every request is billed to your Anthropic account.",
        default_model="claude-haiku-5-5",
        min_interval_s=0.5,
        models=(
            ModelInfo("claude-haiku-5-5", "Claude Haiku 5.5", "paid", "Low cost"),
            ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5", "paid"),
        ),
    ),
}

DEFAULT_PROVIDER = "gemini"  # its default model is free
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$")
_GEMINI_SKIP = ("image", "tts", "audio", "live", "embedding", "aqa", "robotics", "computer-use", "veo", "imagen")
_GROQ_SKIP = ("whisper", "tts", "guard", "playai", "orpheus", "distil")


def valid_model_id(model: str) -> bool:
    return bool(_MODEL_ID.match(model or ""))


def provider(key: str) -> ProviderInfo | None:
    return PROVIDERS.get(key)


def gemini_pricing(model_id: str) -> str:
    m = model_id.lower()
    return "free" if ("flash" in m or "gemma" in m) and "pro" not in m else "paid"


def static_pricing(provider_key: str, model_id: str) -> str:
    """Best-effort label for a model id that is not in a live list (e.g. typed in by hand)."""
    p = PROVIDERS.get(provider_key)
    if p is not None:
        for m in p.models:
            if m.id == model_id:
                return m.pricing
    if provider_key == "gemini":
        return gemini_pricing(model_id)
    if provider_key == "groq":
        return "free"
    if provider_key == "openrouter":
        return "free" if model_id.endswith(":free") else "paid"
    return "paid"


def _sort(models: list[ModelInfo], default: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    uniq = [m for m in models if not (m.id in seen or seen.add(m.id))]  # type: ignore[func-returns-value]
    uniq.sort(key=lambda m: (m.id != default, m.pricing != "free", m.label.lower()))
    return [m.as_dict() for m in uniq[:300]]


def list_models(provider_key: str, api_key: str | None, client: httpx.Client) -> tuple[list[dict[str, str]], str]:
    """(models, source) where source is "live" or "static". Never raises for provider/network problems."""
    p = PROVIDERS[provider_key]
    try:
        live = _live(p, api_key, client)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.info("live model list unavailable", extra={"provider": provider_key, "error": type(e).__name__})
        live = None
    if live:
        return _sort(live, p.default_model), "live"
    return _sort(list(p.models), p.default_model), "static"


def _live(p: ProviderInfo, api_key: str | None, client: httpx.Client) -> list[ModelInfo] | None:
    if p.key == "openrouter":  # public endpoint, includes prices
        r = client.get(f"{p.base_url}/models")
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            mid = str(m.get("id") or "")
            if not valid_model_id(mid):
                continue
            pr = m.get("pricing") or {}
            try:
                free = float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0
            except (TypeError, ValueError):
                free = mid.endswith(":free")
            out.append(ModelInfo(mid, str(m.get("name") or mid)[:120], "free" if free else "paid"))
        return out
    if not api_key:
        return None
    if p.key == "gemini":
        r = client.get(f"{p.base_url}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": api_key})
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            mid = str(m.get("name") or "").removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" not in methods or not valid_model_id(mid) or any(s in mid for s in _GEMINI_SKIP):
                continue
            out.append(ModelInfo(mid, str(m.get("displayName") or mid)[:120], gemini_pricing(mid)))
        return out
    if p.key in ("groq", "openai"):
        r = client.get(f"{p.base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            mid = str(m.get("id") or "")
            if not valid_model_id(mid):
                continue
            if p.key == "groq":
                if any(s in mid for s in _GROQ_SKIP) or m.get("active") is False:
                    continue
                out.append(ModelInfo(mid, mid, "free"))
            elif mid.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-")) and not any(
                s in mid for s in ("audio", "realtime", "transcribe", "tts", "image", "search")
            ):
                out.append(ModelInfo(mid, mid, "paid"))
        return out
    if p.key == "anthropic":
        r = client.get(
            f"{p.base_url}/models",
            params={"limit": 100},
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        r.raise_for_status()
        return [
            ModelInfo(str(m["id"]), str(m.get("display_name") or m["id"])[:120], "paid")
            for m in r.json().get("data", [])
            if valid_model_id(str(m.get("id") or ""))
        ]
    return None
