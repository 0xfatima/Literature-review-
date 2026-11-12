"""Turn an approved protocol into a search expression and a filter plan. Stdlib only, deterministic, no AI.

Order of preference (the first that yields a usable expression wins; the explanation says which one was used):
  1. the protocol's search strategy, when the researcher wrote it as a Boolean expression;
  2. the framework's *search concepts* (population, intervention/exposure, sample, phenomenon, custom elements),
     each concept an OR group of its comma/semicolon/"or"-separated synonyms, concepts ANDed together;
  3. an expression built by the AI model from the whole protocol, when one is available (see app.search.llm_query;
     it is validated against this module's grammar before use, and cached per approved protocol version);
  4. key phrases of the objective, then of the original topic: the text is split at stopwords and punctuation into
     phrases ("social media use", "adolescent depression"), at most MAX_CONCEPTS of them ANDed (a rough query; the UI
     asks for review). Earlier versions ANDed up to six single words, which matched almost nothing relevant.

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
MAX_CONCEPTS = 3

STOPWORDS = frozenset(
    """a about after against all also an and any are as at be been before between both but by can compared
    comparison does do during each effect effects for from has have how impact in into is it its of on or other
    over role than that the their them these this those through to under use used using versus vs was were what
    when where which while who whom why will with within without among amongst systematic review reviews studies
    study literature evidence scoping meta analysis among its may might should could would effectiveness efficacy
    association associated relationship influence factors outcomes outcome investigate investigating examine
    examining explore exploring assess assessing evaluate evaluating determine understanding towards toward via per
    such based new current recent research paper papers article articles understand understanding much many
    right now today currently whether well good best ways way make makes made need needs know known learn
    aim aims objective objectives goal goals purpose identify identifying describe describing review overview
    state art level extent degree really actually""".split()
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
    source: str  # search_strategy | framework_elements | ai | objective | objective_topic | topic | none
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


def key_phrases(text: str, limit: int = MAX_CONCEPTS) -> list[str]:
    """Runs of content words between stopwords/punctuation, longest information first, as query terms."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for tok in re.findall(r"[\w\-']+|[^\w\s]", text.lower(), re.UNICODE):
        w = tok.strip("-'")
        if not w or not re.match(r"[\w]", w) or w in STOPWORDS or w.isdigit() or len(w) < 2:
            if current:
                chunks.append(current)
            current = []
        else:
            current.append(w)
    if current:
        chunks.append(current)
    out: list[str] = []
    for words in chunks:
        if len(words) == 1 and len(words[0]) <= 2:
            continue
        term = _run_term(words)
        if term and term not in out:
            out.append(term)
        if len(out) >= limit:
            break
    return out


def _stem(word: str) -> str:
    """Adverbs and -able adjectives are searched by their stem with truncation (clinically -> clinic*), so a rough
    keyword query does not demand one exact word form."""
    for suffix in ("ically", "ally", "able", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            cut = len(word) - len(suffix) + (2 if suffix == "ically" else 0)
            return word[:cut] + "*"
    return word


def _run_term(words: list[str]) -> str | None:
    """A run of content words -> one query term. Short runs of plain words stay a phrase ("social media");
    runs with adverbs/-able words, or long runs, become an AND of (stemmed) words."""
    if not words:
        return None
    if len(words) == 1:
        return _stem(words[0])
    stems = [_stem(w) for w in words]
    if len(words) <= MAX_PHRASE_WORDS and stems == words:
        return '"' + " ".join(words) + '"'
    kept = list(dict.fromkeys(stems))[:5]
    return "(" + " AND ".join(kept) + ")"


_ACRONYM = re.compile(r"\b([A-Z][A-Z0-9]{1,6})s?\b")


def acronym_expansions(*texts: str) -> dict[str, str]:
    """Acronyms written in capitals (QML, RCT) mapped to an expansion found in the same texts by initials
    ("quantum machine learning"). Only expansions actually written by the researcher are used."""
    acronyms = {m.group(1) for t in texts for m in _ACRONYM.finditer(t or "") if m.group(1) not in q.OPERATORS}
    words = [w for t in texts for w in re.findall(r"[A-Za-z][\w\-']*", t or "")]
    out: dict[str, str] = {}
    for acr in acronyms:
        n = len(acr)
        letters = acr.lower()
        for i in range(len(words) - n + 1):
            run = words[i : i + n]
            if "".join(w[0].lower() for w in run) == letters and not any(w.lower() in STOPWORDS for w in run):
                out[acr.lower()] = " ".join(w.lower() for w in run)
                break
    return out


def keyword_concepts(texts: list[str], limit: int = MAX_CONCEPTS) -> list[str]:
    """Key phrases of several texts (most important first), with acronyms ORed with their written expansion:
    "quantum machine learning" and "QML" become ("quantum machine learning" OR qml), one concept."""
    exp = acronym_expansions(*texts)
    groups: dict[str, str] = {acr: f'("{full}" OR {acr})' for acr, full in exp.items()}
    out: list[str] = []
    for text in texts:
        lowered = text or ""
        for acr, full in exp.items():  # carve each expansion out of the text so it is never split or merged
            lowered = re.sub(re.escape(full), f" , {acr} , ", lowered, flags=re.IGNORECASE)
        for term in key_phrases(lowered, limit=limit * 2):
            term = groups.get(term, term)
            if term not in out:
                out.append(term)
    return out[:limit]


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


def derive(content: dict[str, Any], topic: str, ai: Derived | None = None) -> Derived:
    """`ai`: an already-validated expression proposed by the AI model (source "ai"), used when the researcher wrote
    no Boolean strategy."""
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

    if ai is not None and ai.expression and _valid(ai.expression):
        return Derived(ai.expression, "ai", [note, *ai.explanation])

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

    objective = str(content.get("objective") or "")
    questions = " ; ".join(str(qn.get("text") or "") for qn in content.get("questions") or [] if isinstance(qn, dict))
    # The objective alone is often too thin ("understand the impact of QML"): when it gives fewer than two
    # concepts, the topic and the review questions fill in (acronyms are matched to their expansions across all).
    candidates: list[tuple[str, list[str]]] = [
        ("objective", [objective]),
        ("objective_topic", [topic, objective, questions]),
        ("topic", [topic, questions]),
    ]
    for source, texts in candidates:
        if not any(t.strip() for t in texts) or (source.startswith("objective") and not objective.strip()):
            continue
        phrases = keyword_concepts(texts)
        if source == "objective" and len(phrases) < 2 and (topic.strip() or questions.strip()):
            continue
        if not phrases:
            continue
        expr = " AND ".join(phrases)
        if _valid(expr):
            label = {"objective": "objective", "objective_topic": "topic and objective", "topic": "topic"}[source]
            return Derived(
                expr,
                source,
                [
                    note,
                    f"Built from the key phrases of the {label} (a rough query: synonyms are missing). Review "
                    "and edit it, or connect an AI model in Settings to have a full Boolean strategy built.",
                ],
            )
    return Derived("", "none", [note, "No search terms could be derived. Write the search expression yourself."])
