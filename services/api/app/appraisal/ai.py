"""AI appraisal SUGGESTIONS: prompt, response schema, deterministic validation. Stdlib only.

A suggestion is kept only with a quote that occurs in the page text (located with the Phase 9 matcher and stored as
the exact source substring); a judgement the text cannot support becomes the framework's "no information" value.
Suggestions are stored apart from reviewer judgements and never count as a judgement until a reviewer decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.appraisal.frameworks import NO_INFO, Framework
from app.extraction.extractor import PageText, locate_quote

PROMPT_VERSION = "appraisal-v1"
MAX_TEXT = 800

SYSTEM = (
    "You help a researcher critically appraise ONE study for a systematic review. For each domain, suggest a "
    "judgement from the allowed scale, justify it in at most 60 words using ONLY the page text given, and copy a short "
    "quote word for word from the page that supports it, with the page number. If the text does not report what the "
    "domain needs, choose the 'no information' / 'unclear' value. Do not use outside knowledge about the study. A "
    "human reviewer makes the final judgement."
)

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "domains": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "domain": {"type": "STRING"},
                    "judgement": {"type": "STRING"},
                    "justification": {"type": "STRING"},
                    "page": {"type": "INTEGER"},
                    "quote": {"type": "STRING"},
                },
                "required": ["domain", "judgement"],
            },
        }
    },
    "required": ["domains"],
}


@dataclass
class Suggestion:
    judgement: str
    justification: str | None
    excerpt: str | None = None
    page_id: str | None = None
    page_no: int | None = None
    chunk_index: int = 0
    supported: bool = False


def no_info_value(fw: Framework) -> str:
    return next((j for j in fw.judgements if j in NO_INFO), fw.judgements[-1])


def build_prompt(fw: Framework, title: str | None, chunk: list[PageText], part: int, parts: int) -> str:
    lines = [
        f"Study title: {title or 'unknown'}",
        f"Framework: {fw.label} ({fw.based_on}).",
        f"Allowed judgements: {', '.join(fw.judgements)}.",
        f"Text part {part} of {parts}.",
        "",
        "Domains:",
        *(f"- {d.key}: {d.label}. {d.prompt}" for d in fw.domains),
    ]
    for p in chunk:
        lines += ["", f"=== PAGE {p.page_no} ===", p.text]
    return "\n".join(lines)


def validate(fw: Framework, chunk: list[PageText], data: dict[str, Any], chunk_index: int = 0) -> dict[str, Suggestion]:
    keys = {d.key for d in fw.domains}
    pages = {p.page_no: p for p in chunk}
    out: dict[str, Suggestion] = {}
    items = data.get("domains") if isinstance(data, dict) else None
    for a in items if isinstance(items, list) else []:
        if not isinstance(a, dict):
            continue
        key = str(a.get("domain") or "")
        judgement = str(a.get("judgement") or "")
        if key not in keys or key in out or judgement not in fw.judgements:
            continue
        just = (str(a.get("justification") or "").strip() or None) if a.get("justification") else None
        just = just[:MAX_TEXT] if just else None
        quote = str(a.get("quote") or "")[:1200]
        found: tuple[str, PageText] | None = None
        if quote.strip():
            cited = pages.get(a["page"]) if isinstance(a.get("page"), int) else None
            for p in ([cited] if cited else []) + [p for p in chunk if p is not cited]:
                if ex := locate_quote(p.text, quote):
                    found = (ex, p)
                    break
        if judgement not in NO_INFO and found is None:
            # A substantive judgement needs text support; without it the suggestion is "no information".
            out[key] = Suggestion(
                no_info_value(fw),
                "The AI's judgement could not be supported with a quote from the text and was not kept.",
                chunk_index=chunk_index,
            )
            continue
        s = Suggestion(judgement, just, chunk_index=chunk_index, supported=found is not None)
        if found:
            s.excerpt, s.page_id, s.page_no = found[0], found[1].page_id, found[1].page_no
        out[key] = s
    return out


def merge(fw: Framework, per_chunk: list[dict[str, Suggestion]]) -> dict[str, Suggestion]:
    """Supported suggestions beat unsupported ones; differing supported judgements -> the more serious one, noted."""
    order = fw.severity or fw.judgements
    merged: dict[str, Suggestion] = {}
    for d in fw.domains:
        cands = [c[d.key] for c in per_chunk if d.key in c]
        supported = [s for s in cands if s.supported]
        if supported:
            worst = max(supported, key=lambda s: order.index(s.judgement) if s.judgement in order else 0)
            if len({s.judgement for s in supported}) > 1:
                worst.justification = (
                    (worst.justification or "") + " (Other parts of the text suggested a different judgement.)"
                ).strip()
            merged[d.key] = worst
        elif cands:
            merged[d.key] = cands[0]
    return merged
