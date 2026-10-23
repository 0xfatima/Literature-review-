"""Turn an approved protocol into a search expression and a filter plan. Stdlib only, deterministic, no AI.

Order of preference (the first that yields a usable expression wins; the explanation says which one was used):
  1. the protocol's search strategy, when the researcher wrote it as a Boolean expression;
  2. the framework's *search concepts* (population, intervention/exposure, sample, phenomenon, custom elements),
     each concept an OR group of its comma/semicolon/"or"-separated synonyms, concepts ANDed together;
  3. content words of the objective, then of the original topic (a rough keyword query; the UI asks for review).

Comparator, outcome and design elements are deliberately NOT added to the query: restricting a search by them
loses relevant studies, and they are applied at screening instead (standard practice for sensitive searches).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.search import query as q

# Framework element keys that describe what to search for. Others are applied during screening.
SEARCH_CONCEPT_KEYS = frozenset({"population", "intervention", "exposure", "sample", "phenomenon"})
SCREENING_ONLY_KEYS = frozenset({"comparison", "comparator", "outcome", "design", "evaluation", "research_type"})
MAX_KEYWORDS = 6
MAX_PHRASE_WORDS = 4

STOPWORDS = frozenset(
    """a about after against all also an and any are as at be been before between both but by can compared
    comparison does do during each effect effects for from has have how impact in into is it its of on or other
    over role than that the their them these this those through to under use used using versus vs was were what
    when where which while who whom why will with within without among amongst systematic review reviews studies
    study literature evidence scoping meta analysis""".split()
)

# Language names -> (ISO 639-1, ISO 639-2/B). Used by providers that filter on language.
LANGUAGES: dict[str, tuple[str, str]] = {
    "english": ("en", "eng"),
    "french": ("fr", "fre"),
    "german": ("de", "ger"),
    "spanish": ("es", "spa"),
    "portuguese": ("pt", "por"),
    "italian": ("it", "ita"),
    "dutch": ("nl", "dut"),
    "chinese": ("zh", "chi"),
    "japanese": ("ja", "jpn"),
    "korean": ("ko", "kor"),
    "russian": ("ru", "rus"),
    "arabic": ("ar", "ara"),
    "persian": ("fa", "per"),
    "farsi": ("fa", "per"),
    "turkish": ("tr", "tur"),
    "polish": ("pl", "pol"),
    "swedish": ("sv", "swe"),
    "norwegian": ("no", "nor"),
    "danish": ("da", "dan"),
    "finnish": ("fi", "fin"),
    "greek": ("el", "gre"),
    "hebrew": ("he", "heb"),
    "hindi": ("hi", "hin"),
    "urdu": ("ur", "urd"),
    "indonesian": ("id", "ind"),
    "malay": ("ms", "may"),
    "thai": ("th", "tha"),
    "vietnamese": ("vi", "vie"),
    "czech": ("cs", "cze"),
    "hungarian": ("hu", "hun"),
    "romanian": ("ro", "rum"),
    "ukrainian": ("uk", "ukr"),
}
_ISO1 = {v[0]: v for v in LANGUAGES.values()}
_ISO2 = {v[1]: v for v in LANGUAGES.values()}


@dataclass
class Derived:
    expression: str
    source: str  # search_strategy | framework_elements | objective | topic
    explanation: list[str] = field(default_factory=list)


@dataclass
class FilterPlan:
    """Filters taken from the approved protocol. Each provider applies what it can and reports the rest."""

    date_from: int | None
    date_to: int | None
    languages: list[tuple[str, str]]  # (iso1, iso2) for recognised languages
    unrecognised_languages: list[str]
    study_designs: list[str]  # never applied as a search filter (screening applies them)

    def as_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "languages": [iso1 for iso1, _ in self.languages],
            "unrecognised_languages": self.unrecognised_languages,
            "study_designs": self.study_designs,
        }


def resolve_language(name: str) -> tuple[str, str] | None:
    k = name.strip().lower()
    return LANGUAGES.get(k) or _ISO1.get(k) or _ISO2.get(k)


def filter_plan(content: dict[str, Any]) -> FilterPlan:
    langs: list[tuple[str, str]] = []
    unknown: list[str] = []
    for name in content.get("languages") or []:
        r = resolve_language(str(name))
        if r is None:
            unknown.append(str(name))
        elif r not in langs:
            langs.append(r)
    return FilterPlan(
        date_from=content.get("date_from"),
        date_to=content.get("date_to"),
        languages=langs,
        unrecognised_languages=unknown,
        study_designs=list(content.get("study_designs") or []),
    )


def _looks_boolean(text: str) -> bool:
    return bool(re.search(r'\b(AND|OR|NOT)\b|"|\(', text))


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[\w\-']+", text.lower(), re.UNICODE)
    out: list[str] = []
    for w in words:
        w = w.strip("-'")
        if len(w) > 2 and w not in STOPWORDS and not w.isdigit() and w not in out:
            out.append(w)
    return out


def _term(piece: str) -> str | None:
    """One synonym -> a phrase (short) or an AND of its content words (long)."""
    words = piece.split()
    if not words:
        return None
    if len(words) == 1:
        w = re.sub(r"[^\w\-'*]", "", words[0], flags=re.UNICODE)
        return w or None
    if len(words) <= MAX_PHRASE_WORDS:
        phrase = re.sub(r'["()]', "", " ".join(words)).strip()
        return f'"{phrase}"' if phrase else None
    cw = _content_words(piece)[:MAX_PHRASE_WORDS]
    return f"({' AND '.join(cw)})" if cw else None


def _concept(value: str) -> str | None:
    pieces = [p.strip() for p in re.split(r"[,;\n]|\s+or\s+|\s+OR\s+|/", value) if p.strip()]
    terms = [t for t in (_term(p) for p in pieces) if t]
    if not terms:
        return None
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _valid(expr: str) -> bool:
    try:
        q.parse(expr)
    except q.QueryError:
        return False
    return True


def derive(content: dict[str, Any], topic: str) -> Derived:
    strategy = (content.get("search_strategy") or "").strip()
    if strategy and _looks_boolean(strategy):
        try:
            node = q.parse(strategy)
        except q.QueryError as e:
            note = f"The search strategy looks like a Boolean expression but could not be used: {e}"
        else:
            return Derived(q.to_text(node), "search_strategy", ["Taken from the search strategy in the protocol."])
    else:
        note = (
            "The search strategy is written as prose, so it was not used as a query."
            if strategy
            else "The protocol has no Boolean search strategy."
        )

    used, skipped = [], []
    concepts: list[str] = []
    for el in content.get("framework_elements") or []:
        if not isinstance(el, dict) or not el.get("value"):
            continue
        key, label = str(el.get("key") or ""), str(el.get("label") or el.get("key") or "")
        if key in SCREENING_ONLY_KEYS:
            skipped.append(label)
            continue
        if key in SEARCH_CONCEPT_KEYS or content.get("framework") == "custom":
            c = _concept(str(el["value"]))
            if c:
                concepts.append(c)
                used.append(label)
    if concepts:
        expr = " AND ".join(concepts)
        if _valid(expr):
            why = [note, f"Built from the framework elements: {', '.join(used)} (synonyms joined with OR)."]
            if skipped:
                why.append(
                    f"Not added to the query (applied at screening instead): {', '.join(skipped)}. Restricting a "
                    "search by these loses relevant studies."
                )
            return Derived(expr, "framework_elements", why)

    for source, text in (("objective", content.get("objective") or ""), ("topic", topic)):
        words = _content_words(text)[:MAX_KEYWORDS]
        if words:
            expr = " AND ".join(words)
            if _valid(expr):
                return Derived(
                    expr,
                    source,
                    [
                        note,
                        f"Built from the content words of the {source} (a rough keyword query). Review and edit it "
                        "before running the search.",
                    ],
                )
    return Derived("", "none", [note, "No search terms could be derived. Write the search expression yourself."])
