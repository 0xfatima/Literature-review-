"""PLOS search API (https://api.plos.org/solr/faq/). All PLOS articles are open access (CC BY).

Solr syntax: AND / OR / NOT, "phrases", * wildcard. Rate rule: at most 10 requests/minute and 7200/day, so requests
are spaced 6.5 s apart across workers. No key needed.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly._common import first
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

ENDPOINT = "https://api.plos.org/search"
FIELDS = "id,title_display,abstract,author_display,journal,publication_date,article_type,subject"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=True, name="PLOS")


class PLOSSource:
    endpoint: str = ENDPOINT
    key = "plos"
    label = "PLOS"
    tier = "open_access"
    default_on = False
    description = "All PLOS journals (PLOS ONE, PLOS Medicine, ...): open-access full texts. Slow by its rate rules."
    attribution = "Metadata from the Public Library of Science (plos.org), CC BY."
    terms_url = "https://api.plos.org/terms-of-use/"
    min_interval_s: float = 6.5
    max_page_size = 100
    aliases: tuple[str, ...] = ("plos", "plos one", "public library of science")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        params = {"q": r.text, "fl": FIELDS, "wt": "json", "fq": "doc_type:full"}
        applied: dict[str, Any] = {}
        if filters.date_from or filters.date_to:
            lo = f"{filters.date_from}-01-01T00:00:00Z" if filters.date_from else "*"
            hi = f"{filters.date_to}-12-31T23:59:59Z" if filters.date_to else "*"
            params["fq"] += f" AND publication_date:[{lo} TO {hi}]"
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        limitations = list(r.limitations)
        if filters.languages:
            limitations.append("PLOS publishes in English only; the language filter was not needed.")
        return ProviderQuery(self.key, self.endpoint, r.text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        start = int(cursor or 0)
        rows = min(page_size, self.max_page_size)
        body = http.get_json(
            self.label,
            query.endpoint,
            {**query.params, "start": start, "rows": rows},
            min_interval_s=self.min_interval_s,
        )
        resp = _d(body.get("response")) if isinstance(body, dict) else {}
        if not isinstance(resp.get("docs"), list):
            raise ProviderError(self.label, "malformed", "PLOS returned an unexpected response shape")
        docs = [d for d in resp["docs"] if isinstance(d, dict)]
        total = resp.get("numFound") if isinstance(resp.get("numFound"), int) else None
        nxt = start + len(docs)
        return Page(docs, str(nxt) if docs and total is not None and nxt < total else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        doi = norm_doi(raw.get("id"))
        if not doi:
            raise RecordError("PLOS record without a DOI")
        return doi

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        doi = self.record_id(raw)
        url = f"https://journals.plos.org/plosone/article?id={doi}"
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=doi,
            title=require_title(raw.get("title_display")),
            authors=[Author(name=str(a)[:300]) for a in raw.get("author_display") or [] if a],
            abstract=clean_text(first(raw.get("abstract")), MAX_ABSTRACT),
            year=year(raw.get("publication_date")),
            venue=str(raw["journal"])[:500] if raw.get("journal") else None,
            publication_type=str(raw["article_type"]) if raw.get("article_type") else None,
            keywords=[str(s).strip("/").split("/")[-1][:120] for s in raw.get("subject") or [] if s][:20],
            language="en",
            doi=doi,
            oa_status="open",
            oa_url=f"https://doi.org/{doi}",
            license="https://creativecommons.org/licenses/by/4.0/",
            source_url=url,
        )
        return finish(rec)
