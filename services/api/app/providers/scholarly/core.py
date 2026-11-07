"""CORE (https://api.core.ac.uk/docs/v3): the world's largest aggregator of open-access research papers.

Needs a free API key (CORE_API_KEY, Bearer header at request time, never stored); without one the adapter is not
offered. Query language supports AND / OR / NOT, "phrases" and field filters (yearPublished>=2015). limit/offset
paging (limit <= 100). The free tier allows only a few requests per minute, so requests are spaced 6.5 s apart.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly._common import as_list, first
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
    norm_pmid,
    require_title,
    year,
)

ENDPOINT = "https://api.core.ac.uk/v3/search/works"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" AND NOT ", supports_truncation=False, name="CORE")
MAX_RESULTS = 2000


class CORESource:
    endpoint: str = ENDPOINT
    key = "core"
    label = "CORE"
    tier = "open_access"
    default_on = True
    description = "Aggregator of open-access papers from thousands of repositories and journals (full-text links)."
    attribution = "Data from CORE (core.ac.uk), The Open University."
    terms_url = "https://core.ac.uk/terms"
    min_interval_s: float = 6.5
    max_page_size = 100
    aliases: tuple[str, ...] = ("core", "core.ac.uk", "core ac uk")

    def __init__(self, *, api_key: str):
        self._api_key = api_key

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        text = f"({r.text})"
        applied: dict[str, Any] = {}
        if filters.date_from:
            text += f" AND yearPublished>={filters.date_from}"
            applied["date_from"] = filters.date_from
        if filters.date_to:
            text += f" AND yearPublished<={filters.date_to}"
            applied["date_to"] = filters.date_to
        limitations = list(r.limitations)
        if filters.languages:
            limitations.append("Languages were not applied in CORE (language metadata is often missing).")
        return ProviderQuery(self.key, self.endpoint, text, {"q": text}, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        offset = int(cursor or 0)
        limit = min(page_size, self.max_page_size)
        body = http.get_json(
            self.label,
            query.endpoint,
            {**query.params, "limit": limit, "offset": offset},
            min_interval_s=self.min_interval_s,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ProviderError(self.label, "malformed", "CORE returned an unexpected response shape")
        records = [r for r in body["results"] if isinstance(r, dict)]
        total = body.get("totalHits") if isinstance(body.get("totalHits"), int) else None
        nxt = offset + len(records)
        more = records and total is not None and nxt < min(total, MAX_RESULTS)
        return Page(records, str(nxt) if more else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = raw.get("id")
        if rid is None or str(rid) == "":
            raise RecordError("CORE record without an id")
        return str(rid)[:64]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        rid = self.record_id(raw)
        doi = norm_doi(raw.get("doi"))
        pdf = raw.get("downloadUrl") if isinstance(raw.get("downloadUrl"), str) and raw.get("downloadUrl") else None
        ids = {str(_d(i).get("type", "")).lower(): _d(i).get("id") for i in as_list(raw.get("identifiers"))}
        journal = first(raw.get("journals"))
        lang = _d(raw.get("language")).get("code")
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=rid,
            title=require_title(raw.get("title")),
            authors=[
                Author(name=str(_d(a).get("name"))[:300]) for a in as_list(raw.get("authors")) if _d(a).get("name")
            ],
            abstract=clean_text(raw.get("abstract"), MAX_ABSTRACT),
            year=year(raw.get("yearPublished") or raw.get("publishedDate")),
            venue=str(_d(journal).get("title"))[:500] if _d(journal).get("title") else None,
            publication_type=str(raw["documentType"]) if raw.get("documentType") else None,
            keywords=[str(f)[:120] for f in as_list(raw.get("fieldOfStudy")) if f],
            language=str(lang)[:2] if lang else None,
            doi=doi,
            pmid=norm_pmid(ids.get("pubmed")),
            arxiv_id=norm_arxiv(raw.get("arxivId")),
            other_ids={"core": rid},
            oa_status="open" if pdf else "unknown",
            oa_url=f"https://core.ac.uk/works/{rid}" if pdf else None,
            pdf_url=pdf,
            source_url=f"https://core.ac.uk/works/{rid}",
        )
        return finish(rec)
