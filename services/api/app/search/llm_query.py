"""Ask the AI model to design the Boolean search strategy (concept groups of synonyms, ANDed together).

The model gets public protocol text only (topic, objective, questions, framework elements, criteria). Its answer is
untrusted: the expression is parsed with app.search.query (same grammar the user edits) and, if it does not parse,
rebuilt from the concept lists after sanitising every term. Nothing the model writes reaches a provider unchecked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.untrusted import fence
from app.providers.llm import LLMGateway
from app.search import query as q

PROMPT_VERSION = "search-expression-v1"
MAX_CONCEPTS = 5
MAX_SYNONYMS = 10

SYSTEM = " ".join(
    [
        "You are an expert medical/academic search librarian. You design sensitive but precise Boolean search",
        "strategies for systematic reviews that are run against scholarly databases (OpenAlex, PubMed, Europe PMC,",
        "Semantic Scholar, Crossref and others).",
        "Method: identify the 2-4 core CONCEPTS that every relevant paper must mention (usually the",
        "population/context and the intervention/exposure/phenomenon; add the outcome only when the topic is",
        "meaningless without it). For each concept list 3-8 synonyms, spelling variants, abbreviations and closely",
        "related terms researchers actually use in titles and abstracts. Synonyms within a concept are joined with OR;",
        "concepts are joined with AND.",
        "Rules for the expression: operators AND, OR, NOT in UPPER CASE; multi-word terms in double quotes;",
        "parentheses around every OR group; a trailing * is allowed for truncation (e.g. adolescen*); NO field tags",
        "(no TITLE:, [tiab], MeSH), no wildcards inside words, no proximity operators. Never use generic research",
        "words as terms (study, review, effect, impact, role, analysis, outcomes, factors, evidence). Do not add study",
        "designs, dates or languages: they are applied separately. Use NOT only to remove an unambiguous, frequent",
        "false-positive sense.",
    ]
)

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "concepts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING", "description": "Short label, e.g. Population"},
                    "terms": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["name", "terms"],
            },
        },
        "expression": {"type": "STRING", "description": "The full Boolean expression"},
        "notes": {"type": "STRING", "description": "One or two sentences explaining the choices"},
    },
    "required": ["concepts", "expression"],
}


@dataclass
class Suggestion:
    expression: str
    concepts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""


def build_prompt(content: dict[str, Any], topic: str) -> str:
    lines = [f"Review topic: {topic}"]
    if content.get("objective"):
        lines.append(f"Objective: {content['objective']}")
    lines += [
        f"Research question: {qn['text']}"
        for qn in content.get("questions") or []
        if isinstance(qn, dict) and qn.get("text")
    ]
    lines += [
        f"{el.get('label') or el.get('key')}: {el['value']}"
        for el in content.get("framework_elements") or []
        if isinstance(el, dict) and el.get("value")
    ]
    lines += [
        f"{str(c.get('kind') or 'criterion').capitalize()} criterion: {c['text']}"
        for c in content.get("criteria") or []
        if isinstance(c, dict) and c.get("text") and c.get("state", "active") != "removed"
    ]
    if content.get("search_strategy"):
        lines.append(f"The researcher's notes on the search (prose): {content['search_strategy']}")
    body = "\n".join(str(x)[:1500] for x in lines)[:12000]
    return "Design the Boolean search expression for this review protocol.\n\n" + fence(body, "protocol")


_BAD = re.compile(r"[\"():\[\]{}^~]")


def _clean_term(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    t = " ".join(_BAD.sub(" ", raw).split())
    t = re.sub(r"\b(AND|OR|NOT)\b", lambda m: m.group(1).lower(), t)
    if not t or len(t) > 80:
        return None
    words = t.split()
    if len(words) == 1:
        w = words[0]
        return w if re.fullmatch(r"[\w\-'.+/&]+\*?", w, re.UNICODE) else None
    if len(words) > 5:
        return None
    phrase = " ".join(w.rstrip("*") for w in words)
    return f'"{phrase}"'


def from_concepts(concepts: Any) -> tuple[str, list[dict[str, Any]]]:
    groups: list[str] = []
    kept: list[dict[str, Any]] = []
    for c in concepts if isinstance(concepts, list) else []:
        if not isinstance(c, dict):
            continue
        terms = list(dict.fromkeys(t for t in (_clean_term(x) for x in (c.get("terms") or [])) if t))[:MAX_SYNONYMS]
        if not terms:
            continue
        kept.append({"name": str(c.get("name") or "Concept")[:60], "terms": terms})
        groups.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
        if len(groups) >= MAX_CONCEPTS:
            break
    return " AND ".join(groups), kept


def _parses(expr: str) -> str | None:
    try:
        node = q.parse(expr)
    except q.QueryError:
        return None
    if not q.positive_terms(node):
        return None
    return q.to_text(node)


def suggest(gw: LLMGateway, content: dict[str, Any], topic: str) -> Suggestion | None:
    """Raises LLMError for provider failures; returns None when the answer is unusable."""
    res = gw.generate_json(
        data_class="public_metadata",
        system=SYSTEM,
        prompt=build_prompt(content, topic),
        schema=SCHEMA,
        temperature=0.2,
        max_output_tokens=2048,
    )
    data = res.data or {}
    rebuilt, concepts = from_concepts(data.get("concepts"))
    expr = _parses(str(data.get("expression") or "")) if isinstance(data.get("expression"), str) else None
    notes: list[str] = []
    if expr is None:
        expr = _parses(rebuilt) if rebuilt else None
        if expr is not None and data.get("expression"):
            notes.append("The model's expression did not parse, so it was rebuilt from its concept lists.")
    if expr is None:
        return None
    note = data.get("notes")
    if isinstance(note, str) and note.strip():
        notes.insert(0, " ".join(note.split())[:400])
    return Suggestion(expr, concepts, notes, res.provider, res.model)
