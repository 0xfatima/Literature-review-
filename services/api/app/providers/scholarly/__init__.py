"""Scholarly search providers. `registry(settings)` is the only place adapters are constructed."""

from __future__ import annotations

from typing import Any

from app.providers.scholarly.arxiv import ArxivSource
from app.providers.scholarly.base import Page, ProviderQuery, ScholarlySource
from app.providers.scholarly.crossref import CrossrefSource
from app.providers.scholarly.europepmc import EuropePMCSource
from app.providers.scholarly.openalex import OpenAlexSource

ALL_KEYS = ("openalex", "europepmc", "arxiv", "crossref")

__all__ = [
    "ALL_KEYS",
    "Page",
    "ProviderQuery",
    "ScholarlySource",
    "match_protocol_sources",
    "parse_endpoints",
    "registry",
]


def registry(settings: Any) -> dict[str, ScholarlySource]:
    """Enabled adapters, in display order. `settings` is app.core.config.Settings (typed Any to avoid a cycle)."""
    mailto = settings.scholarly_contact_email or None
    built: dict[str, ScholarlySource] = {
        "openalex": OpenAlexSource(mailto=mailto, api_key=settings.openalex_api_key or None),
        "europepmc": EuropePMCSource(),
        "arxiv": ArxivSource(),
        "crossref": CrossrefSource(mailto=mailto),
    }
    for key, url in parse_endpoints(getattr(settings, "scholarly_endpoints", "") or "").items():
        if key in built:
            built[key].endpoint = url  # type: ignore[attr-defined]  # every adapter declares `endpoint`
    enabled = {k.strip().lower() for k in str(settings.search_providers_enabled).split(",") if k.strip()}
    return {k: v for k, v in built.items() if k in enabled}


def parse_endpoints(value: str) -> dict[str, str]:
    """'openalex=https://mirror/works,crossref=https://...' -> {provider: url}. Unknown keys are ignored."""
    out: dict[str, str] = {}
    for part in value.split(","):
        key, sep, url = part.partition("=")
        if sep and key.strip().lower() in ALL_KEYS and url.strip().startswith(("http://", "https://")):
            out[key.strip().lower()] = url.strip()
    return out


def match_protocol_sources(sources: list[str], providers: dict[str, ScholarlySource]) -> tuple[list[str], list[str]]:
    """Map the protocol's free-text source names to provider keys. Returns (matched keys, unsupported names)."""
    matched: list[str] = []
    unsupported: list[str] = []
    for name in sources:
        n = " ".join(str(name).lower().replace("-", " ").split())
        key = next((k for k, p in providers.items() if n == k or n in p.aliases), None)
        if key is None:
            unsupported.append(str(name))
        elif key not in matched:
            matched.append(key)
    return matched, unsupported
