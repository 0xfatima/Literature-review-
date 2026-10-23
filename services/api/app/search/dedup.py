"""Duplicate detection between a newly retrieved record and the project's existing papers. Stdlib only.

Conservative by design: a false merge silently loses a study, while a missed duplicate is visible and can be
resolved by a person. So:

  1. Same DOI, PMID, arXiv id or OpenAlex id -> the same paper (basis = that identifier).
  2. Same normalised title, compatible first author and year, and NO conflicting identifier -> the same paper
     (basis = title_author_year). "Compatible" = equal when both are known.
  3. Same normalised title but something disagrees (different DOIs, e.g. a preprint and its published version;
     different first author; years more than one apart) -> a NEW paper flagged as a *possible duplicate* of the
     existing one, with the reason. A person decides; nothing is merged automatically.
  4. Otherwise -> a new paper.

A title-only match additionally requires the title to be long enough to be distinctive and, when an author is
missing on either side, the years to be equal: short generic titles ("Editorial", "Introduction") never merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ID_FIELDS = ("doi", "pmid", "arxiv_id", "openalex_id")
MIN_TITLE_KEY_LEN = 25


class PaperLike(Protocol):
    id: object
    doi: str | None
    pmid: str | None
    arxiv_id: str | None
    openalex_id: str | None
    title_key: str
    first_author_key: str | None
    year: int | None


class RecordLike(Protocol):
    doi: str | None
    pmid: str | None
    arxiv_id: str | None
    openalex_id: str | None
    title_key: str
    first_author_key: str | None
    year: int | None


@dataclass(frozen=True)
class Decision:
    match: PaperLike | None  # merge into this paper
    basis: str  # doi | pmid | arxiv_id | openalex_id | title_author_year | new
    possible_duplicate_of: PaperLike | None = None
    reason: str | None = None


def _conflicts(rec: RecordLike, paper: PaperLike) -> list[str]:
    """Identifier fields present on both sides with different values."""
    return [f for f in ID_FIELDS if getattr(rec, f) and getattr(paper, f) and getattr(rec, f) != getattr(paper, f)]


def decide(rec: RecordLike, candidates: list[PaperLike]) -> Decision:
    """`candidates` = existing papers sharing at least one identifier or the normalised title with `rec`."""
    for f in ID_FIELDS:
        value = getattr(rec, f)
        if value:
            for p in candidates:
                if getattr(p, f) == value:
                    return Decision(p, f)

    flagged: tuple[PaperLike, str] | None = None
    for p in candidates:
        if not rec.title_key or p.title_key != rec.title_key:
            continue
        conflicts = _conflicts(rec, p)
        a1, a2 = rec.first_author_key, p.first_author_key
        y1, y2 = rec.year, p.year
        if conflicts:
            reason = (
                f"Same title but different {', '.join(conflicts)} (possibly a preprint and its published version, "
                "or two versions of one work)."
            )
        elif a1 and a2 and a1 != a2:
            reason = "Same title but a different first author."
        elif y1 and y2 and abs(y1 - y2) > 1:
            reason = f"Same title but publication years differ ({y1} vs {y2})."
        elif len(rec.title_key) < MIN_TITLE_KEY_LEN:
            reason = "Same short title; too generic to merge automatically."
        elif (not a1 or not a2) and not (y1 and y2 and y1 == y2):
            reason = "Same title, but the first author is missing on one record and the years do not confirm it."
        else:
            return Decision(p, "title_author_year")
        flagged = flagged or (p, reason)
    if flagged:
        return Decision(None, "new", possible_duplicate_of=flagged[0], reason=flagged[1])
    return Decision(None, "new")
