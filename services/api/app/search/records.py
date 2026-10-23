"""The common scholarly-record schema every provider adapter produces, plus identifier and title normalisation.
Stdlib only.

Access fields keep three things apart (spec 3.3, "distinguish open metadata from open full text"):
  * metadata availability: we have a record (always true for a record);
  * open-access status as REPORTED by the provider (`oa_status`: open | closed | unknown);
  * a lawful full-text LINK reported by the provider (`pdf_url` / `oa_url`).
None of these means the application has obtained or read the full text; that is `full_text_status` on the paper,
which this phase always leaves at `not_obtained`.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

OA_STATUSES = ("open", "closed", "unknown")
ACCESS_STATUSES = ("open_pdf_link", "open_landing_page", "metadata_only")
MAX_ABSTRACT = 20_000
MAX_TITLE = 1000
MAX_AUTHORS = 100


class RecordError(ValueError):
    """A provider record that cannot be normalised (e.g. no title). Message is short and user-safe."""


@dataclass
class Author:
    name: str
    family: str | None = None
    orcid: str | None = None


@dataclass
class ScholarlyRecord:
    provider: str
    provider_record_id: str
    title: str
    authors: list[Author] = field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    publication_type: str | None = None
    keywords: list[str] = field(default_factory=list)
    language: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    other_ids: dict[str, str] = field(default_factory=dict)
    is_preprint: bool = False
    is_retracted: bool | None = None
    oa_status: str = "unknown"
    oa_url: str | None = None
    pdf_url: str | None = None
    license: str | None = None
    source_url: str | None = None

    @property
    def access_status(self) -> str:
        return access_status(self.oa_status, self.pdf_url, self.oa_url)

    @property
    def title_key(self) -> str:
        return title_key(self.title)

    @property
    def first_author_key(self) -> str | None:
        return author_key(self.authors[0]) if self.authors else None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["access_status"] = self.access_status
        return d


def access_status(oa_status: str, pdf_url: str | None, oa_url: str | None) -> str:
    if oa_status == "open" and pdf_url:
        return "open_pdf_link"
    if oa_status == "open" and oa_url:
        return "open_landing_page"
    return "metadata_only"


# ---------- identifiers ----------

_DOI = re.compile(r"(10\.\d{4,9}/\S+)", re.I)
_ARXIV_NEW = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?", re.I)
_ARXIV_OLD = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)


def norm_doi(raw: Any) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    m = _DOI.search(raw.strip())
    if not m:
        return None
    doi = m.group(1).lower().rstrip(".,;)")
    return doi if len(doi) <= 255 else None


def norm_pmid(raw: Any) -> str | None:
    if raw is None:
        return None
    m = re.search(r"(\d{1,9})\s*$", str(raw).strip().rstrip("/"))
    if m is None:
        return None
    return m.group(1).lstrip("0") or None


def norm_pmcid(raw: Any) -> str | None:
    if raw is None:
        return None
    m = re.search(r"(?:PMC)?(\d{1,9})\s*$", str(raw).strip().rstrip("/"), re.I)
    return f"PMC{m.group(1)}" if m else None


def norm_arxiv(raw: Any) -> str | None:
    """Version-less arXiv id: '2101.00001' or 'hep-th/9901001'."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    doi = _ARXIV_DOI.match(s)
    if doi:
        s = doi.group(1)
    s = re.sub(r"^(https?://)?(export\.)?arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"^arxiv:", "", s, flags=re.I).removesuffix(".pdf")
    m = _ARXIV_NEW.fullmatch(s) or _ARXIV_OLD.fullmatch(s)
    return m.group(1).lower() if m else None


def arxiv_from_doi(doi: str | None) -> str | None:
    if doi and _ARXIV_DOI.match(doi):
        return norm_arxiv(doi)
    return None


def norm_openalex(raw: Any) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    m = re.search(r"(W\d+)\s*$", raw.strip(), re.I)
    return m.group(1).upper() if m else None


# ---------- text ----------

_TAG = re.compile(r"<[^>]+>")


def clean_text(raw: Any, limit: int) -> str | None:
    """Strip HTML/JATS markup and entities, collapse whitespace, truncate to `limit` characters."""
    if raw is None:
        return None
    s = html.unescape(_TAG.sub(" ", str(raw)))
    s = " ".join(s.split())
    return s[:limit] or None


def title_key(title: str) -> str:
    """Normalised title for duplicate detection: accents folded, case-folded, punctuation removed."""
    s = unicodedata.normalize("NFKD", title)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).replace("_", " ")
    return " ".join(s.split())[:500]


def author_key(a: Author) -> str | None:
    fam = a.family
    if not fam and a.name:
        n = a.name.strip()
        # "Smith J" / "Smith, John" (family first) vs "John Smith" (family last)
        if "," in n:
            fam = n.split(",")[0]
        elif re.fullmatch(r"[^\s]+(\s+[A-Z]{1,3}\.?)+", n):
            fam = n.split()[0]
        else:
            fam = n.split()[-1] if n.split() else None
    if not fam:
        return None
    k = title_key(fam).replace(" ", "")
    return k or None


def year(raw: Any) -> int | None:
    try:
        y = int(str(raw)[:4])
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2100 else None


def require_title(raw: Any) -> str:
    t = clean_text(raw, MAX_TITLE)
    if not t:
        raise RecordError("record has no title")
    return t


def finish(rec: ScholarlyRecord) -> ScholarlyRecord:
    """Shared post-processing: derive arXiv id from an arXiv DOI, cap list sizes, validate enums."""
    if rec.arxiv_id is None:
        rec.arxiv_id = arxiv_from_doi(rec.doi)
    if rec.arxiv_id and rec.doi and _ARXIV_DOI.match(rec.doi):
        rec.is_preprint = True
    rec.authors = rec.authors[:MAX_AUTHORS]
    rec.keywords = list(dict.fromkeys(k for k in rec.keywords if k))[:50]
    if rec.oa_status not in OA_STATUSES:
        rec.oa_status = "unknown"
    for attr in ("oa_url", "pdf_url", "source_url"):
        v = getattr(rec, attr)
        if v is not None and not (isinstance(v, str) and v.startswith(("https://", "http://")) and len(v) <= 2000):
            setattr(rec, attr, None)
    return rec
