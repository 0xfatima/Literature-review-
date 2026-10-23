"""The interface every scholarly search provider implements (spec 3.3: "source adapters behind a common interface").

Workflow and services only use `ScholarlySource`; nothing outside `app/providers/scholarly/` knows a provider's URL,
query syntax or response shape. Adding a provider = one module + one registry line.

Secrets and contact details (API keys, the polite-pool e-mail address) are added by the adapter at request time
(`request_params`) and are never part of the stored, user-visible query parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.search.derive import FilterPlan
from app.search.query import Node
from app.search.records import ScholarlyRecord

TIERS = ("open_access", "additional")


def as_dict(value: Any) -> dict[str, Any]:
    """A provider field that should be an object; anything else (missing, null, wrong type) becomes {}."""
    return value if isinstance(value, dict) else {}


@dataclass
class ProviderQuery:
    """A provider-specific query built from the expression and the protocol's filters. Stored verbatim."""

    provider: str
    endpoint: str
    query_text: str  # the exact search string sent to the provider
    params: dict[str, str]  # every request parameter except paging, secrets and contact details
    filters_applied: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


@dataclass
class Page:
    records: list[dict[str, Any]]  # original provider records, JSON-serialisable, in provider order
    next_cursor: str | None  # None = no further page
    total: int | None  # the provider's reported hit count, when it gives one


class ScholarlySource(Protocol):
    key: str
    label: str
    tier: str  # "open_access": reports open-access status and lawful full-text links; "additional": broader
    description: str
    attribution: str
    terms_url: str
    min_interval_s: float  # minimum spacing between requests (provider rate rule), shared across workers
    max_page_size: int
    aliases: tuple[str, ...]  # protocol source names that mean this provider (lower case)

    def build_query(self, expression: Node, filters: FilterPlan) -> ProviderQuery: ...

    def fetch_page(self, http: Any, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        """`http` is an app.providers.scholarly.http.HttpFetcher. Raises ProviderError."""
        ...

    def record_id(self, raw: dict[str, Any]) -> str: ...

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        """Raises app.search.records.RecordError for a record that cannot be used (kept as skipped)."""
        ...
