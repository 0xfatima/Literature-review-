"""OpenAlex works API (https://docs.openalex.org). Open metadata (CC0), with open-access status and OA locations.

Rules followed: cursor paging (`cursor=*`), `per-page` <= 200, `select=` to fetch only the fields used, the polite
pool via `mailto` and an optional API key (both added at request time, never stored), and request spacing well under
the published per-second limit. Boolean search (AND / OR / NOT, quoted phrases) is supported by the `search`
parameter; truncation is not (OpenAlex stems words itself).
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
    norm_doi,
    norm_openalex,
    norm_pmcid,
    norm_pmid,
    require_title,
    year,
)

ENDPOINT = "https://api.openalex.org/works"
SELECT = (
    "id,doi,title,display_name,publication_year,ids,language,type,primary_location,best_oa_location,open_access,"
    "authorships,abstract_inverted_index,keywords,is_retracted"
)
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=False, name="OpenAlex")


def rebuild_abstract(inverted: Any) -> str | None:
    """OpenAlex ships abstracts as {word: [positions]} (licensing reasons); put the words back in order."""
    if not isinstance(inverted, dict) or not inverted:
        return None
    slots: dict[int, str] = {}
    for word, positions in inverted.items():
        if isinstance(positions, list):
            for p in positions:
                if isinstance(p, int) and 0 <= p < 20_000:
                    slots[p] = str(word)
    return clean_text(" ".join(slots[i] for i in sorted(slots)), MAX_ABSTRACT)


class OpenAlexSource:
    key = "openalex"
    label = "OpenAlex"
    tier = "open_access"
    description = "Broad multidisciplinary index with open-access status and links to open copies."
    attribution = "Metadata from OpenAlex (openalex.org), released under CC0."
    terms_url = "https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication"
    min_interval_s: float = 0.15
    max_page_size = 200
    aliases: tuple[str, ...] = ("openalex", "open alex")

    def __init__(self, *, mailto: str | None = None, api_key: str | None = None):
        self._mailto, self._api_key = mailto, api_key

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        parts: list[str] = []
        applied: dict[str, Any] = {}
        limitations = list(r.limitations)
        if filters.date_from:
            parts.append(f"from_publication_date:{filters.date_from}-01-01")
            applied["date_from"] = filters.date_from
        if filters.date_to:
            parts.append(f"to_publication_date:{filters.date_to}-12-31")
            applied["date_to"] = filters.date_to
        if filters.languages:
            parts.append("language:" + "|".join(iso1 for iso1, _ in filters.languages))
            applied["languages"] = [iso1 for iso1, _ in filters.languages]
            limitations.append(
                "OpenAlex language metadata is inferred and sometimes missing; records without a "
                "language are excluded by this filter."
            )
        params = {"search": r.text, "select": SELECT}
        if parts:
            params["filter"] = ",".join(parts)
        return ProviderQuery(self.key, ENDPOINT, r.text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        params: dict[str, Any] = {
            **query.params,
            "per-page": min(page_size, self.max_page_size),
            "cursor": cursor or "*",
        }
        if self._mailto:
            params["mailto"] = self._mailto
        if self._api_key:
            params["api_key"] = self._api_key
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ProviderError(self.label, "malformed", "OpenAlex returned an unexpected response shape")
        meta = _d(body.get("meta"))
        results = [r for r in body["results"] if isinstance(r, dict)]
        nxt = meta.get("next_cursor") if results else None
        total = meta.get("count")
        return Page(results, str(nxt) if nxt else None, int(total) if isinstance(total, int) else None)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = norm_openalex(raw.get("id"))
        if not rid:
            raise RecordError("OpenAlex record without an id")
        return rid

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        ids = _d(raw.get("ids"))
        authors = []
        for a in raw.get("authorships") or []:
            if not isinstance(a, dict):
                continue
            au = _d(a.get("author"))
            name = au.get("display_name") or a.get("raw_author_name")
            if name:
                authors.append(Author(name=str(name)[:300], orcid=au.get("orcid")))
        pl = _d(raw.get("primary_location"))
        src = _d(pl.get("source"))
        oa = _d(raw.get("open_access"))
        best = _d(raw.get("best_oa_location"))
        is_oa = oa.get("is_oa")
        oa_status = "open" if is_oa is True else "closed" if is_oa is False else "unknown"
        doi = norm_doi(raw.get("doi") or ids.get("doi"))
        work_type = raw.get("type")
        venue = src.get("display_name")
        preprint = work_type == "preprint" or (
            pl.get("version") == "submittedVersion" and src.get("type") == "repository"
        )
        other = {"mag": str(ids["mag"])} if ids.get("mag") else {}
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(raw.get("title") or raw.get("display_name")),
            authors=authors,
            abstract=rebuild_abstract(raw.get("abstract_inverted_index")),
            year=year(raw.get("publication_year")),
            venue=str(venue)[:500] if venue else None,
            publication_type=str(work_type) if work_type else None,
            keywords=[
                str(k.get("display_name"))[:120]
                for k in raw.get("keywords") or []
                if isinstance(k, dict) and k.get("display_name")
            ],
            language=raw.get("language") if isinstance(raw.get("language"), str) else None,
            doi=doi,
            pmid=norm_pmid(ids.get("pmid")),
            pmcid=norm_pmcid(ids.get("pmcid")),
            openalex_id=self.record_id(raw),
            other_ids=other,
            is_preprint=bool(preprint),
            is_retracted=raw.get("is_retracted") if isinstance(raw.get("is_retracted"), bool) else None,
            oa_status=oa_status,
            oa_url=(oa.get("oa_url") or best.get("landing_page_url")) if oa_status == "open" else None,
            pdf_url=best.get("pdf_url") if oa_status == "open" else None,
            license=best.get("license") or pl.get("license"),
            source_url=pl.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else raw.get("id")),
        )
        return finish(rec)
