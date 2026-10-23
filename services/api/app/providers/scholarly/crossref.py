"""Crossref REST API (https://api.crossref.org). DOI registration metadata across publishers: broad, but it is a
metadata registry, not a full-text or open-access index. It is therefore an *additional* source, used only when the
user chooses to broaden discovery.

Rules followed: cursor paging (`cursor=*`), `rows` <= 1000, `select=` for the fields used, the polite pool via
`mailto` (added at request time, never stored) and a User-Agent with contact details, conservative request spacing.
Crossref's `query` is a relevance-ranked bibliographic match with no Boolean support, so the expression is
flattened to its positive keywords; this is recorded as a limitation on every Crossref query.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly.base import Page, ProviderQuery
from app.providers.scholarly.http import HttpFetcher, ProviderError
from app.search import query as q
from app.search.derive import FilterPlan, resolve_language
from app.search.records import (
    MAX_ABSTRACT,
    Author,
    RecordError,
    ScholarlyRecord,
    clean_text,
    finish,
    norm_doi,
    require_title,
    year,
)

ENDPOINT = "https://api.crossref.org/works"
SELECT = "DOI,title,author,abstract,container-title,type,subtype,issued,published,license,link,URL,language,subject"


def _first(v: Any) -> str | None:
    if isinstance(v, list):
        return str(v[0]) if v else None
    return str(v) if v else None


def _issued_year(raw: dict[str, Any]) -> int | None:
    for k in ("issued", "published"):
        d = raw.get(k)
        parts = d.get("date-parts") if isinstance(d, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0] and parts[0][0]:
            return year(parts[0][0])
    return None


class CrossrefSource:
    key = "crossref"
    label = "Crossref"
    tier = "additional"
    description = (
        "DOI metadata from publishers. Broadens discovery, including subscription content; it rarely reports "
        "whether a free full text exists."
    )
    attribution = "Metadata from Crossref (crossref.org)."
    terms_url = "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"
    min_interval_s: float = 0.5
    max_page_size = 1000
    aliases: tuple[str, ...] = ("crossref",)

    def __init__(self, *, mailto: str | None = None):
        self._mailto = mailto

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        terms = q.positive_terms(expression)
        text = " ".join(f'"{t.text}"' if t.phrase else t.text for t in terms)
        limitations = [
            "Crossref does not support Boolean queries: the expression was reduced to its keywords and results are "
            "ranked by relevance, so they are broader than the Boolean strategy and can include unrelated works."
        ]
        if q.has_operator(expression, q.Not):
            limitations.append("Exclusions (NOT) cannot be applied in Crossref and were dropped.")
        applied: dict[str, Any] = {}
        parts: list[str] = []
        if filters.date_from:
            parts.append(f"from-pub-date:{filters.date_from}-01-01")
            applied["date_from"] = filters.date_from
        if filters.date_to:
            parts.append(f"until-pub-date:{filters.date_to}-12-31")
            applied["date_to"] = filters.date_to
        if filters.languages:
            limitations.append("Crossref has no language filter; the protocol's languages were not applied here.")
        params = {"query": text, "select": SELECT}
        if parts:
            params["filter"] = ",".join(parts)
        return ProviderQuery(self.key, ENDPOINT, text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        params: dict[str, Any] = {**query.params, "rows": min(page_size, self.max_page_size), "cursor": cursor or "*"}
        if self._mailto:
            params["mailto"] = self._mailto
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s)
        msg = body.get("message") if isinstance(body, dict) else None
        if not isinstance(msg, dict) or not isinstance(msg.get("items"), list):
            raise ProviderError(self.label, "malformed", "Crossref returned an unexpected response shape")
        items = [i for i in msg["items"] if isinstance(i, dict)]
        nxt = msg.get("next-cursor") if items else None
        total = msg.get("total-results")
        return Page(items, str(nxt) if nxt else None, int(total) if isinstance(total, int) else None)

    def record_id(self, raw: dict[str, Any]) -> str:
        doi = norm_doi(raw.get("DOI"))
        if not doi:
            raise RecordError("Crossref record without a DOI")
        return doi

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        authors = []
        for a in raw.get("author") or []:
            if not isinstance(a, dict):
                continue
            given, family = a.get("given"), a.get("family")
            name = a.get("name") or " ".join(x for x in (given, family) if x)
            if name:
                authors.append(Author(name=str(name)[:300], family=family, orcid=a.get("ORCID")))
        lic_urls = [str(x.get("URL")) for x in raw.get("license") or [] if isinstance(x, dict) and x.get("URL")]
        cc = next((u for u in lic_urls if "creativecommons.org" in u), None)
        pdf = None
        if cc:  # a link is only treated as an open full text when the work carries an open licence
            for ln in raw.get("link") or []:
                if isinstance(ln, dict) and ln.get("content-type") == "application/pdf" and ln.get("URL"):
                    pdf = str(ln["URL"])
                    break
        doi = self.record_id(raw)
        wtype, subtype = raw.get("type"), raw.get("subtype")
        lang = raw.get("language")
        resolved = resolve_language(str(lang)) if lang else None
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=doi,
            title=require_title(_first(raw.get("title"))),
            authors=authors,
            abstract=clean_text(raw.get("abstract"), MAX_ABSTRACT),
            year=_issued_year(raw),
            venue=(_first(raw.get("container-title")) or "")[:500] or None,
            publication_type=str(subtype or wtype) if (subtype or wtype) else None,
            keywords=[str(s)[:120] for s in raw.get("subject") or [] if s],
            language=resolved[0] if resolved else None,
            doi=doi,
            is_preprint=wtype == "posted-content" and subtype == "preprint",
            oa_status="open" if cc else "unknown",
            oa_url=(raw.get("URL") or f"https://doi.org/{doi}") if cc else None,
            pdf_url=pdf,
            license=cc or (lic_urls[0] if lic_urls else None),
            source_url=raw.get("URL") or f"https://doi.org/{doi}",
        )
        return finish(rec)
