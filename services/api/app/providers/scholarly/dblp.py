"""dblp computer science bibliography (https://dblp.org/faq/How+to+use+the+dblp+search+API.html).

Keyword search only (prefix matching, no Boolean NOT, no abstracts), so it is an *additional* source for computer
science reviews: the main term of each concept is sent; `f` (offset) / `h` (hits, <= 1000) paging. No key needed.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly._common import as_list, keyword_limitations, keyword_text
from app.providers.scholarly.base import Page, ProviderQuery
from app.providers.scholarly.base import as_dict as _d
from app.providers.scholarly.http import HttpFetcher, ProviderError
from app.search import query as q
from app.search.derive import FilterPlan
from app.search.records import Author, RecordError, ScholarlyRecord, finish, norm_arxiv, norm_doi, require_title, year

ENDPOINT = "https://dblp.org/search/publ/api"


class DBLPSource:
    endpoint: str = ENDPOINT
    key = "dblp"
    label = "dblp"
    tier = "additional"
    default_on = False
    description = "Computer science bibliography (conferences and journals). Titles only, no abstracts."
    attribution = "Metadata from dblp (dblp.org), CC0."
    terms_url = "https://dblp.org/faq/"
    min_interval_s: float = 1.5
    max_page_size = 1000
    aliases: tuple[str, ...] = ("dblp", "dblp computer science bibliography")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        text = keyword_text(expression).replace('"', "")
        limitations = keyword_limitations(self.label, expression)
        limitations.append("dblp has no abstracts; screening relies on titles only.")
        if filters.date_from or filters.date_to or filters.languages:
            limitations.append("dblp offers no date or language filter in its search API; none was applied.")
        return ProviderQuery(self.key, self.endpoint, text, {"q": text, "format": "json"}, {}, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        start = int(cursor or 0)
        body = http.get_json(
            self.label,
            query.endpoint,
            {**query.params, "f": start, "h": min(page_size, self.max_page_size)},
            min_interval_s=self.min_interval_s,
        )
        hits = _d(_d(_d(body).get("result")).get("hits"))
        if not hits and not isinstance(body, dict):
            raise ProviderError(self.label, "malformed", "dblp returned an unexpected response shape")
        recs: list[dict[str, Any]] = [
            h["info"] for h in as_list(hits.get("hit")) if isinstance(h, dict) and isinstance(h.get("info"), dict)
        ]
        try:
            total: int | None = int(str(hits.get("@total")))
        except (TypeError, ValueError):
            total = None
        nxt = start + len(recs)
        return Page(recs, str(nxt) if recs and total is not None and nxt < min(total, 10_000) else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise RecordError("dblp record without a key")
        return key[:128]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        authors = []
        for a in as_list(_d(raw.get("authors")).get("author")):
            name = a.get("text") if isinstance(a, dict) else a
            if name:
                authors.append(Author(name=str(name)[:300]))
        doi = norm_doi(raw.get("doi"))
        ee = raw.get("ee") if isinstance(raw.get("ee"), str) else None
        title = str(raw.get("title") or "").rstrip(".")
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(title),
            authors=authors,
            year=year(raw.get("year")),
            venue=str(raw["venue"])[:500] if isinstance(raw.get("venue"), str) else None,
            publication_type=str(raw["type"]) if raw.get("type") else None,
            doi=doi,
            arxiv_id=norm_arxiv(ee) if ee and "arxiv.org" in ee else None,
            other_ids={"dblp": self.record_id(raw)},
            is_preprint=str(raw.get("type")) == "Informal and Other Publications",
            source_url=ee or raw.get("url"),
        )
        return finish(rec)
