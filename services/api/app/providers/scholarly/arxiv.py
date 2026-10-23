"""arXiv API (https://info.arxiv.org/help/api/). Preprints in physics, mathematics, computer science, quantitative
biology/finance, statistics, electrical engineering and economics. Every e-print is openly readable on arxiv.org.

Rules followed (arXiv API terms of use): no more than one request every three seconds (enforced across workers by the
shared throttle), at most 2000 results per request (we use far fewer), metadata only (we never bulk-download PDFs),
and the acknowledgement below shown wherever arXiv results appear. Records are labelled as preprints: appearing on
arXiv does not imply peer review, even when a journal reference is present.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405  (see _parse: no DTDs/entities accepted)
from typing import Any

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
    norm_arxiv,
    norm_doi,
    require_title,
    year,
)

ENDPOINT = "https://export.arxiv.org/api/query"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
    "ax": "http://arxiv.org/schemas/atom",
}
DIALECT = q.Dialect(
    and_op=" AND ", or_op=" OR ", and_not=" ANDNOT ", term_prefix="all:", supports_truncation=False, name="arXiv"
)


def _text(el: ET.Element | None) -> str | None:
    return " ".join(el.text.split()) if el is not None and el.text else None


def _parse(xml_text: str) -> ET.Element:
    head = xml_text[:4096].upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in head:
        raise ProviderError("arXiv", "malformed", "arXiv returned XML with a document type declaration (refused)")
    try:
        return ET.fromstring(xml_text)  # noqa: S314  (no DTD/entities: checked above; expat limits expansion)
    except ET.ParseError:
        raise ProviderError("arXiv", "malformed", "arXiv returned a response that is not valid XML") from None


def entry_to_dict(e: ET.Element) -> dict[str, Any]:
    """The Atom entry as a plain dict (JSON-serialisable) plus the entry's original XML, so nothing is lost."""
    links = [
        {
            k: v
            for k, v in (
                ("href", ln.get("href")),
                ("rel", ln.get("rel")),
                ("type", ln.get("type")),
                ("title", ln.get("title")),
            )
            if v
        }
        for ln in e.findall("a:link", NS)
    ]
    return {
        "id": _text(e.find("a:id", NS)),
        "title": _text(e.find("a:title", NS)),
        "summary": _text(e.find("a:summary", NS)),
        "published": _text(e.find("a:published", NS)),
        "updated": _text(e.find("a:updated", NS)),
        "authors": [_text(a.find("a:name", NS)) for a in e.findall("a:author", NS) if a.find("a:name", NS) is not None],
        "doi": _text(e.find("ax:doi", NS)),
        "journal_ref": _text(e.find("ax:journal_ref", NS)),
        "comment": _text(e.find("ax:comment", NS)),
        "primary_category": (
            e.find("ax:primary_category", NS).get("term")  # type: ignore[union-attr]
            if e.find("ax:primary_category", NS) is not None
            else None
        ),
        "categories": [c.get("term") for c in e.findall("a:category", NS) if c.get("term")],
        "links": links,
        "atom_xml": ET.tostring(e, encoding="unicode")[:50_000],
    }


class ArxivSource:
    key = "arxiv"
    label = "arXiv"
    tier = "open_access"
    description = "Open preprints in physics, mathematics, computer science, statistics and related fields."
    attribution = "Thank you to arXiv for use of its open access interoperability."
    terms_url = "https://info.arxiv.org/help/api/tou.html"
    min_interval_s: float = 3.0
    max_page_size = 200
    aliases: tuple[str, ...] = ("arxiv", "arxiv.org")

    def build_query(self, expression: q.Node, filters: FilterPlan) -> ProviderQuery:
        r = q.render(expression, DIALECT)
        text = f"({r.text})" if " " in r.text else r.text  # always group before appending the date filter
        applied: dict[str, Any] = {}
        limitations = list(r.limitations)
        if filters.date_from or filters.date_to:
            lo = f"{filters.date_from or 1991}01010000"
            hi = f"{filters.date_to or 2100}12312359"
            text += f" AND submittedDate:[{lo} TO {hi}]"
            applied.update(date_from=filters.date_from, date_to=filters.date_to)
            limitations.append("arXiv filters on the first submission date, not the journal publication date.")
        if filters.languages:
            limitations.append("arXiv has no language filter; the protocol's languages were not applied here.")
        limitations.append("arXiv records are preprints; posting on arXiv does not imply peer review.")
        params = {"search_query": text, "sortBy": "relevance", "sortOrder": "descending"}
        return ProviderQuery(self.key, ENDPOINT, text, params, applied, limitations)

    def fetch_page(self, http: HttpFetcher, query: ProviderQuery, cursor: str | None, page_size: int) -> Page:
        start = int(cursor or 0)
        size = min(page_size, self.max_page_size)
        params = {**query.params, "start": start, "max_results": size}
        root = _parse(http.get_text(self.label, query.endpoint, params, min_interval_s=self.min_interval_s))
        if root.tag != f"{{{NS['a']}}}feed":
            raise ProviderError(self.label, "malformed", "arXiv returned an unexpected response shape")
        entries = root.findall("a:entry", NS)
        if len(entries) == 1 and (_text(entries[0].find("a:id", NS)) or "").startswith("http://arxiv.org/api/errors"):
            raise ProviderError(self.label, "rejected", "arXiv rejected the query (syntax error)")
        total_txt = _text(root.find("os:totalResults", NS))
        total = int(total_txt) if total_txt and total_txt.isdigit() else None
        records = [entry_to_dict(e) for e in entries]
        nxt = start + len(records)
        more = bool(records) and (total is None or nxt < total)
        return Page(records, str(nxt) if more else None, total)

    def record_id(self, raw: dict[str, Any]) -> str:
        rid = norm_arxiv(raw.get("id"))
        if not rid:
            raise RecordError("arXiv record without an arXiv id")
        return rid

    def normalize(self, raw: dict[str, Any]) -> ScholarlyRecord:
        rid = self.record_id(raw)
        pdf = abs_url = None
        for ln in raw.get("links") or []:
            href = str(ln.get("href") or "").replace("http://", "https://", 1)
            if ln.get("title") == "pdf" and not pdf:
                pdf = href
            elif ln.get("rel") == "alternate" and not abs_url:
                abs_url = href
        abs_url = abs_url or f"https://arxiv.org/abs/{rid}"
        journal_ref = raw.get("journal_ref")
        rec = ScholarlyRecord(
            provider=self.key,
            provider_record_id=rid,
            title=require_title(raw.get("title")),
            authors=[Author(name=str(a)[:300]) for a in raw.get("authors") or [] if a],
            abstract=clean_text(raw.get("summary"), MAX_ABSTRACT),
            year=year(raw.get("published")),
            venue=f"arXiv ({journal_ref})"[:500] if journal_ref else "arXiv",
            publication_type="preprint",
            keywords=[str(c) for c in raw.get("categories") or []],
            doi=norm_doi(raw.get("doi")),
            arxiv_id=rid,
            other_ids={"arxiv_primary_category": str(raw["primary_category"])} if raw.get("primary_category") else {},
            is_preprint=True,
            oa_status="open",
            oa_url=abs_url,
            pdf_url=pdf or f"https://arxiv.org/pdf/{rid}",
            source_url=abs_url,
        )
        out = finish(rec)
        if journal_ref:
            out.other_ids["journal_ref"] = re.sub(r"\s+", " ", str(journal_ref))[:300]
        return out
