"""OpenAIRE Graph API, research products (https://graph.openaire.eu/docs/apis/graph-api/).

Europe's open scholarly graph (publications aggregated from repositories, journals and registries) with access
rights per record. `search` accepts AND / OR / NOT; cursor paging (`cursor=*`, pageSize <= 100). No key needed.
The endpoint can be moved to a newer API version with SCHOLARLY_ENDPOINTS=openaire=https://... .
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
    norm_pmcid,
    norm_pmid,
    require_title,
    year,
)

ENDPOINT = "https://api.openaire.eu/graph/v1/researchProducts"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=False, name="OpenAIRE")


class OpenAIRESource:
    endpoint: str = ENDPOINT
    key = "openaire"
    label = "OpenAIRE"
    tier = "open_access"
    default_on = False
    description = "European open scholarly graph: repositories, journals and registries, with access rights."
    attribution = "Data from the OpenAIRE Graph (graph.openaire.eu), CC-BY."
    terms_url = "https://graph.openaire.eu/docs/apis/terms"
    min_interval_s: float = 1.0
    max_page_size = 100
    aliases: tuple[str, ...] = ("openaire", "open aire", "openaire explore")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        params = {"search": r.text, "type": "publication"}
        applied: dict[str, Any] = {}
        if filters.date_from:
            params["fromPublicationDate"] = f"{filters.date_from}-01-01"
            applied["date_from"] = filters.date_from
        if filters.date_to:
            params["toPublicationDate"] = f"{filters.date_to}-12-31"
            applied["date_to"] = filters.date_to
        limitations = list(r.limitations)
        if filters.languages:
            limitations.append("Languages were not applied in OpenAIRE (language metadata is often missing).")
        return ProviderQuery(self.key, self.endpoint, r.text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        params = {**query.params, "pageSize": min(page_size, self.max_page_size), "cursor": cursor or "*"}
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ProviderError(self.label, "malformed", "OpenAIRE returned an unexpected response shape")
        header = _d(body.get("header"))
        records = [r for r in body["results"] if isinstance(r, dict)]
        nxt = header.get("nextCursor") if records else None
        total = header.get("numFound")
        return Page(records, str(nxt) if nxt else None, int(total) if isinstance(total, int) else None)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid:
            raise RecordError("OpenAIRE record without an id")
        return rid[:128]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        pids: dict[str, str] = {}
        for p in as_list(raw.get("pids") or raw.get("pid")):
            if isinstance(p, dict) and p.get("scheme") and p.get("value"):
                pids.setdefault(str(p["scheme"]).lower(), str(p["value"]))
        urls = [
            u
            for inst in as_list(raw.get("instances"))
            for u in as_list(_d(inst).get("urls"))
            if isinstance(u, str) and u.startswith("http")
        ]
        access = str(_d(raw.get("bestAccessRight")).get("label") or "").lower()
        oa_status = "open" if "open" in access else "closed" if access in ("closed access", "restricted") else "unknown"
        doi = norm_doi(pids.get("doi"))
        subjects = []
        for s in as_list(raw.get("subjects")):
            v = _d(_d(s).get("subject")).get("value") or _d(s).get("value")
            if v:
                subjects.append(str(v)[:120])
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(raw.get("mainTitle") or first(raw.get("title"))),
            authors=[
                Author(name=str(_d(a).get("fullName") or _d(a).get("name"))[:300])
                for a in as_list(raw.get("authors"))
                if _d(a).get("fullName") or _d(a).get("name")
            ],
            abstract=clean_text(first(raw.get("descriptions") or raw.get("description")), MAX_ABSTRACT),
            year=year(raw.get("publicationDate")),
            venue=str(_d(raw.get("container")).get("name"))[:500] if _d(raw.get("container")).get("name") else None,
            publication_type=str(raw.get("type")) if raw.get("type") else None,
            keywords=subjects,
            language=str(_d(raw.get("language")).get("code") or "")[:2] or None,
            doi=doi,
            pmid=norm_pmid(pids.get("pmid")),
            pmcid=norm_pmcid(pids.get("pmc")),
            oa_status=oa_status,
            oa_url=urls[0] if oa_status == "open" and urls else None,
            source_url=(f"https://doi.org/{doi}" if doi else None) or (urls[0] if urls else None),
        )
        return finish(rec)
