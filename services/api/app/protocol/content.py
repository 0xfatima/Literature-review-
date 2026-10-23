"""The protocol document: normalisation, hashing, approval validation, summary and diff. Stdlib only.

A protocol's *content* is a plain dict (see EMPTY). Services convert ORM rows to/from it; everything here works on
that dict, so the rules are testable without a database. Questions and criteria carry a `stable_key` (a UUID string)
that survives revisions, which is what lets a diff say "criterion X changed" rather than "list changed".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.protocol.frameworks import FRAMEWORK_KEYS, FRAMEWORKS, MAX_CUSTOM_ELEMENTS

CRITERION_KINDS = ("inclusion", "exclusion")
CRITERION_STATES = ("proposed", "accepted", "rejected")
CRITERION_ORIGINS = ("user", "suggested")
FRAMEWORK_SOURCES = ("suggested", "user", "skipped")

MAX_TEXT = 2000  # objective, criterion and question text
MAX_LONG_TEXT = 8000  # search strategy, extraction and synthesis plans
MAX_ITEMS = 50  # questions, criteria
MAX_LIST = 30  # languages, designs, sources
MAX_TAG = 120
MIN_YEAR = 1800

SCALAR_FIELDS = ("objective", "search_strategy", "extraction_plan", "synthesis_plan")
LIST_FIELDS = ("languages", "study_designs", "sources")
FIELD_LABELS = {
    "objective": "Objective",
    "framework": "Question framework",
    "framework_elements": "Framework elements",
    "questions": "Review questions",
    "criteria": "Eligibility criteria",
    "date_range": "Date range",
    "languages": "Languages",
    "study_designs": "Study designs",
    "sources": "Sources",
    "search_strategy": "Search strategy",
    "extraction_plan": "Extraction plan",
    "synthesis_plan": "Synthesis plan",
}


def empty() -> dict[str, Any]:
    return {
        "objective": None,
        "framework": None,
        "framework_source": None,
        "framework_elements": [],
        "questions": [],
        "criteria": [],
        "date_from": None,
        "date_to": None,
        "languages": [],
        "study_designs": [],
        "sources": [],
        "search_strategy": None,
        "extraction_plan": None,
        "synthesis_plan": None,
    }


class ContentError(ValueError):
    """Invalid protocol content. `details` uses the API error-detail shape: [{field, issue}]."""

    def __init__(self, message: str, details: list[dict[str, Any]]):
        super().__init__(message)
        self.details = details


# ---------- normalisation ----------


def _text(value: Any, field: str, limit: int, errors: list[dict[str, Any]]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append({"field": field, "issue": "not_text"})
        return None
    t = value.strip()
    t = re.sub(r"[ \t]+", " ", t)
    if len(t) > limit:
        errors.append({"field": field, "issue": "too_long", "limit": limit})
        return None
    return t or None


def _tags(values: Any, field: str, errors: list[dict[str, Any]]) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        errors.append({"field": field, "issue": "not_a_list"})
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        t = _text(v, field, MAX_TAG, errors)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    if len(out) > MAX_LIST:
        errors.append({"field": field, "issue": "too_many", "limit": MAX_LIST})
    return out


def _year(value: Any, field: str, errors: list[dict[str, Any]], today: date) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append({"field": field, "issue": "not_a_year"})
        return None
    if not (MIN_YEAR <= value <= today.year + 1):
        errors.append({"field": field, "issue": "out_of_range", "min": MIN_YEAR, "max": today.year + 1})
        return None
    return value


def normalize(raw: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Validate shape and limits; trim text; de-duplicate tags. Raises ContentError listing every problem.
    Does NOT check completeness (that is `approval_issues`): drafts may be partial (spec: save for later)."""
    today = today or date.today()
    errors: list[dict[str, Any]] = []
    out = empty()
    out["objective"] = _text(raw.get("objective"), "objective", MAX_TEXT, errors)
    for f in ("search_strategy", "extraction_plan", "synthesis_plan"):
        out[f] = _text(raw.get(f), f, MAX_LONG_TEXT, errors)
    for f in LIST_FIELDS:
        out[f] = _tags(raw.get(f), f, errors)
    out["date_from"] = _year(raw.get("date_from"), "date_from", errors, today)
    out["date_to"] = _year(raw.get("date_to"), "date_to", errors, today)

    fw = raw.get("framework")
    if fw is not None and fw not in FRAMEWORK_KEYS:
        errors.append({"field": "framework", "issue": "unknown", "allowed": list(FRAMEWORK_KEYS)})
        fw = None
    out["framework"] = fw
    src = raw.get("framework_source")
    if src is not None and src not in FRAMEWORK_SOURCES:
        errors.append({"field": "framework_source", "issue": "unknown"})
        src = None
    out["framework_source"] = src if fw is not None else None

    out["framework_elements"] = _elements(fw, raw.get("framework_elements") or [], errors)
    out["questions"] = _questions(raw.get("questions") or [], errors)
    out["criteria"] = _criteria(raw.get("criteria") or [], errors)
    if errors:
        raise ContentError("The protocol has invalid fields.", errors)
    return out


def _elements(fw: str | None, items: Any, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        errors.append({"field": "framework_elements", "issue": "not_a_list"})
        return []
    if fw in (None, "none"):
        return []
    by_key = {e.key: e for e in FRAMEWORKS[fw].elements}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, it in enumerate(items):
        f = f"framework_elements[{i}]"
        if not isinstance(it, dict):
            errors.append({"field": f, "issue": "not_an_object"})
            continue
        value = _text(it.get("value"), f + ".value", MAX_TEXT, errors)
        if fw == "custom":
            label = _text(it.get("label"), f + ".label", MAX_TAG, errors)
            if not label:
                if value:
                    errors.append({"field": f + ".label", "issue": "required"})
                continue
            key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or f"element_{i + 1}"
        else:
            key = str(it.get("key") or "")
            if key not in by_key:
                errors.append({"field": f + ".key", "issue": "unknown", "allowed": list(by_key)})
                continue
            label = by_key[key].label
        if key in seen:
            errors.append({"field": f + ".key", "issue": "duplicate"})
            continue
        seen.add(key)
        out.append({"key": key, "label": label, "value": value})
    if fw == "custom" and len(out) > MAX_CUSTOM_ELEMENTS:
        errors.append({"field": "framework_elements", "issue": "too_many", "limit": MAX_CUSTOM_ELEMENTS})
    if fw != "custom":  # canonical order = the framework's own order, every element present
        values = {e["key"]: e["value"] for e in out}
        out = [{"key": e.key, "label": e.label, "value": values.get(e.key)} for e in FRAMEWORKS[fw].elements]
    return out


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _key(value: Any, field: str, errors: list[dict[str, Any]], seen: set[str]) -> str | None:
    """stable_key: a lower-case UUID string supplied by the caller (the service assigns one to new items)."""
    k = str(value or "").lower()
    if not _UUID.match(k):
        errors.append({"field": field, "issue": "invalid_stable_key"})
        return None
    if k in seen:
        errors.append({"field": field, "issue": "duplicate"})
        return None
    seen.add(k)
    return k


def _questions(items: Any, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        errors.append({"field": "questions", "issue": "not_a_list"})
        return []
    if len(items) > MAX_ITEMS:
        errors.append({"field": "questions", "issue": "too_many", "limit": MAX_ITEMS})
        return []
    out, seen = [], set[str]()
    for i, it in enumerate(items):
        f = f"questions[{i}]"
        if not isinstance(it, dict):
            errors.append({"field": f, "issue": "not_an_object"})
            continue
        text = _text(it.get("text"), f + ".text", MAX_TEXT, errors)
        key = _key(it.get("stable_key"), f + ".stable_key", errors, seen)
        if not text:
            errors.append({"field": f + ".text", "issue": "required"})
            continue
        if key:
            out.append({"stable_key": key, "text": text})
    return out


def _criteria(items: Any, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        errors.append({"field": "criteria", "issue": "not_a_list"})
        return []
    if len(items) > MAX_ITEMS:
        errors.append({"field": "criteria", "issue": "too_many", "limit": MAX_ITEMS})
        return []
    out, seen = [], set[str]()
    for i, it in enumerate(items):
        f = f"criteria[{i}]"
        if not isinstance(it, dict):
            errors.append({"field": f, "issue": "not_an_object"})
            continue
        key = _key(it.get("stable_key"), f + ".stable_key", errors, seen)
        text = _text(it.get("text"), f + ".text", MAX_TEXT, errors)
        rationale = _text(it.get("rationale"), f + ".rationale", MAX_TEXT, errors)
        kind, state = it.get("kind"), it.get("state", "accepted")
        origin = it.get("origin", "user")
        if kind not in CRITERION_KINDS:
            errors.append({"field": f + ".kind", "issue": "unknown", "allowed": list(CRITERION_KINDS)})
        if state not in CRITERION_STATES:
            errors.append({"field": f + ".state", "issue": "unknown", "allowed": list(CRITERION_STATES)})
        if origin not in CRITERION_ORIGINS:
            errors.append({"field": f + ".origin", "issue": "unknown"})
        if not text:
            errors.append({"field": f + ".text", "issue": "required"})
            continue
        if origin == "user" and state == "proposed":
            state = "accepted"  # the user's own criterion needs no separate acceptance
        if key and kind in CRITERION_KINDS and state in CRITERION_STATES and origin in CRITERION_ORIGINS:
            out.append(
                {
                    "stable_key": key,
                    "kind": kind,
                    "text": text,
                    "rationale": rationale,
                    "origin": origin,
                    "state": state,
                }
            )
    return out


# ---------- hashing ----------


def canonical_json(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(content: dict[str, Any]) -> str:
    """SHA-256 of the canonical content: what exactly was approved can be checked later."""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


# ---------- approval validation ----------


@dataclass(frozen=True)
class Issue:
    field: str
    severity: str  # "error" blocks approval; "warning" is shown but does not
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "severity": self.severity, "message": self.message}


def approval_issues(c: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    if not c.get("objective"):
        issues.append(Issue("objective", "error", "State the review objective."))
    if c.get("framework") is None:
        issues.append(Issue("framework", "error", "Choose a question framework, or mark it as not applicable."))
    elif c["framework"] not in ("none",) and not any(e.get("value") for e in c.get("framework_elements", [])):
        issues.append(Issue("framework_elements", "warning", "No framework elements are filled in."))
    if not c.get("questions"):
        issues.append(Issue("questions", "error", "Add at least one review question."))
    crit = c.get("criteria", [])
    if not any(x["kind"] == "inclusion" and x["state"] == "accepted" for x in crit):
        issues.append(Issue("criteria", "error", "Accept or add at least one inclusion criterion."))
    pending = [x for x in crit if x["state"] == "proposed"]
    if pending:
        issues.append(
            Issue("criteria", "error", f"{len(pending)} suggested criterion(s) still need to be accepted or rejected.")
        )
    df, dt = c.get("date_from"), c.get("date_to")
    if df is not None and dt is not None and df > dt:
        issues.append(Issue("date_range", "error", "The start year is after the end year."))
    if df is None and dt is None:
        issues.append(Issue("date_range", "warning", "No date range: all publication years will be eligible."))
    if not c.get("sources"):
        issues.append(Issue("sources", "warning", "No sources are listed yet."))
    return issues


def blocking(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def effective_criteria(c: dict[str, Any]) -> list[dict[str, Any]]:
    """Criteria that actually apply: accepted ones. Rejected suggestions are kept for the record only."""
    return [x for x in c.get("criteria", []) if x["state"] == "accepted"]


# ---------- human-readable summary ----------


def _years(c: dict[str, Any]) -> str:
    df, dt = c.get("date_from"), c.get("date_to")
    if df is None and dt is None:
        return "any year"
    return f"{df if df is not None else 'earliest'} to {dt if dt is not None else 'latest'}"


def summary_lines(c: dict[str, Any]) -> list[str]:
    """Plain-text summary shown for approval. Built only from the stored content (nothing invented)."""
    fw = c.get("framework")
    lines = [f"Objective: {c.get('objective') or '(not stated)'}"]
    lines.append(f"Framework: {FRAMEWORKS[fw].label if fw else '(not chosen)'}")
    lines.extend(f"  {e['label']}: {e['value']}" for e in c.get("framework_elements", []) if e.get("value"))
    qs = c.get("questions", [])
    lines.append(f"Questions ({len(qs)}):")
    lines += [f"  {i + 1}. {q['text']}" for i, q in enumerate(qs)]
    eff = effective_criteria(c)
    for kind in CRITERION_KINDS:
        items = [x for x in eff if x["kind"] == kind]
        lines.append(f"{kind.capitalize()} criteria ({len(items)}):")
        lines += [f"  - {x['text']}" for x in items]
    lines.append(f"Publication years: {_years(c)}")
    for f in LIST_FIELDS:
        lines.append(f"{FIELD_LABELS[f]}: {', '.join(c.get(f) or []) or '(none listed)'}")
    for f in ("search_strategy", "extraction_plan", "synthesis_plan"):
        if c.get(f):
            lines.append(f"{FIELD_LABELS[f]}: {c[f]}")
    return lines


# ---------- diff between two versions ----------


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Field-level changes from `old` to `new`. Criteria are compared on their *effective* form (accepted only),
    so rejecting a suggestion that was already rejected is not a change, but un-accepting a criterion is."""
    changes: list[dict[str, Any]] = []

    def add(field: str, change: str, **extra: Any) -> None:
        changes.append({"field": field, "change": change, **extra})

    for f in SCALAR_FIELDS:
        if (old.get(f) or None) != (new.get(f) or None):
            add(f, "changed")
    if old.get("framework") != new.get("framework"):
        add("framework", "changed", before=old.get("framework"), after=new.get("framework"))
    if [(e["key"], e.get("value")) for e in old.get("framework_elements", [])] != [
        (e["key"], e.get("value")) for e in new.get("framework_elements", [])
    ]:
        add("framework_elements", "changed")
    if (old.get("date_from"), old.get("date_to")) != (new.get("date_from"), new.get("date_to")):
        add("date_range", "changed", before=_years(old), after=_years(new))
    for f in LIST_FIELDS:
        a = {x.lower() for x in old.get(f) or []}
        b = {x.lower() for x in new.get(f) or []}
        if a != b:
            add(f, "changed", added=len(b - a), removed=len(a - b))

    changes += _keyed_diff("questions", old.get("questions", []), new.get("questions", []), ("text",))
    changes += _keyed_diff("criteria", effective_criteria(old), effective_criteria(new), ("text", "kind"))
    return changes


def _keyed_diff(
    field: str, old: list[dict[str, Any]], new: list[dict[str, Any]], compare: tuple[str, ...]
) -> list[dict[str, Any]]:
    a = {x["stable_key"]: x for x in old}
    b = {x["stable_key"]: x for x in new}
    out: list[dict[str, Any]] = [{"field": field, "change": "added", "stable_key": k} for k in b.keys() - a.keys()]
    out += [{"field": field, "change": "removed", "stable_key": k} for k in a.keys() - b.keys()]
    out += [
        {"field": field, "change": "changed", "stable_key": k}
        for k in a.keys() & b.keys()
        if any(a[k].get(c) != b[k].get(c) for c in compare)
    ]
    if [x["stable_key"] for x in old if x["stable_key"] in b] != [x["stable_key"] for x in new if x["stable_key"] in a]:
        out.append({"field": field, "change": "reordered"})
    return sorted(out, key=lambda c: (c["change"], c.get("stable_key", "")))
