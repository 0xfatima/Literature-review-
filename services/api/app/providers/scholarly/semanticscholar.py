"""Semantic Scholar Academic Graph API, bulk paper search (https://api.semanticscholar.org/api-docs/graph).

Bulk search supports Boolean syntax (+ AND, | OR, - NOT, "phrases", trailing * prefix match, parentheses), returns up
to 1000 records per call with a continuation `token`, and is sorted here by citation count so that a capped result
holds the most-cited MATCHING papers (bulk search has no relevance ranking). Without an API key requests share a
public pool that is often throttled (429, retried with back-off); a free key (SEMANTIC_SCHOLAR_API_KEY) is sent in
the x-api-key header at request time and never stored.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly.base import Page, ProviderQuery
from app.providers.scholarly.base import as_dict as _d
from app.providers.scholarly.http import HttpFetcher, ProviderError
from app.search import query as q
from app.search.derive import FilterPlan
from app.search.records import (
    MAX_ABSTRACT,
    Author,
    RecordError,
    ScholarlyRecord,
    clean_text,
    finish,
    norm_arxiv,
    norm_doi,
    norm_pmcid,
    norm_pmid,
    require_title,
    year,
)

ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = (
    "paperId,title,abstract,year,authors,externalIds,venue,journal,publicationTypes,openAccessPdf,isOpenAccess,"
    "url,fieldsOfStudy,publicationDate"
)
DIALECT = q.Dialect(and_op=" + ", or_op=" | ", and_not=" -", supports_truncation=True, name="Semantic Scholar")


class SemanticScholarSource:
    endpoint: str = ENDPOINT
    key = "semanticscholar"
    label = "Semantic Scholar"
    tier = "open_access"
    default_on = True
    description = (
        "Large multidisciplinary index (AI2) with abstracts and links to open PDFs; strong in CS and biomedicine."
    )
    attribution = "Data from Semantic Scholar (semanticscholar.org), Allen Institute for AI."
    terms_url = "https://www.semanticscholar.org/product/api/license"
    min_interval_s: float = 1.1
    max_page_size = 1000
    aliases: tuple[str, ...] = ("semantic scholar", "semanticscholar", "s2")

    def __init__(self, *, api_key: str | None = None):
        self._api_key = api_key
        if not api_key:
            self.min_interval_s = 3.0  # be gentle with the shared public pool

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        params = {"query": r.text, "fields": FIELDS, "sort": "citationCount:desc"}
        applied: dict[str, Any] = {}
        limitations = list(r.limitations)
        if filters.date_from or filters.date_to:
            params["year"] = f"{filters.date_from or ''}-{filters.date_to or ''}"
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        if filters.languages:
            limitations.append("Semantic Scholar has no language filter; the protocol's languages were not applied.")
        limitations.append(
            "Semantic Scholar bulk search is ordered by citation count, not relevance: when the result is capped, the "
            "most-cited matching papers were retrieved."
        )
        return ProviderQuery(self.key, self.endpoint, r.text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        params: dict[str, Any] = dict(query.params)
        if cursor:
            params["token"] = cursor
        headers = {"x-api-key": self._api_key} if self._api_key else None
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s, headers=headers)
        if not isinstance(body, dict):
            raise ProviderError(self.label, "malformed", "Semantic Scholar returned an unexpected response shape")
        data = body.get("data")
        if data is None:
            data = []
        if not isinstance(data, list):
            raise ProviderError(self.label, "malformed", "Semantic Scholar returned an unexpected response shape")
        records = [r for r in data if isinstance(r, dict)]
        nxt = body.get("token") if records else None
        total = body.get("total")
        return Page(records, str(nxt) if nxt else None, int(total) if isinstance(total, int) else None)

    def record_id(self, raw: dict[str, Any]) -> str:
        pid = raw.get("paperId")
        if not isinstance(pid, str) or not pid:
            raise RecordError("Semantic Scholar record without a paperId")
        return pid[:64]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        ext = _d(raw.get("externalIds"))
        doi = norm_doi(ext.get("DOI"))
        oa_pdf = _d(raw.get("openAccessPdf"))
        pdf = oa_pdf.get("url") or None
        is_oa = raw.get("isOpenAccess")
        oa_status = "open" if (is_oa is True or pdf) else "closed" if is_oa is False else "unknown"
        types = [str(t) for t in raw.get("publicationTypes") or [] if t]
        venue = raw.get("venue") or _d(raw.get("journal")).get("name")
        authors = [
            Author(name=str(a.get("name"))[:300])
            for a in raw.get("authors") or []
            if isinstance(a, dict) and a.get("name")
        ]
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(raw.get("title")),
            authors=authors,
            abstract=clean_text(raw.get("abstract"), MAX_ABSTRACT),
            year=year(raw.get("year") or raw.get("publicationDate")),
            venue=str(venue)[:500] if venue else None,
            publication_type=types[0] if types else None,
            keywords=[str(f)[:120] for f in raw.get("fieldsOfStudy") or [] if f],
            doi=doi,
            pmid=norm_pmid(ext.get("PubMed")),
            pmcid=norm_pmcid(ext.get("PubMedCentral")),
            arxiv_id=norm_arxiv(ext.get("ArXiv")),
            other_ids={"s2": self.record_id(raw)},
            is_preprint=bool(ext.get("ArXiv")) and not doi,
            oa_status=oa_status,
            oa_url=pdf if oa_status == "open" else None,
            pdf_url=pdf,
            source_url=raw.get("url") or (f"https://doi.org/{doi}" if doi else None),
        )
        return finish(rec)
