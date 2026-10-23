"""Europe PMC REST search (https://europepmc.org/RestfulWebService). Covers PubMed/MEDLINE (source MED), PubMed
Central (PMC), preprints (PPR) and more, with open-access flags and full-text links.

Rules followed: cursorMark paging, pageSize <= 1000, `resultType=core` (abstracts, links), conservative request
spacing. Filters are part of Europe PMC's query syntax, so the stored query text includes them: it is exactly what
was sent. Truncation (`*`) and Boolean operators are native.
"""

from __future__ import annotations

from typing import Any

from app.providers.scholarly.base import Page, ProviderQuery
from app.providers.scholarly.base import as_dict as _d
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
    norm_pmcid,
    norm_pmid,
    require_title,
    year,
)

ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=True, name="Europe PMC")
OPEN_CODES = frozenset({"OA", "F"})  # open access, free to read (S = subscription, R = registration)


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class EuropePMCSource:
    key = "europepmc"
    label = "Europe PMC"
    tier = "open_access"
    description = "Biomedical and life-science literature, including PubMed/MEDLINE, PubMed Central and preprints."
    attribution = "Data from Europe PMC (europepmc.org), including PubMed/MEDLINE records from the US NLM."
    terms_url = "https://europepmc.org/Help#termsofuse"
    min_interval_s: float = 0.2
    max_page_size = 1000
    aliases: tuple[str, ...] = (
        "europe pmc",
        "europepmc",
        "europe pubmed central",
        "pubmed",
        "medline",
        "pmc",
        "pubmed central",
    )

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        text = f"({r.text})"
        applied: dict[str, Any] = {}
        if filters.date_from or filters.date_to:
            lo, hi = filters.date_from or 1000, filters.date_to or 3000
            text += f" AND (PUB_YEAR:[{lo} TO {hi}])"
            applied.update(date_from=filters.date_from, date_to=filters.date_to)
        if filters.languages:
            text += " AND (" + " OR ".join(f'LANG:"{iso2}"' for _, iso2 in filters.languages) + ")"
            applied["languages"] = [iso1 for iso1, _ in filters.languages]
        params = {"query": text, "format": "json", "resultType": "core"}
        return ProviderQuery(self.key, ENDPOINT, text, params, applied, list(r.limitations))

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        current = cursor or "*"
        params = {**query.params, "pageSize": min(page_size, self.max_page_size), "cursorMark": current}
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s)
        if not isinstance(body, dict):
            raise ProviderError(self.label, "malformed", "Europe PMC returned an unexpected response shape")
        rl = body.get("resultList")
        results = rl.get("result") if isinstance(rl, dict) else None
        if results is None and body.get("hitCount") == 0:
            results = []
        if not isinstance(results, list):
            raise ProviderError(self.label, "malformed", "Europe PMC returned an unexpected response shape")
        results = [r for r in results if isinstance(r, dict)]
        nxt = body.get("nextCursorMark")
        if not results or not nxt or nxt == current:
            nxt = None
        total = body.get("hitCount")
        return Page(results, str(nxt) if nxt else None, int(total) if isinstance(total, int) else None)

    def record_id(self, raw: dict[str, Any]) -> str:
        src, rid = raw.get("source"), raw.get("id")
        if not src or not rid:
            raise RecordError("Europe PMC record without source/id")
        return f"{src}:{rid}"

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        authors = []
        al = _d(raw.get("authorList"))
        for a in _as_list(al.get("author")):
            if not isinstance(a, dict):
                continue
            name = a.get("fullName") or a.get("collectiveName")
            if name:
                orcid = (a.get("authorId") or {}).get("value") if isinstance(a.get("authorId"), dict) else None
                authors.append(Author(name=str(name)[:300], family=a.get("lastName"), orcid=orcid))
        ji = _d(raw.get("journalInfo"))
        journal = _d(ji.get("journal"))
        ptl = _d(raw.get("pubTypeList"))
        pubtypes = [str(p) for p in _as_list(ptl.get("pubType")) if p]
        kl = _d(raw.get("keywordList"))
        source = str(raw.get("source") or "")
        ftl = _d(raw.get("fullTextUrlList"))
        pdf_url = html_url = None
        for u in _as_list(ftl.get("fullTextUrl")):
            if not isinstance(u, dict) or u.get("availabilityCode") not in OPEN_CODES or not u.get("url"):
                continue
            if u.get("documentStyle") == "pdf" and pdf_url is None:
                pdf_url = u["url"]
            elif u.get("documentStyle") in ("html", "doi") and html_url is None:
                html_url = u["url"]
        is_oa = raw.get("isOpenAccess") == "Y" or bool(pdf_url or html_url)
        pmcid = norm_pmcid(raw.get("pmcid"))
        if is_oa and html_url is None and pmcid:
            html_url = f"https://europepmc.org/article/PMC/{pmcid}"
        lang = raw.get("language")
        resolved = resolve_language(str(lang)) if lang else None
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=self.record_id(raw),
            title=require_title(raw.get("title")),
            authors=authors,
            abstract=clean_text(raw.get("abstractText"), MAX_ABSTRACT),
            year=year(raw.get("pubYear") or ji.get("yearOfPublication")),
            venue=str(journal["title"])[:500] if journal.get("title") else None,
            publication_type=pubtypes[0] if pubtypes else None,
            keywords=[str(k)[:120] for k in _as_list(kl.get("keyword")) if k],
            language=resolved[0] if resolved else None,
            doi=norm_doi(raw.get("doi")),
            pmid=norm_pmid(raw.get("pmid")),
            pmcid=pmcid,
            other_ids={"europepmc": self.record_id(raw)},
            is_preprint=source == "PPR" or any("preprint" in p.lower() for p in pubtypes),
            is_retracted=True if any("retracted publication" in p.lower() for p in pubtypes) else None,
            # "N" only means "not in Europe PMC's open-access subset"; a free copy may exist elsewhere.
            oa_status="open" if is_oa else "unknown",
            oa_url=html_url if is_oa else None,
            pdf_url=pdf_url if is_oa else None,
            license=str(raw["license"])[:100] if raw.get("license") else None,
            source_url=f"https://europepmc.org/article/{source}/{raw.get('id')}",
        )
        return finish(rec)
