"""Contextual workspace actions, deterministic parts. Stdlib only.

* `search_pages`: "Find supporting evidence" searches the paper's stored page text for the terms of a field or a
  query and returns ranked passages that are EXACT substrings of the page (so they can be saved as a verbatim
  excerpt with their page number). It finds candidate text; it never decides what the evidence is.
* `explain_lines`: "Explain this exclusion" turns the recorded decision, its reason/criterion, the reviewer's note and
  any advisory AI assessment into plain sentences. Nothing is inferred beyond the record.
"""

from __future__ import annotations

import re
from typing import Any

STOPWORDS = frozenset(
    "the and for with that this from were was are been have has had not but all any can its into than then them they "
    "their there these those which while who whom what when where why how our out per via also such other more most "
    "between among within without about after before over under study studies paper report reported number".split()
)
WINDOW = 320
MAX_HITS = 8


def terms_for(query: str | None, field: dict[str, Any] | None) -> list[str]:
    """Search terms from a free-text query, or from a field's label, hint and options."""
    text = query or ""
    if not text and field:
        text = " ".join(
            [
                str(field.get("label") or field.get("field_key") or ""),
                str(field.get("hint") or ""),
                " ".join(str(o) for o in field.get("options") or []),
            ]
        )
    words = re.findall(r"[a-z0-9][a-z0-9-]{2,}", text.lower())
    return list(dict.fromkeys(w for w in words if w not in STOPWORDS))[:20]


def _best_window(text: str, low: str, terms: list[str]) -> tuple[int, int, list[str]] | None:
    positions = [(m.start(), t) for t in terms for m in re.finditer(r"\b" + re.escape(t), low)]
    if not positions:
        return None
    positions.sort()
    best: tuple[int, int, list[str]] | None = None
    for i, (start, _) in enumerate(positions):
        inside = {t for p, t in positions[i:] if p < start + WINDOW}
        if best is None or len(inside) > len(best[2]):
            best = (start, start + WINDOW, sorted(inside))
    assert best is not None
    s, e, found = best
    s = max(0, s - 60)
    # widen to word boundaries so the passage reads cleanly; it stays an exact substring
    while s > 0 and not text[s - 1].isspace():
        s -= 1
    e = min(len(text), e)
    while e < len(text) and not text[e].isspace():
        e += 1
    return s, e, found


def search_pages(pages: list[tuple[int, str]], terms: list[str], max_hits: int = MAX_HITS) -> list[dict[str, Any]]:
    """Ranked passages: [{page_no, excerpt, matched_terms, score, truncated_start, truncated_end}]."""
    if not terms:
        return []
    hits: list[dict[str, Any]] = []
    for page_no, text in pages:
        if not text:
            continue
        w = _best_window(text, text.lower(), terms)
        if w is None:
            continue
        s, e, found = w
        excerpt = text[s:e].strip()
        offset = text.find(excerpt, s)
        hits.append(
            {
                "page_no": page_no,
                "excerpt": excerpt,
                "matched_terms": found,
                "score": len(found),
                "truncated_start": offset > 0,
                "truncated_end": offset + len(excerpt) < len(text),
            }
        )
    hits.sort(key=lambda h: (-h["score"], h["page_no"]))
    return hits[:max_hits]


STAGE_LABELS = {"title_abstract": "title/abstract screening", "full_text": "full-text screening"}


def explain_lines(stages: list[dict[str, Any]], ai: list[dict[str, Any]]) -> list[str]:
    """Plain sentences describing why (and by whom) a record was excluded or what was decided."""
    lines: list[str] = []
    for s in stages:
        where = STAGE_LABELS.get(s["stage"], s["stage"].replace("_", " "))
        when = f" on {s['decided_at'][:10]}" if s.get("decided_at") else ""
        if s["decision"] == "exclude":
            why = s.get("criterion_text") or s.get("reason_label") or s.get("reason_code")
            line = f"Excluded at {where}{when} by a reviewer"
            line += f", for the reason: {why}." if why else ", without a recorded reason."
            if s.get("criterion_kind") == "inclusion":
                line += " The record was judged not to meet this inclusion criterion."
            elif s.get("criterion_kind") == "exclusion":
                line += " The record was judged to meet this exclusion criterion."
            lines.append(line)
        else:
            lines.append(f"Marked '{s['decision']}' at {where}{when} by a reviewer.")
        if s.get("note"):
            lines.append(f"Reviewer's note: {s['note']}")
        if s.get("current_protocol") is False:
            lines.append("This decision was made under an earlier protocol version and may need review.")
    if not stages:
        lines.append("No screening decision has been recorded for this record yet.")
    for a in ai:
        label = "AI relevance assessment" if a["method"] == "llm" else "Word-overlap relevance score"
        bits = [f"{label} (advisory only, never a decision): band '{a['band'].replace('_', ' ')}', score {a['score']}"]
        if a.get("suggestion"):
            bits.append(f"suggested '{a['suggestion']}'")
        line = "; ".join(bits) + "."
        if a.get("summary"):
            line += f" Its stated reason: {a['summary']}"
        lines.append(line)
    return lines
