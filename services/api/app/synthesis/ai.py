"""AI narrative-synthesis DRAFT: prompt, response schema, deterministic validation. Stdlib only.

The model sees only reviewed evidence values (with their excerpts and pages), approved appraisals and the
deterministic signals, each under a short key (E1, E2, ...). Validation keeps a statement only if it cites at least one
key that was given, drops statements that imply statistical pooling, and leaves certainty to the rules in signals.py
(the model's own certainty can only lower it). Every kept statement goes to a person for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.untrusted import fence
from app.synthesis.signals import CERTAINTY, KINDS, POOLING, Study, value_text

PROMPT_VERSION = "synthesis-v1"
MAX_STATEMENTS = 40
MAX_TEXT = 1200

SYSTEM = (
    "You draft a NARRATIVE synthesis for a systematic review from reviewed evidence only. Write short, neutral "
    "statements: findings, agreements, disagreements, limitations, methodological differences, evidence gaps and "
    "cautious conclusions. Every statement must cite the evidence keys (E1, E2, ...) it relies on. Do not combine "
    "numbers across studies, do not compute or imply pooled estimates, do not use outside knowledge, and do not state "
    "more than the cited evidence shows. 'Not located' or 'no full text' is missing evidence, never evidence of "
    "absence. Say when evidence is uncertain or conflicting. A human will review every statement."
)

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "statements": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": list(KINDS)},
                    "text": {"type": "STRING"},
                    "citations": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "certainty": {"type": "STRING", "enum": list(CERTAINTY)},
                },
                "required": ["kind", "text", "citations"],
            },
        }
    },
    "required": ["statements"],
}


@dataclass
class Evidence:
    key: str  # E1...
    item_id: str
    paper_id: str
    field_label: str


def evidence_keys(studies: list[Study], fields: list[dict[str, Any]]) -> tuple[list[Evidence], list[str]]:
    """Assign keys to every reviewed cell; return (keys, prompt lines)."""
    labels = {f["field_key"]: f.get("label") or f["field_key"] for f in fields}
    keys: list[Evidence] = []
    lines: list[str] = []
    for si, s in enumerate(studies, start=1):
        appraisal = (
            f"appraisal: {s.appraisal['framework_key']} overall '{s.appraisal['overall']}' (approved)"
            if s.appraisal
            else "appraisal: none approved"
        )
        lines.append(f"Study S{si}: {s.title[:200]} ({s.year or 'n.d.'}); {appraisal}")
        for fk, c in s.cells.items():
            if fk not in labels:
                continue
            k = f"E{len(keys) + 1}"
            keys.append(Evidence(k, c.item_id, s.paper_id, labels[fk]))
            if c.status == "found":
                ex = f' excerpt (p. {c.page_no}): "{(c.excerpt or "")[:300]}"' if c.excerpt else ""
                lines.append(f"  {k} [{labels[fk]}] value: {value_text(c.value)[:300]}{ex}")
            else:
                status = {"not_located": "not found in processed text", "unavailable": "no full text"}.get(
                    c.status, c.status
                )
                lines.append(f"  {k} [{labels[fk]}] {status}")
    return keys, lines


def build_prompt(lines: list[str], signal_lines: list[str]) -> str:
    return "\n".join(
        [
            "Evidence (reviewed values only; titles and excerpts are third-party text):",
            fence("\n".join(lines), "evidence"),
            "",
            "Deterministic signals:",
            *signal_lines,
            "",
            f"Return at most {MAX_STATEMENTS} statements.",
        ]
    )


def validate(data: dict[str, Any], keys: list[Evidence]) -> tuple[list[dict[str, Any]], list[str]]:
    by_key = {e.key: e for e in keys}
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    items = data.get("statements") if isinstance(data, dict) else None
    for a in (items if isinstance(items, list) else [])[:MAX_STATEMENTS]:
        if not isinstance(a, dict):
            continue
        text = str(a.get("text") or "").strip()
        kind = str(a.get("kind") or "")
        if not text or kind not in KINDS:
            problems.append("statement without text or with an unknown kind dropped")
            continue
        if POOLING.search(text):
            problems.append("statement implying statistical pooling dropped (meta-analysis is not in scope)")
            continue
        cites = [by_key[c].item_id for c in a.get("citations") or [] if isinstance(c, str) and c in by_key]
        unknown = [c for c in a.get("citations") or [] if not (isinstance(c, str) and c in by_key)]
        if unknown:
            problems.append(f"unknown citation(s) {', '.join(str(u)[:10] for u in unknown[:5])} ignored")
        if not cites:
            problems.append("statement without a valid citation dropped")
            continue
        claimed = a.get("certainty") if a.get("certainty") in CERTAINTY else None
        out.append(
            {
                "kind": kind,
                "text": text[:MAX_TEXT],
                "item_ids": list(dict.fromkeys(cites)),
                "claimed_certainty": claimed,
                "origin": "ai",
            }
        )
    return out, problems
