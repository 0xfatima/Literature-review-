"""ERIC, the U.S. Department of Education's education research database (https://eric.ed.gov/?api).

Supports AND / OR / NOT and "phrases"; start/rows paging. Records with `e_fulltextauth = 1` have a free PDF on
files.eric.ed.gov. No key needed.
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
    norm_doi,
    require_title,
    year,
)

ENDPOINT = "https://api.ies.ed.gov/eric/"
FIELDS = (
    "id,title,author,description,publicationdateyear,source,subject,url,e_fulltextauth,peerreviewed,language,"
    "publicationtype"
)
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=False, name="ERIC")


class ERICSource:
    endpoint: str = ENDPOINT
    key = "eric"
    label = "ERIC"
    tier = "open_access"
    default_on = False
    description = "Education research (journals, reports, theses) from the U.S. Institute of Education Sciences."
    attribution = "Data from ERIC (eric.ed.gov), Institute of Education Sciences."
    terms_url = "https://eric.ed.gov/?api"
    min_interval_s: float = 1.0
    max_page_size = 200
    aliases: tuple[str, ...] = ("eric", "education resources information center")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        text = r.text
        applied: dict[str, Any] = {}
        if filters.date_from or filters.date_to:
            text = f"({text}) AND publicationdateyear:[{filters.date_from or 1900} TO {filters.date_to or 2100}]"
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        limitations = list(r.limitations)
        if filters.languages:
            limitations.append("Languages were not applied in ERIC (mostly English-language records).")
        return ProviderQuery(
            self.key, self.endpoint, text, {"search": text, "format": "json", "fields": FIELDS}, applied, limitations
        )

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        start = int(cursor or 0)
        body = http.get_json(
            self.label,
            query.endpoint,
            {**query.params, "start": start, "rows": min(page_size, self.max_page_size)},
            min_interval_s=self.min_interval_s,
        )
        resp = _d(body.get("response")) if isinstance(body, dict) else {}
        if not isinstance(resp.get("docs"), list):
            raise ProviderError(self.label, "malformed", "ERIC returned an unexpected response shape")
        docs = [d for d in resp["docs"] if isinstance(d, dict)]
        total = resp.get("numFound") if isinstance(resp.get("numFound"), int) else None
        nxt = start + len(docs)
        return Page(docs, str(nxt) if docs and total is not None and nxt < total else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid:
            raise RecordError("ERIC record without an id")
        return rid[:32]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        rid = self.record_id(raw)
        free = str(raw.get("e_fulltextauth")) == "1"
        url = first(raw.get("url"))
        doi = norm_doi(url) if isinstance(url, str) and "doi.org" in url else None
        pdf = f"https://files.eric.ed.gov/fulltext/{rid}.pdf" if free and rid.startswith("ED") else None
        types = [str(t) for t in as_list(raw.get("publicationtype")) if t]
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=rid,
            title=require_title(raw.get("title")),
            authors=[Author(name=str(a)[:300]) for a in as_list(raw.get("author")) if a],
            abstract=clean_text(raw.get("description"), MAX_ABSTRACT),
            year=year(raw.get("publicationdateyear")),
            venue=str(raw["source"])[:500] if raw.get("source") else None,
            publication_type=types[0] if types else None,
            keywords=[str(s)[:120] for s in as_list(raw.get("subject")) if s],
            language="en" if "English" in [str(x) for x in as_list(raw.get("language"))] else None,
            doi=doi,
            other_ids={"eric": rid},
            oa_status="open" if free else "unknown",
            oa_url=f"https://eric.ed.gov/?id={rid}" if free else None,
            pdf_url=pdf,
            source_url=url if isinstance(url, str) and url.startswith("http") else f"https://eric.ed.gov/?id={rid}",
        )
        return finish(rec)
