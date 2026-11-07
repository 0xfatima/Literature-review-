"""DOAJ (Directory of Open Access Journals) article search API (https://doaj.org/api/docs).

Every DOAJ article is in a fully open-access journal. The query is an Elasticsearch query string in the URL path
(AND / OR / NOT, "phrases", * wildcard); `page`/`pageSize` paging (pageSize <= 100). No key needed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

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
    norm_doi,
    require_title,
    year,
)

ENDPOINT = "https://doaj.org/api/search/articles/"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=True, name="DOAJ")
MAX_RESULTS = 1000  # DOAJ refuses deeper paging


class DOAJSource:
    endpoint: str = ENDPOINT
    key = "doaj"
    label = "DOAJ"
    tier = "open_access"
    default_on = True
    description = "Articles from peer-reviewed, fully open-access journals (all freely readable)."
    attribution = "Metadata from the Directory of Open Access Journals (doaj.org), CC0."
    terms_url = "https://doaj.org/api/docs"
    min_interval_s: float = 0.6
    max_page_size = 100
    aliases: tuple[str, ...] = ("doaj", "directory of open access journals")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        text = f"({r.text})" if " OR " in r.text else r.text
        applied: dict[str, Any] = {}
        limitations = list(r.limitations)
        if filters.date_from or filters.date_to:
            text += f" AND bibjson.year:[{filters.date_from or '*'} TO {filters.date_to or '*'}]"
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        if filters.languages:
            limitations.append("DOAJ language data describes the journal, not the article; languages were not applied.")
        url = self.endpoint + quote(text, safe="")
        return ProviderQuery(self.key, url, text, {}, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        page = int(cursor or 1)
        size = min(page_size, self.max_page_size)
        body = http.get_json(
            self.label, query.endpoint, {"page": page, "pageSize": size}, min_interval_s=self.min_interval_s
        )
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ProviderError(self.label, "malformed", "DOAJ returned an unexpected response shape")
        records = [r for r in body["results"] if isinstance(r, dict)]
        total = body.get("total") if isinstance(body.get("total"), int) else None
        more = records and page * size < min(total if total is not None else 0, MAX_RESULTS)
        return Page(records, str(page + 1) if more else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid:
            raise RecordError("DOAJ record without an id")
        return rid[:64]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        b = _d(raw.get("bibjson"))
        ids = {str(i.get("type", "")).lower(): i.get("id") for i in b.get("identifier") or [] if isinstance(i, dict)}
        doi = norm_doi(ids.get("doi"))
        links = [ln for ln in b.get("link") or [] if isinstance(ln, dict) and ln.get("url")]
        full = next((str(ln["url"]) for ln in links if str(ln.get("type")) == "fulltext"), None)
        pdf = next((str(ln["url"]) for ln in links if "pdf" in str(ln.get("content_type", "")).lower()), None)
        journal = _d(b.get("journal"))
        langs = journal.get("language") or []
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(b.get("title")),
            authors=[
                Author(name=str(a["name"])[:300])
                for a in b.get("author") or []
                if isinstance(a, dict) and a.get("name")
            ],
            abstract=clean_text(b.get("abstract"), MAX_ABSTRACT),
            year=year(b.get("year")),
            venue=str(journal.get("title"))[:500] if journal.get("title") else None,
            publication_type="article",
            keywords=[str(k)[:120] for k in b.get("keywords") or [] if k],
            language=str(langs[0]).lower()[:2] if isinstance(langs, list) and len(langs) == 1 else None,
            doi=doi,
            oa_status="open",
            oa_url=full or (f"https://doi.org/{doi}" if doi else None),
            pdf_url=pdf,
            license=str(
                _d(
                    (b.get("license") or [{}])[0] if isinstance(b.get("license"), list) and b.get("license") else {}
                ).get("type")
                or ""
            )
            or None,
            source_url=full or (f"https://doi.org/{doi}" if doi else f"https://doaj.org/article/{self.record_id(raw)}"),
        )
        return finish(rec)
