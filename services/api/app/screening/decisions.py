"""Screening decision rules. Stdlib only.

Decisions are human records. A decision is one of include / exclude / uncertain, or `cleared` (an undo back to
"no decision"). Exclusion reasons come from the approved protocol (an exclusion criterion that applies, or an
inclusion criterion that is not met) plus a few generic reasons; "other" needs a note. At title/abstract stage a
reason is encouraged and can be required by configuration; full-text screening (Phase 7) will always require one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STAGES = ("title_abstract",)
DECISIONS = ("include", "exclude", "uncertain", "cleared")
HUMAN_DECISIONS = ("include", "exclude", "uncertain")
ACTIONS = ("decide", "bulk", "undo")
GENERIC_REASONS = {
    "off_topic": "Not about the review topic",
    "wrong_publication_type": "Wrong publication type (e.g. editorial, comment, protocol only)",
    "duplicate": "Duplicate of another record",
    "other": "Other (explain in the note)",
}
MAX_NOTE = 2000


class DecisionError(ValueError):
    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Reason:
    code: str
    label: str
    group: str  # exclusion_criterion | inclusion_not_met | generic


def reason_options(content: dict[str, Any]) -> list[Reason]:
    accepted = [c for c in content.get("criteria") or [] if c.get("state", "accepted") == "accepted"]
    out = [
        Reason(f"exclusion:{c['stable_key']}", f"Meets exclusion criterion: {c['text']}", "exclusion_criterion")
        for c in accepted
        if c.get("kind") == "exclusion"
    ]
    out += [
        Reason(f"inclusion:{c['stable_key']}", f"Does not meet inclusion criterion: {c['text']}", "inclusion_not_met")
        for c in accepted
        if c.get("kind") == "inclusion"
    ]
    out += [Reason(k, v, "generic") for k, v in GENERIC_REASONS.items()]
    return out


def check(
    decision: str, reason_code: str | None, note: str | None, options: list[Reason], *, require_reason: bool
) -> tuple[str | None, str | None, str | None]:
    """Validate one decision. Returns (reason_code, reason_label, note) normalised. Raises DecisionError."""
    if decision not in HUMAN_DECISIONS:
        raise DecisionError("Choose include, exclude or uncertain.", "decision")
    note = (note or "").strip() or None
    if note and len(note) > MAX_NOTE:
        raise DecisionError(f"The note is too long (limit {MAX_NOTE} characters).", "note")
    if decision != "exclude":
        if reason_code:
            raise DecisionError("A reason applies only to an exclusion; use the note instead.", "reason_code")
        return None, None, note
    if not reason_code:
        if require_reason:
            raise DecisionError("Choose a reason for excluding this record.", "reason_code")
        return None, None, note
    by_code = {r.code: r for r in options}
    r = by_code.get(reason_code)
    if r is None:
        raise DecisionError("That reason is not one of the approved protocol's reasons.", "reason_code")
    if r.code == "other" and not note:
        raise DecisionError("Explain the 'other' reason in the note.", "note")
    return r.code, r.label[:500], note
