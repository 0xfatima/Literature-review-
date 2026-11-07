"""HAL, the French national open archive (https://api.archives-ouvertes.fr/docs/search).

Solr syntax (AND / OR / NOT, "phrases", * wildcard); start/rows paging. Most records have an open full text. No key.
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

ENDPOINT = "https://api.archives-ouvertes.fr/search/"
FIELDS = (
    "docid,halId_s,title_s,abstract_s,authFullName_s,producedDateY_i,doiId_s,journalTitle_s,uri_s,docType_s,"
    "language_s,keyword_s,fileMain_s,openAccess_bool"
)
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=True, name="HAL")


class HALSource:
    endpoint: str = ENDPOINT
    key = "hal"
    label = "HAL"
    tier = "open_access"
    default_on = False
    description = "French national open archive: articles, theses and preprints, mostly with open full texts."
    attribution = "Metadata from HAL (hal.science), CCSD."
    terms_url = "https://api.archives-ouvertes.fr/docs"
    min_interval_s: float = 0.5
    max_page_size = 200
    aliases: tuple[str, ...] = ("hal", "hal open science", "hal archives ouvertes")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        params = {"q": r.text, "wt": "json", "fl": FIELDS}
        applied: dict[str, Any] = {}
        fq: list[str] = []
        if filters.date_from or filters.date_to:
            fq.append(f"producedDateY_i:[{filters.date_from or '*'} TO {filters.date_to or '*'}]")
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        if filters.languages:
            fq.append("language_s:(" + " OR ".join(i1 for i1, _ in filters.languages) + ")")
            applied["languages"] = [i1 for i1, _ in filters.languages]
        if fq:
            params["fq"] = " AND ".join(fq)
        return ProviderQuery(self.key, self.endpoint, r.text, params, applied, list(r.limitations))

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
            raise ProviderError(self.label, "malformed", "HAL returned an unexpected response shape")
        docs = [d for d in resp["docs"] if isinstance(d, dict)]
        total = resp.get("numFound") if isinstance(resp.get("numFound"), int) else None
        nxt = start + len(docs)
        return Page(docs, str(nxt) if docs and total is not None and nxt < total else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = raw.get("halId_s") or raw.get("docid")
        if not rid:
            raise RecordError("HAL record without an id")
        return str(rid)[:64]

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        doi = norm_doi(raw.get("doiId_s"))
        pdf = raw.get("fileMain_s") if isinstance(raw.get("fileMain_s"), str) else None
        is_oa = raw.get("openAccess_bool")
        oa_status = "open" if (is_oa is True or pdf) else "unknown"
        lang = first(raw.get("language_s"))
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(first(raw.get("title_s"))),
            authors=[Author(name=str(a)[:300]) for a in as_list(raw.get("authFullName_s")) if a],
            abstract=clean_text(first(raw.get("abstract_s")), MAX_ABSTRACT),
            year=year(raw.get("producedDateY_i")),
            venue=str(raw["journalTitle_s"])[:500] if raw.get("journalTitle_s") else None,
            publication_type=str(raw["docType_s"]) if raw.get("docType_s") else None,
            keywords=[str(k)[:120] for k in as_list(raw.get("keyword_s")) if k],
            language=str(lang)[:2] if lang else None,
            doi=doi,
            other_ids={"hal": self.record_id(raw)},
            is_preprint=str(raw.get("docType_s")) in ("UNDEFINED", "PREPRINT"),
            oa_status=oa_status,
            oa_url=raw.get("uri_s") if oa_status == "open" else None,
            pdf_url=pdf,
            source_url=raw.get("uri_s") or (f"https://doi.org/{doi}" if doi else None),
        )
        return finish(rec)
