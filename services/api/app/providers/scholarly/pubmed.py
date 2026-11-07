"""PubMed via NCBI E-utilities (https://www.ncbi.nlm.nih.gov/books/NBK25499/).

Two requests per page: ESearch (Boolean query, relevance order, ids) then EFetch (PubMed XML with abstracts). Rules:
at most 3 requests/second without an API key, 10 with one (NCBI_API_KEY, sent at request time, never stored), and the
`tool`/`email` identification parameters. PubMed syntax supports AND / OR / NOT, quoted phrases and truncation (*).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405
from typing import Any

from app.providers.scholarly._common import itertext, safe_xml
from app.providers.scholarly.base import Page, ProviderQuery
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

ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DIALECT = q.Dialect(and_op=" AND ", or_op=" OR ", and_not=" NOT ", supports_truncation=True, name="PubMed")
LANG_NAMES = {
    "en": "english",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "pt": "portuguese",
    "it": "italian",
    "nl": "dutch",
    "zh": "chinese",
    "ja": "japanese",
    "ko": "korean",
    "ru": "russian",
    "ar": "arabic",
    "fa": "persian",
    "tr": "turkish",
    "pl": "polish",
}


def article_to_dict(art: ET.Element) -> dict[str, Any]:
    mc = art.find("MedlineCitation")
    a = mc.find("Article") if mc is not None else None
    if mc is None or a is None:
        return {}
    abstract_parts = []
    for t in a.findall("Abstract/AbstractText"):
        txt = itertext(t)
        if txt:
            label = t.get("Label")
            abstract_parts.append(f"{label}: {txt}" if label else txt)
    authors = []
    for au in a.findall("AuthorList/Author"):
        coll = itertext(au.find("CollectiveName"))
        last, fore = itertext(au.find("LastName")), itertext(au.find("ForeName"))
        name = coll or " ".join(x for x in (fore, last) if x)
        if name:
            authors.append({"name": name, "family": last})
    ids = {i.get("IdType"): (i.text or "").strip() for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
    pub = a.find("Journal/JournalIssue/PubDate")
    yr = itertext(pub.find("Year")) if pub is not None else None
    if not yr and pub is not None:
        yr = (itertext(pub.find("MedlineDate")) or "")[:4] or None
    return {
        "pmid": itertext(mc.find("PMID")),
        "title": itertext(a.find("ArticleTitle")),
        "abstract": " ".join(abstract_parts) or None,
        "authors": authors,
        "journal": itertext(a.find("Journal/Title")),
        "year": yr,
        "language": itertext(a.find("Language")),
        "publication_types": [itertext(p) for p in a.findall("PublicationTypeList/PublicationType") if itertext(p)],
        "keywords": [itertext(k) for k in mc.findall("KeywordList/Keyword") if itertext(k)]
        + [itertext(m) for m in mc.findall("MeshHeadingList/MeshHeading/DescriptorName") if itertext(m)],
        "doi": ids.get("doi"),
        "pmcid": ids.get("pmc"),
    }


class PubMedSource:
    endpoint: str = ENDPOINT
    key = "pubmed"
    label = "PubMed"
    tier = "open_access"
    default_on = False  # Europe PMC already includes PubMed; choose this for PubMed's own ranking and syntax
    description = "NCBI's biomedical literature database (MEDLINE) with abstracts; links to free PMC full texts."
    attribution = "Data from PubMed, U.S. National Library of Medicine."
    terms_url = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
    min_interval_s: float = 0.4
    max_page_size = 200
    aliases: tuple[str, ...] = ("pubmed (ncbi)", "ncbi pubmed", "pubmed api", "entrez")

    def __init__(self, *, api_key: str | None = None, email: str | None = None):
        self._api_key, self._email = api_key, email
        if api_key:
            self.min_interval_s = 0.12

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        term = r.text
        applied: dict[str, Any] = {}
        limitations = list(r.limitations)
        params: dict[str, str] = {"db": "pubmed", "retmode": "json", "sort": "relevance"}
        if filters.date_from or filters.date_to:
            params.update(
                {"datetype": "pdat", "mindate": str(filters.date_from or 1800), "maxdate": str(filters.date_to or 3000)}
            )
            applied.update({k: v for k, v in (("date_from", filters.date_from), ("date_to", filters.date_to)) if v})
        langs = [LANG_NAMES[i1] for i1, _ in filters.languages if i1 in LANG_NAMES]
        if langs:
            term = f"({term}) AND (" + " OR ".join(f"{name}[lang]" for name in langs) + ")"
            applied["languages"] = [i1 for i1, _ in filters.languages if i1 in LANG_NAMES]
        params["term"] = term
        return ProviderQuery(self.key, self.endpoint, term, params, applied, limitations)

    def _ident(self) -> dict[str, str]:
        out = {"tool": "systematic-review-scientist"}
        if self._email:
            out["email"] = self._email
        if self._api_key:
            out["api_key"] = self._api_key
        return out

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        start = int(cursor or 0)
        size = min(page_size, self.max_page_size)
        params = {**query.params, "retstart": start, "retmax": size, **self._ident()}
        body = http.get_json(self.label, query.endpoint, params, min_interval_s=self.min_interval_s)
        res = body.get("esearchresult") if isinstance(body, dict) else None
        if not isinstance(res, dict) or not isinstance(res.get("idlist"), list):
            raise ProviderError(self.label, "malformed", "PubMed returned an unexpected search response")
        ids = [str(i) for i in res["idlist"] if str(i).isdigit()]
        try:
            total: int | None = int(str(res.get("count")))
        except (TypeError, ValueError):
            total = None
        if not ids:
            return Page([], None, total)
        xml = http.get_text(
            self.label,
            EFETCH,
            {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", **self._ident()},
            min_interval_s=self.min_interval_s,
        )
        root = safe_xml(self.label, xml)
        records = [d for d in (article_to_dict(a) for a in root.findall("PubmedArticle")) if d.get("pmid")]
        nxt = start + len(ids)
        return Page(records, str(nxt) if total is not None and nxt < total and nxt < 9999 else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        pmid = norm_pmid(raw.get("pmid"))
        if not pmid:
            raise RecordError("PubMed record without a PMID")
        return pmid

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        pmid = self.record_id(raw)
        pmcid = norm_pmcid(raw.get("pmcid"))
        doi = norm_doi(raw.get("doi"))
        types = [str(t) for t in raw.get("publication_types") or []]
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=pmid,
            title=require_title(raw.get("title")),
            authors=[
                Author(name=str(a["name"])[:300], family=a.get("family"))
                for a in raw.get("authors") or []
                if a.get("name")
            ],
            abstract=clean_text(raw.get("abstract"), MAX_ABSTRACT),
            year=year(raw.get("year")),
            venue=str(raw["journal"])[:500] if raw.get("journal") else None,
            publication_type=types[0] if types else None,
            keywords=[str(k)[:120] for k in raw.get("keywords") or []],
            language={"eng": "en", "fre": "fr", "ger": "de", "spa": "es"}.get(str(raw.get("language") or ""), None),
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            is_preprint="Preprint" in types,
            oa_status="open" if pmcid else "unknown",
            oa_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/" if pmcid else None,
            source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        )
        return finish(rec)
