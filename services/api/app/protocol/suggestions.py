"""Template suggestions built ONLY from what the user already entered. Stdlib only.

These are mechanical restatements (e.g. the Population element becomes an inclusion criterion "Population: ..."),
labelled as suggestions, each with a rationale naming the answer it came from. They never add facts the user did
not provide, and every suggested criterion starts as 'proposed' so the user must accept or reject it.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.protocol.content import effective_criteria

# Element keys that describe *who/what is studied* and therefore restate naturally as inclusion criteria.
_INCLUSION_ELEMENTS = {
    "population",
    "intervention",
    "exposure",
    "outcome",
    "sample",
    "phenomenon",
    "design",
    "evaluation",
    "research_type",
    "comparison",
    "comparator",
}


def _sk(namespace: uuid.UUID, name: str) -> str:
    """Deterministic stable key: re-proposing the same suggestion yields the same key (no duplicates)."""
    return str(uuid.uuid5(namespace, name))


def propose_criteria(content: dict[str, Any], namespace: uuid.UUID) -> list[dict[str, Any]]:
    """Suggested criteria not already present (by stable key or identical text)."""
    existing_keys = {c["stable_key"] for c in content.get("criteria", [])}
    existing_text = {c["text"].lower() for c in content.get("criteria", [])}
    out: list[dict[str, Any]] = []

    def propose(name: str, kind: str, text: str, rationale: str) -> None:
        key = _sk(namespace, name)
        if key in existing_keys or text.lower() in existing_text:
            return
        out.append(
            {
                "stable_key": key,
                "kind": kind,
                "text": text,
                "rationale": rationale,
                "origin": "suggested",
                "state": "proposed",
            }
        )

    for e in content.get("framework_elements", []):
        if e.get("value") and (e["key"] in _INCLUSION_ELEMENTS or content.get("framework") == "custom"):
            propose(
                f"element:{e['key']}:{e['value'].lower()}",
                "inclusion",
                f"{e['label']}: {e['value']}",
                f"Restates your '{e['label']}' answer.",
            )
    df, dt = content.get("date_from"), content.get("date_to")
    if df is not None or dt is not None:
        span = f"{df if df is not None else 'any year'} to {dt if dt is not None else 'the present'}"
        propose(f"years:{df}:{dt}", "inclusion", f"Published from {span}.", "Restates your date range.")
    if content.get("languages"):
        langs = ", ".join(content["languages"])
        propose(f"lang:{langs.lower()}", "inclusion", f"Published in: {langs}.", "Restates your language choice.")
        propose(
            f"lang-ex:{langs.lower()}",
            "exclusion",
            f"Published only in languages other than: {langs}.",
            "The counterpart of your language choice; reject it if non-listed languages should be translated.",
        )
    if content.get("study_designs"):
        designs = ", ".join(content["study_designs"])
        propose(f"design:{designs.lower()}", "inclusion", f"Study design: {designs}.", "Restates your study designs.")
    return out


def propose_question(content: dict[str, Any]) -> str | None:
    """A draft review question assembled from the framework elements, or None if there is too little to go on."""
    v = {e["key"]: e.get("value") for e in content.get("framework_elements", []) if e.get("value")}
    fw = content.get("framework")
    if fw == "pico" and v.get("intervention") and v.get("outcome"):
        who = f"In {v['population']}, " if v.get("population") else ""
        vs = f" compared with {v['comparison']}" if v.get("comparison") else ""
        return f"{who}what is the effect of {v['intervention']}{vs} on {v['outcome']}?"
    if fw == "peco" and v.get("exposure") and v.get("outcome"):
        who = f"In {v['population']}, " if v.get("population") else ""
        vs = f" compared with {v['comparator']}" if v.get("comparator") else ""
        return f"{who}is exposure to {v['exposure']}{vs} associated with {v['outcome']}?"
    if fw == "spider" and v.get("phenomenon"):
        who = f" among {v['sample']}" if v.get("sample") else ""
        what = v.get("evaluation") or "experiences and views"
        return f"What are the {what} of {v['phenomenon']}{who}?"
    return None


def accepted_count(content: dict[str, Any]) -> int:
    return len(effective_criteria(content))
