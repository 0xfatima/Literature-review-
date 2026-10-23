"""AI relevance assessment: prompt, response schema and deterministic validation. Stdlib only.

"AI proposes; deterministic code validates; humans decide." The model returns, per paper, a relevance score with a
short reason and an OPTIONAL eligibility suggestion. Validation then:
  * accepts only papers and criteria that were in the request (unknown ids are dropped and reported);
  * keeps an evidence quote only if it occurs verbatim in that paper's title or abstract;
  * downgrades an "exclude" suggestion to "uncertain" unless at least one criterion is judged not met WITH a
    verified quote (an exclusion needs evidence);
  * forces "uncertain" + band `insufficient` when the model says the record has too little information.
Nothing here writes a screening decision. Suggestions are stored as suggestions and shown as such.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.screening.relevance import LLM_THRESHOLDS, band_for

METHOD = "llm"
PROMPT_VERSION = "relevance-v1"
MAX_ABSTRACT_CHARS = 3000
SUGGESTIONS = ("include", "exclude", "uncertain")
JUDGEMENTS = ("met", "not_met", "unclear")

SYSTEM = (
    "You assist a researcher screening records for a systematic review. You judge ONLY from the title, keywords and "
    "abstract given. Never invent facts that are not in the record. If the record does not say something, the "
    "criterion is 'unclear', not 'not_met'. Relevance (does the record address the research questions?) is separate "
    "from eligibility (does it satisfy the inclusion and exclusion criteria?): a highly relevant record can still be "
    "ineligible. Quotes must be copied exactly, word for word, from the record's title or abstract. Your output is a "
    "suggestion that a human will review; be calibrated and say when information is insufficient."
)

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "assessments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "paper": {"type": "STRING", "description": "The paper id exactly as given, e.g. P3"},
                    "relevance": {"type": "INTEGER", "description": "0-100: how directly it addresses the questions"},
                    "relevance_reason": {"type": "STRING", "description": "At most 50 words"},
                    "insufficient_information": {"type": "BOOLEAN"},
                    "questions": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "question": {"type": "STRING"},
                                "relevance": {"type": "STRING", "enum": ["high", "medium", "low", "none"]},
                            },
                            "required": ["question", "relevance"],
                        },
                    },
                    "eligibility_suggestion": {"type": "STRING", "enum": list(SUGGESTIONS)},
                    "criteria": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "criterion": {"type": "STRING"},
                                "judgement": {"type": "STRING", "enum": list(JUDGEMENTS)},
                                "evidence_quote": {"type": "STRING", "description": "Exact words from the record"},
                            },
                            "required": ["criterion", "judgement"],
                        },
                    },
                    "eligibility_reason": {"type": "STRING", "description": "At most 50 words"},
                },
                "required": [
                    "paper",
                    "relevance",
                    "relevance_reason",
                    "insufficient_information",
                    "eligibility_suggestion",
                ],
            },
        }
    },
    "required": ["assessments"],
}


@dataclass
class PaperInput:
    key: str  # P1, P2 ... (short ids keep the prompt small and stop the model from echoing UUIDs wrongly)
    paper_id: str
    title: str
    abstract: str | None
    keywords: list[str]
    year: int | None
    publication_type: str | None


@dataclass
class ProtocolInput:
    objective: str | None
    questions: list[tuple[str, str]]  # (Q1, text)
    criteria: list[tuple[str, str, str, str]]  # (C1, kind, text, stable_key)


def protocol_input(content: dict[str, Any]) -> ProtocolInput:
    qs = [(f"Q{i}", str(q.get("text") or "")) for i, q in enumerate(content.get("questions") or [], 1) if q.get("text")]
    crit = [
        (f"C{i}", str(c.get("kind")), str(c.get("text") or ""), str(c.get("stable_key") or ""))
        for i, c in enumerate(
            [c for c in content.get("criteria") or [] if c.get("state", "accepted") == "accepted"], start=1
        )
    ]
    return ProtocolInput(content.get("objective"), qs, crit)


def build_prompt(protocol: ProtocolInput, papers: list[PaperInput]) -> str:
    lines = ["REVIEW PROTOCOL"]
    if protocol.objective:
        lines.append(f"Objective: {protocol.objective}")
    lines.append("Research questions:")
    lines += [f"  {k}: {t}" for k, t in protocol.questions] or ["  (none given)"]
    lines.append("Eligibility criteria:")
    lines += [f"  {k} [{kind}]: {t}" for k, kind, t, _ in protocol.criteria] or ["  (none given)"]
    lines.append("")
    lines.append(f"RECORDS ({len(papers)})")
    for p in papers:
        lines.append(f"--- {p.key}")
        lines.append(f"Title: {p.title}")
        if p.year:
            lines.append(f"Year: {p.year}")
        if p.publication_type:
            lines.append(f"Type: {p.publication_type}")
        if p.keywords:
            lines.append(f"Keywords: {', '.join(p.keywords[:15])}")
        lines.append(f"Abstract: {(p.abstract or '(no abstract available)')[:MAX_ABSTRACT_CHARS]}")
    lines.append("")
    lines.append(
        "Return one assessment per record, using the record ids above. For each criterion you can judge, give the "
        "judgement and an exact quote; use 'unclear' when the record does not say. Suggest 'exclude' only when a "
        "quoted passage shows a criterion is not met; otherwise 'include' or 'uncertain'."
    )
    return "\n".join(lines)


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).casefold()
    s = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", s)
    return " ".join(s.split())


@dataclass
class Validated:
    paper_id: str
    score: int
    band: str
    confidence: str
    explanation: dict[str, Any]
    suggestion: str | None
    suggestion_detail: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def validate(
    data: dict[str, Any], protocol: ProtocolInput, papers: list[PaperInput]
) -> tuple[list[Validated], list[str], list[str]]:
    """Returns (valid assessments, paper keys the model did not answer, response-level warnings)."""
    by_key = {p.key: p for p in papers}
    crit = {k: (kind, text, sk) for k, kind, text, sk in protocol.criteria}
    qkeys = {k: t for k, t in protocol.questions}
    out: list[Validated] = []
    problems: list[str] = []
    seen: set[str] = set()
    items = data.get("assessments")
    if not isinstance(items, list):
        return [], list(by_key), ["The AI answer had no list of assessments."]
    for a in items:
        if not isinstance(a, dict):
            continue
        key = str(a.get("paper") or "").strip()
        p = by_key.get(key)
        if p is None or key in seen:
            problems.append(f"Ignored an assessment for unknown or repeated record {key!r}.")
            continue
        seen.add(key)
        warn: list[str] = []
        try:
            score = max(0, min(100, int(a.get("relevance"))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            problems.append(f"{key}: relevance was not a number; record left for a retry.")
            seen.discard(key)
            continue
        haystack = _norm(f"{p.title} {p.abstract or ''}")
        checked: list[dict[str, Any]] = []
        for c in a.get("criteria") or []:
            if not isinstance(c, dict):
                continue
            ck = str(c.get("criterion") or "").strip()
            if ck not in crit:
                warn.append(f"Dropped a judgement on unknown criterion {ck!r}.")
                continue
            judgement = c.get("judgement") if c.get("judgement") in JUDGEMENTS else "unclear"
            quote = str(c.get("evidence_quote") or "").strip()
            verified = bool(quote) and len(quote) >= 8 and _norm(quote) in haystack
            if quote and not verified:
                warn.append(f"{ck}: the quoted text was not found in the record and was removed.")
            kind, text, sk = crit[ck]
            checked.append(
                {
                    "criterion": ck,
                    "criterion_key": sk,
                    "kind": kind,
                    "text": text[:300],
                    "judgement": judgement,
                    "evidence_quote": quote[:500] if verified else None,
                    "quote_verified": verified,
                }
            )
        insufficient = bool(a.get("insufficient_information")) or (not p.abstract and score < 35)
        suggestion = a.get("eligibility_suggestion") if a.get("eligibility_suggestion") in SUGGESTIONS else "uncertain"

        def violated(c: dict[str, Any]) -> bool:
            # an inclusion criterion "not met", or an exclusion criterion "met", each backed by a verified quote
            bad = (c["kind"] == "inclusion" and c["judgement"] == "not_met") or (
                c["kind"] == "exclusion" and c["judgement"] == "met"
            )
            return bool(bad and c["quote_verified"])

        if suggestion == "exclude" and not any(violated(c) for c in checked):
            suggestion = "uncertain"
            warn.append(
                "The AI suggested exclusion without a verified quote showing a criterion is violated; "
                "shown as 'uncertain' instead."
            )
        if insufficient and suggestion != "uncertain":
            suggestion = "uncertain"
            warn.append("Too little information in the record for a suggestion; shown as 'uncertain'.")
        per_q = [
            {
                "question": str(q.get("question")),
                "text": qkeys[str(q.get("question"))][:300],
                "relevance": q.get("relevance"),
            }
            for q in a.get("questions") or []
            if isinstance(q, dict)
            and str(q.get("question")) in qkeys
            and q.get("relevance") in ("high", "medium", "low", "none")
        ]
        out.append(
            Validated(
                paper_id=p.paper_id,
                score=score,
                band="insufficient" if insufficient else band_for(score, LLM_THRESHOLDS),
                confidence="low" if insufficient or not p.abstract else "normal",
                explanation={
                    "method": PROMPT_VERSION,
                    "summary": str(a.get("relevance_reason") or "")[:600],
                    "questions": per_q,
                    "insufficient_information": insufficient,
                },
                suggestion=suggestion,
                suggestion_detail={
                    "reason": str(a.get("eligibility_reason") or "")[:600],
                    "criteria": checked,
                    "model_suggestion": a.get("eligibility_suggestion"),
                },
                warnings=warn,
            )
        )
    missing = [k for k in by_key if k not in seen]
    return out, missing, problems
