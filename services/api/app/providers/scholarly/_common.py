"""Small helpers shared by the adapters added for broader coverage (Semantic Scholar, PubMed, DOAJ, ...)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  (DOCTYPE/ENTITY refused in safe_xml)
from typing import Any

from app.providers.scholarly.http import ProviderError
from app.search import query as q
from app.search.derive import FilterPlan


def first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def keyword_text(expression: q.Node, per_group: int = 1) -> str:
    """For keyword-only providers: one (or a few) term(s) per concept, phrases quoted."""
    terms = q.core_terms(expression, per_group) or q.positive_terms(expression)
    return " ".join(f'"{t.text}"' if t.phrase else t.text for t in terms)


def keyword_limitations(label: str, expression: q.Node) -> list[str]:
    out = [
        f"{label} does not support Boolean queries: the main term of each concept was sent and results are ranked "
        "by relevance, so they are broader than the Boolean strategy."
    ]
    if q.has_operator(expression, q.Not):
        out.append(f"Exclusions (NOT) cannot be applied in {label} and were dropped.")
    return out


def year_range(filters: FilterPlan) -> tuple[int | None, int | None]:
    return filters.date_from, filters.date_to


_DOCTYPE = re.compile(r"<!DOCTYPE[^\[>]*>", re.I)


def safe_xml(provider: str, text: str) -> ET.Element:
    """Parse XML that may carry an EXTERNAL DOCTYPE reference (PubMed does); internal subsets/entities are refused.
    ElementTree never fetches external DTDs."""
    head = text[:8192]
    if "<!ENTITY" in head.upper() or re.search(r"<!DOCTYPE[^>]*\[", head, re.I):
        raise ProviderError(provider, "malformed", f"{provider} returned XML with an internal DTD (refused)")
    try:
        return ET.fromstring(_DOCTYPE.sub("", text, count=1))  # noqa: S314
    except ET.ParseError:
        raise ProviderError(provider, "malformed", f"{provider} returned a response that is not valid XML") from None


def itertext(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    s = " ".join("".join(el.itertext()).split())
    return s or None
