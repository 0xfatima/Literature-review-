"""Relevance (NOT eligibility): how closely a record's title, keywords and abstract match the approved protocol's
research questions and search concepts. Stdlib only, deterministic, fully explainable.

Lexical method (`lexical-v1`):
  * concepts = each framework search concept (population, intervention, ...; synonyms of one concept count as one
    concept) and each approved research question (its content words);
  * a concept's score = the share of its terms found in the record, scaled by WHERE they were found
    (title 1.0, keywords 0.85, abstract only 0.7);
  * the record's score (0-100) = the mean concept score. The explanation lists, per concept, the words that matched,
    where, and the words that did not.
A high score means "uses the same words as the questions". It says nothing about study design, population limits or
any inclusion criterion: those are judged by the reviewer during screening.

Bands (text label always shown; colour is never the only signal):
  high, moderate_high, possible, low   - from the score
  insufficient                         - no abstract and a short title: too little text to judge
  not_assessed                         - no assessment yet
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

METHOD = "lexical"
METHOD_VERSION = "lexical-v1"
FIELD_WEIGHTS = {"title": 1.0, "keywords": 0.85, "abstract": 0.7}
BANDS = ("high", "moderate_high", "possible", "low", "insufficient", "not_assessed")
BAND_LABELS = {
    "high": "High relevance",
    "moderate_high": "Moderate-to-high relevance",
    "possible": "Possible relevance",
    "low": "Low relevance",
    "insufficient": "Insufficient metadata to judge",
    "not_assessed": "Not yet assessed",
}
LEXICAL_THRESHOLDS = ((60, "high"), (40, "moderate_high"), (20, "possible"))
LLM_THRESHOLDS = ((75, "high"), (55, "moderate_high"), (35, "possible"))
SEARCH_CONCEPT_KEYS = frozenset({"population", "intervention", "exposure", "sample", "phenomenon"})

_STOP = frozenset(
    """a about after against all also among an and any are as at be been before being between both but by can
    could did do does done during each effect effects for from had has have how however if in into is it its
    may might more most of on or other our over same should so such than that the their them then there these
    they this those through to under up use used using versus vs was we were what when where whether which while
    who whom why will with within without would compared comparison associated association impact role among
    study studies review reviews systematic evidence literature research question questions""".split()
)


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


def stem(word: str) -> str:
    """A deliberately small suffix stripper (explainable; good enough to match 'depressive'/'depression')."""
    w = word
    for suf in (
        "ization",
        "isation",
        "ations",
        "ation",
        "ments",
        "ment",
        "ness",
        "ities",
        "ity",
        "ings",
        "ing",
        "ions",
        "ion",
        "ives",
        "ive",
        "ies",
        "ers",
        "er",
        "ed",
        "es",
        "s",
    ):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return [stem(w) for w in re.findall(r"[a-z0-9]+", _fold(text)) if len(w) > 2 and w not in _STOP]


def content_terms(text: str) -> list[str]:
    """Unique stems in order, for building concepts from protocol text."""
    out: list[str] = []
    for t in tokens(text):
        if t not in out:
            out.append(t)
    return out


@dataclass
class Concept:
    id: str
    label: str
    kind: str  # framework_element | research_question
    groups: list[list[str]]  # OR-groups of AND-ed stems: any one synonym group satisfies the concept fully
    display: str

    def all_terms(self) -> list[str]:
        seen: list[str] = []
        for g in self.groups:
            for t in g:
                if t not in seen:
                    seen.append(t)
        return seen


def concepts_from_protocol(content: dict[str, Any]) -> list[Concept]:
    out: list[Concept] = []
    for el in content.get("framework_elements") or []:
        if not isinstance(el, dict) or not el.get("value"):
            continue
        key = str(el.get("key") or "")
        if key not in SEARCH_CONCEPT_KEYS and content.get("framework") != "custom":
            continue
        synonyms = [p.strip() for p in re.split(r"[,;\n/]|\s+or\s+", str(el["value"]), flags=re.I) if p.strip()]
        groups = [g for g in (content_terms(s) for s in synonyms) if g]
        if groups:
            out.append(Concept(f"el:{key}", str(el.get("label") or key), "framework_element", groups, str(el["value"])))
    for i, q in enumerate(content.get("questions") or [], start=1):
        text = str(q.get("text") if isinstance(q, dict) else q or "")
        terms = content_terms(text)
        if terms:
            qkey = q.get("stable_key") if isinstance(q, dict) else None
            out.append(Concept(f"q:{qkey or i}", f"Question {i}", "research_question", [terms], text))
    return out


@dataclass
class Assessment:
    score: int  # 0-100
    band: str
    confidence: str  # normal | low
    explanation: dict[str, Any] = field(default_factory=dict)


def band_for(score: int, thresholds: tuple[tuple[int, str], ...]) -> str:
    for cut, band in thresholds:
        if score >= cut:
            return band
    return "low"


def assess(record: dict[str, Any], concepts: list[Concept]) -> Assessment:
    """`record`: title, abstract, keywords (list). Pure function."""
    fields = {
        "title": set(tokens(record.get("title"))),
        "keywords": set(tokens(" ".join(record.get("keywords") or []))),
        "abstract": set(tokens(record.get("abstract"))),
    }
    has_abstract = bool(record.get("abstract"))
    insufficient = not has_abstract and len(fields["title"]) < 4
    if not concepts:
        return Assessment(
            0,
            "not_assessed",
            "low",
            {
                "method": METHOD_VERSION,
                "summary": "The approved protocol has no research questions or search concepts to compare with.",
                "concepts": [],
            },
        )
    rows: list[dict[str, Any]] = []
    total = 0.0
    for c in concepts:
        best: tuple[float, list[str], list[str], str] = (0.0, [], [], "")  # score, matched, missing, where
        for g in c.groups:
            matched = [t for t in g if any(t in f for f in fields.values())]
            if not matched:
                continue
            where = next(
                (name for name in ("title", "keywords", "abstract") if any(t in fields[name] for t in matched)),
                "abstract",
            )
            s = (len(matched) / len(g)) * FIELD_WEIGHTS[where]
            if s > best[0]:
                best = (s, matched, [t for t in g if t not in matched], where)
        total += best[0]
        rows.append(
            {
                "id": c.id,
                "label": c.label,
                "kind": c.kind,
                "text": c.display[:300],
                "score": round(best[0] * 100),
                "matched": best[1],
                "missing": best[2] if best[1] else c.all_terms()[:12],
                "best_field": best[3] or None,
            }
        )
    score = round(100 * total / len(concepts))
    band = "insufficient" if insufficient else band_for(score, LEXICAL_THRESHOLDS)
    hit = sum(1 for r in rows if r["matched"])
    summary = (
        f"Shares words with {hit} of {len(concepts)} protocol concepts and questions"
        + ("" if has_abstract else "; no abstract was available, so only the title and keywords were compared")
        + ". This measures word overlap, not eligibility."
    )
    return Assessment(
        score,
        band,
        "normal" if has_abstract else "low",
        {"method": METHOD_VERSION, "summary": summary, "concepts": rows, "has_abstract": has_abstract},
    )
