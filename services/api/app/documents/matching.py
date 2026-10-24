"""Match an uploaded PDF to the project's paper records. Pure functions, no database, no network.

Evidence comes from the PDF itself: identifiers (DOI, arXiv id) and the title/authors/year on the first pages, plus
the PDF's own metadata. The outcome is one of
  auto_matched        reliable enough to link now (still shown to the user and reversible)
  needs_confirmation  plausible candidates; NOT linked until the user confirms one
  unmatched           nothing plausible
A DOI found in the text is not proof by itself: a short paper can cite another paper's DOI on its first page, so an
exact DOI match is only reliable when the PDF metadata agrees, the title agrees, or it is the first DOI on the page
together with the first author's name. When two or more papers match by identifier the result is never automatic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.documents.text import match_string, match_tokens

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>{}|\\^`]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"arxiv\s*:\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?", re.IGNORECASE)
FIRST_PAGES_CHARS = 6000
AUTO_TITLE_SCORE = 0.9
AUTO_MARGIN = 0.2
CANDIDATE_FLOOR = 0.45
MAX_CANDIDATES = 5
MIN_TITLE_TOKENS = 3
_TRAILING = ".,;:)]}>'\"" + "\u201d\u2019"


@dataclass(frozen=True)
class PaperFacts:
    id: str
    title: str
    doi: str | None
    arxiv_id: str | None
    year: int | None
    first_author_surname: str | None


@dataclass(frozen=True)
class DocumentFacts:
    first_pages_text: str
    metadata_title: str = ""
    metadata_doi: str = ""
    metadata_subject: str = ""


@dataclass
class Candidate:
    paper_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    identifier_match: bool = False
    title_score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"paper_id": self.paper_id, "score": round(self.score, 3), "reasons": self.reasons}


@dataclass(frozen=True)
class MatchOutcome:
    status: str  # auto_matched | needs_confirmation | unmatched
    paper_id: str | None
    score: float | None
    method: str | None  # doi | arxiv | title_author | candidates
    candidates: list[dict[str, Any]]
    explanation: str


_PAIRS = {")": "(", "]": "[", "}": "{"}


def _strip_trailing(doi: str) -> str:
    """Remove sentence punctuation after a DOI. A closing bracket is removed only when it is unbalanced, so a DOI that
    legitimately ends in ")" (e.g. 10.1234/abc-1(2)) is kept."""
    while doi and doi[-1] in _TRAILING:
        last = doi[-1]
        if last in _PAIRS and doi.count(last) <= doi.count(_PAIRS[last]):
            break
        doi = doi[:-1]
    return doi


def extract_dois(text: str) -> list[str]:
    """DOIs in order of first appearance, lower-cased, without trailing punctuation, de-duplicated."""
    seen: dict[str, None] = {}
    for m in DOI_RE.finditer(text):
        doi = _strip_trailing(m.group(0)).lower()
        if "/" in doi and len(doi) > 8:
            seen.setdefault(doi, None)
    return list(seen)


def extract_arxiv_ids(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in ARXIV_RE.finditer(text):
        seen.setdefault(m.group(1).lower(), None)
    return list(seen)


def title_evidence(title: str, page_text: str, metadata_title: str) -> float:
    """0-1 evidence that `title` is the title of the document. 0.9 when the whole normalised title occurs in the
    first pages' text; otherwise the best fuzzy window over the first pages or the PDF's metadata title."""
    t_tokens = match_tokens(title)
    if len(t_tokens) < MIN_TITLE_TOKENS:
        return 0.0
    wanted = " ".join(t_tokens)
    page_tokens = match_tokens(page_text)
    best = 0.0
    if f" {wanted} " in f" {' '.join(page_tokens)} ":
        best = 0.95
    meta = match_string(metadata_title)
    if meta:
        if meta == wanted:
            best = max(best, 0.95)
        else:
            best = max(best, 0.9 * SequenceMatcher(None, meta, wanted).ratio())
    if best >= 0.9 or not page_tokens:
        return best
    vocab = set(page_tokens)
    if sum(1 for t in t_tokens if t in vocab) / len(t_tokens) < 0.6:  # cheap prefilter before the window search
        return best
    n = len(t_tokens)
    for start in range(0, max(1, len(page_tokens) - n + 1)):
        window = page_tokens[start : start + n]
        ratio = SequenceMatcher(None, window, t_tokens).ratio()
        if ratio > best / 0.9:
            best = max(best, 0.9 * ratio)
    return best


def score_candidates(doc: DocumentFacts, papers: list[PaperFacts]) -> list[Candidate]:
    text = doc.first_pages_text[:FIRST_PAGES_CHARS]
    text_dois = extract_dois(text)
    meta_dois = set(extract_dois(" ".join((doc.metadata_doi, doc.metadata_subject, doc.metadata_title))))
    first_doi = text_dois[0] if text_dois else None
    arxiv_ids = set(extract_arxiv_ids(text))
    page_tokens = set(match_tokens(text))
    out: list[Candidate] = []
    for p in papers:
        c = Candidate(p.id, 0.0)
        c.title_score = title_evidence(p.title, text, doc.metadata_title)
        author_hit = bool(p.first_author_surname) and match_string(p.first_author_surname or "") in page_tokens
        year_hit = bool(p.year) and str(p.year) in page_tokens
        if p.doi and (p.doi.lower() in text_dois or p.doi.lower() in meta_dois):
            in_meta = p.doi.lower() in meta_dois
            reliable = in_meta or c.title_score >= 0.5 or (p.doi.lower() == first_doi and author_hit)
            c.identifier_match = reliable
            c.reasons.append("DOI found in the PDF" + (" metadata" if in_meta else ""))
            if not reliable:
                c.reasons.append("the title does not match, so this DOI may only be cited in the text")
            c.score = 1.0 if reliable else 0.6
        elif p.arxiv_id and p.arxiv_id.lower() in arxiv_ids:
            c.identifier_match = c.title_score >= 0.5 or author_hit
            c.reasons.append("arXiv identifier found in the PDF")
            c.score = 0.97 if c.identifier_match else 0.6
        if c.title_score:
            c.reasons.append(f"title similarity {c.title_score:.2f}")
        if author_hit:
            c.reasons.append("first author's surname appears on the first pages")
        if year_hit:
            c.reasons.append(f"publication year {p.year} appears on the first pages")
        if not c.identifier_match:
            c.score = max(
                c.score,
                min(
                    0.99,
                    c.title_score * 0.85
                    + (0.1 if author_hit and c.title_score else 0.0)
                    + (0.05 if year_hit and c.title_score else 0.0),
                ),
            )
        if c.score > 0:
            out.append(c)
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def match(doc: DocumentFacts, papers: list[PaperFacts]) -> MatchOutcome:
    ranked = score_candidates(doc, papers)
    if not ranked:
        return MatchOutcome("unmatched", None, None, None, [], "Nothing in the PDF matched a paper in this project.")
    best = ranked[0]
    identifier_hits = [c for c in ranked if c.identifier_match]
    shown = [c.as_dict() for c in ranked[:MAX_CANDIDATES] if c.score >= CANDIDATE_FLOOR]
    if len(identifier_hits) > 1:
        return MatchOutcome(
            "needs_confirmation",
            None,
            best.score,
            "candidates",
            [c.as_dict() for c in identifier_hits[:MAX_CANDIDATES]],
            "Identifiers of more than one paper appear in the PDF. Choose the right one.",
        )
    if len(identifier_hits) == 1:
        hit = identifier_hits[0]
        method = "doi" if any(r.startswith("DOI") for r in hit.reasons) else "arxiv"
        return MatchOutcome(
            "auto_matched", hit.paper_id, hit.score, method, [hit.as_dict()], f"Matched by {method.upper()}."
        )
    second = ranked[1].score if len(ranked) > 1 else 0.0
    if best.score >= AUTO_TITLE_SCORE and best.score - second >= AUTO_MARGIN:
        return MatchOutcome(
            "auto_matched",
            best.paper_id,
            best.score,
            "title_author",
            [best.as_dict()],
            "Matched by title, author and year.",
        )
    if shown:
        return MatchOutcome(
            "needs_confirmation",
            None,
            best.score,
            "candidates",
            shown,
            "The PDF resembles these papers. Confirm which one it is.",
        )
    return MatchOutcome("unmatched", None, best.score, None, [], "Nothing in the PDF matched a paper in this project.")


def consistency_warning(doc: DocumentFacts, linked: PaperFacts, papers: list[PaperFacts]) -> str | None:
    """For a PDF the user attached to a specific paper: warn when its content looks like a DIFFERENT paper.
    The user's choice stands; this only tells them what the PDF itself says."""
    ranked = score_candidates(doc, papers)
    own = next((c for c in ranked if c.paper_id == linked.id), None)
    other = next((c for c in ranked if c.paper_id != linked.id), None)
    if other is None or other.score < 0.8:
        return None
    if own is not None and own.score >= other.score - 0.1:
        return None
    return "The content of this PDF looks like a different paper in this project. Check that it is the right file."
