"""Which screening decisions an eligibility-criteria change affects. Stdlib only, deterministic.

A decision recorded under an earlier protocol version is never silently carried forward. Each is classified:
  needs_redecision  excluded for a criterion that was removed or rejected in the new version
  needs_review      excluded for a criterion whose text changed; or included/uncertain while criteria were added
                    or changed (the record may now fail a criterion)
  carry_forward     the basis of the decision is unchanged; a person may confirm it under the new version in bulk
"""

from __future__ import annotations

from typing import Any


def _active(criteria: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["stable_key"]: c for c in criteria if c.get("state", "accepted") == "accepted"}


def criteria_changes(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, list[str]]:
    o, n = _active(old), _active(new)
    return {
        "added": [k for k in n if k not in o],
        "removed": [k for k in o if k not in n],
        "changed": [
            k for k in n if k in o and (n[k]["text"].strip() != o[k]["text"].strip() or n[k]["kind"] != o[k]["kind"])
        ],
    }


def classify(decisions: list[dict[str, Any]], old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, Any]:
    """`decisions`: [{paper_id, decision, reason_code}] recorded under the OLD version."""
    ch = criteria_changes(old, new)
    any_change = bool(ch["added"] or ch["removed"] or ch["changed"])
    out: dict[str, list[dict[str, Any]]] = {"needs_redecision": [], "needs_review": [], "carry_forward": []}
    n = _active(new)
    for d in decisions:
        code = d.get("reason_code") or ""
        key = code.split(":", 1)[1] if ":" in code else None
        if d["decision"] == "exclude" and key is not None:
            if key in ch["removed"] or key not in n:
                out["needs_redecision"].append({**d, "why": "Excluded for a criterion that no longer applies."})
            elif key in ch["changed"]:
                out["needs_review"].append({**d, "why": "Excluded for a criterion whose wording changed."})
            else:
                out["carry_forward"].append({**d, "why": "Excluded for a criterion that is unchanged."})
        elif d["decision"] in ("include", "uncertain") and any_change:
            out["needs_review"].append({**d, "why": "Eligibility criteria changed since this record was included."})
        else:
            out["carry_forward"].append({**d, "why": "The basis of this decision is unchanged."})
    return {
        "criteria_changes": ch,
        "counts": {k: len(v) for k, v in out.items()},
        **out,
    }
