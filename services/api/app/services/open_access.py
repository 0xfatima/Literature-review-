"""Open-access facts about a paper, from what the search providers reported. Pure functions.

A paper counts as open access when a provider said so (oa_status = open) or when it is an arXiv preprint or has a
PubMed Central id: both archives publish the full text for everyone. `pdf_candidates` are the places a PDF may be
downloaded from, best first; they are only ever fetched by the full-text fetch job, which checks every response.
"""

from __future__ import annotations

import re
from typing import Any

from app.db.models import PaperRecord

_ARXIV = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)
_PMC = re.compile(r"^PMC\d{4,10}$", re.I)


def arxiv_id(p: PaperRecord) -> str | None:
    a = (p.arxiv_id or "").strip()
    a = re.sub(r"^arxiv:", "", a, flags=re.I)
    return a if _ARXIV.match(a) else None


def pmcid(p: PaperRecord) -> str | None:
    v = (p.pmcid or "").strip().upper()
    return v if _PMC.match(v) else None


def is_open(p: PaperRecord) -> bool:
    return p.oa_status == "open" or arxiv_id(p) is not None or pmcid(p) is not None


def pdf_candidates(p: PaperRecord) -> list[str]:
    out: list[str] = []
    a = arxiv_id(p)
    if a:
        out.append(f"https://arxiv.org/pdf/{a}")
    if p.pdf_url:
        out.append(p.pdf_url)
    pm = pmcid(p)
    if pm:
        out.append(f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pm}&blobtype=pdf")
    if p.oa_url and p.oa_url.lower().split("?")[0].endswith(".pdf"):
        out.append(p.oa_url)
    seen: list[str] = []
    for u in out:
        if u.lower().startswith(("https://", "http://")) and u not in seen:
            seen.append(u)
    return seen


def view(p: PaperRecord) -> dict[str, Any]:
    """The fields the screens show: a tag and where to read it."""
    links = pdf_candidates(p)
    return {
        "open_access": is_open(p),
        "oa_status": p.oa_status,
        "read_url": links[0] if links else (p.oa_url or None),
    }
