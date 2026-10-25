"""Deterministic comparison and synthesis rules. Stdlib only.

Inputs are APPROVED (reviewed) extraction values plus approved appraisals; nothing here calls a model.
  * group studies by a field's value (study design, population, method, outcome, ...) or by research question;
  * per field: agreement (>= 2 studies, one value), disagreement (>= 2 distinct values), single study, numeric range;
  * evidence gaps keep their reasons apart: not located in processed text / no full text / ambiguous / not reviewed;
  * methodological differences (designs, appraisal frameworks, overall appraisal) and limitations (serious overall
    appraisals, studies without an approved appraisal);
  * a statement's certainty is computed from its citations and can only be LOWERED by a person or a model, never
    raised above what the cited evidence supports.
No pooled estimate is ever computed: statistical meta-analysis is out of scope (see `META_ANALYSIS`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.appraisal.frameworks import is_serious
from app.extraction.extractor import numbers_in

CERTAINTY = ("insufficient", "conflicting", "uncertain", "supported")  # increasing confidence
RANK = {c: i for i, c in enumerate(CERTAINTY)}
KINDS = ("finding", "agreement", "disagreement", "gap", "limitation", "method_difference", "conclusion")
REVIEWED = ("approved", "edited")
POOLING = re.compile(
    r"\b(pooled|meta-?analy\w*|summary (effect|estimate)|overall effect size|forest plot|heterogeneity|I\s?\^?2\s?=)",
    re.I,
)
META_ANALYSIS = {
    "in_scope": False,
    "note": "Narrative synthesis only. No statistical pooling is performed by this application; a meta-analysis "
    "needs a separately specified, validated statistical feature (effect measures, model, heterogeneity, "
    "sensitivity analyses) and must not be produced by an AI model.",
}


@dataclass
class Cell:
    item_id: str
    field_key: str
    revision: int
    status: str
    value: Any
    excerpt: str | None
    page_no: int | None
    review_status: str


@dataclass
class Study:
    paper_id: str
    title: str
    year: int | None
    cells: dict[str, Cell] = field(default_factory=dict)
    appraisal: dict[str, Any] | None = None  # {record_id, revision, framework_key, overall}


def value_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, list):
        return "; ".join(value_text(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(value_text(x) for x in v.values())
    return str(v)


def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", value_text(v)).strip().lower()


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    nums = numbers_in(value_text(v))
    return float(next(iter(nums))) if len(nums) == 1 else None


def reviewed(cell: Cell | None) -> bool:
    return cell is not None and cell.review_status in REVIEWED


# ---------- grouping ----------


def group_studies(
    studies: list[Study], fields: list[dict[str, Any]], group_by: str | None, question_map: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Groups of paper ids. `group_by` is a field key, "question:<id>" for the fields mapped to a research question
    (all studies, a subset of fields), or None (one group). Multi-choice values put a study in several groups."""
    all_ids = [s.paper_id for s in studies]
    keys = [f["field_key"] for f in fields]
    if not group_by:
        return [{"key": "all", "label": "All included studies", "paper_ids": all_ids, "field_keys": keys}]
    if group_by.startswith("question:"):
        qid = group_by.split(":", 1)[1]
        mapped = [k for k in keys if qid in (question_map.get(k) or [])]
        return [{"key": group_by, "label": "Research question", "paper_ids": all_ids, "field_keys": mapped or keys}]
    groups: dict[str, dict[str, Any]] = {}
    for s in studies:
        c = s.cells.get(group_by)
        if reviewed(c) and c is not None and c.status == "found":
            vals = c.value if isinstance(c.value, list) else [c.value]
            labels = [value_text(v) for v in vals if value_text(v)]
        else:
            labels = []
        for lab in labels or ["Not reported or not reviewed"]:
            g = groups.setdefault(norm(lab) or lab, {"key": norm(lab) or lab, "label": lab, "paper_ids": []})
            if s.paper_id not in g["paper_ids"]:
                g["paper_ids"].append(s.paper_id)
    other = [k for k in keys if k != group_by]
    return sorted(
        ({**g, "field_keys": other} for g in groups.values()),
        key=lambda g: (g["label"] == "Not reported or not reviewed", -len(g["paper_ids"]), g["label"]),
    )


# ---------- per-field signals ----------


def field_signal(fd: dict[str, Any], studies: list[Study]) -> dict[str, Any]:
    key = fd["field_key"]
    found: list[tuple[Study, Cell]] = []
    gaps: dict[str, list[str]] = {"not_located": [], "unavailable": [], "ambiguous": [], "not_reviewed": []}
    for s in studies:
        c = s.cells.get(key)
        if c is None or not reviewed(c) or c.status == "pending":
            gaps["not_reviewed"].append(s.paper_id)
        elif c.status == "found":
            found.append((s, c))
        elif c.status in ("not_located", "unavailable", "ambiguous"):
            gaps[c.status].append(s.paper_id)
    values: dict[str, dict[str, Any]] = {}
    for s, c in found:
        v = values.setdefault(norm(c.value), {"value": value_text(c.value), "paper_ids": [], "item_ids": []})
        v["paper_ids"].append(s.paper_id)
        v["item_ids"].append(c.item_id)
    distinct = sorted(values.values(), key=lambda v: -len(v["paper_ids"]))
    if len(found) == 0:
        pattern = "no_evidence"
    elif len(found) == 1:
        pattern = "single_study"
    elif len(distinct) == 1:
        pattern = "agreement"
    else:
        pattern = "disagreement"
    out: dict[str, Any] = {
        "field_key": key,
        "label": fd.get("label") or key,
        "type": fd.get("type") or "text",
        "pattern": pattern,
        "studies_with_value": len(found),
        "values": distinct,
        "gaps": gaps,
    }
    if fd.get("type") == "number":
        nums = [n for n in (_num(c.value) for _, c in found) if n is not None]
        if nums:
            out["range"] = {"min": min(nums), "max": max(nums), "n": len(nums)}
    # Free-text values rarely match exactly: for text fields a "disagreement" means only "reported differently".
    if fd.get("type", "text") == "text" and pattern == "disagreement":
        out["pattern"] = "varied"
    return out


def method_differences(studies: list[Study], design_key: str | None) -> dict[str, Any]:
    designs: dict[str, list[str]] = {}
    if design_key:
        for s in studies:
            c = s.cells.get(design_key)
            lab = value_text(c.value) if reviewed(c) and c is not None and c.status == "found" else "Not reported"
            designs.setdefault(lab, []).append(s.paper_id)
    frameworks: dict[str, list[str]] = {}
    for s in studies:
        key = s.appraisal["framework_key"] if s.appraisal else "not appraised"
        frameworks.setdefault(key, []).append(s.paper_id)
    return {"designs": designs, "appraisal_frameworks": frameworks, "mixed_designs": len(designs) > 1}


def limitations(studies: list[Study]) -> dict[str, list[str]]:
    serious = [
        s.paper_id for s in studies if s.appraisal and is_serious(s.appraisal["framework_key"], s.appraisal["overall"])
    ]
    unappraised = [s.paper_id for s in studies if not s.appraisal]
    return {"serious_appraisal_concerns": serious, "not_appraised": unappraised}


# ---------- certainty ----------


def compute_certainty(
    text: str, cited: list[tuple[Study, Cell]], kind: str, text_fields: frozenset[str] = frozenset()
) -> tuple[str, list[str]]:
    """(certainty, reasons). Gap and limitation statements describe missing/weak evidence: their certainty describes
    the description itself and is 'supported' when every citation backs it."""
    reasons: list[str] = []
    if not cited:
        return "insufficient", ["No reviewed evidence is cited."]
    if kind in ("gap", "limitation", "method_difference"):
        return "supported", ["Describes the cited evidence base."]
    found = [(s, c) for s, c in cited if c.status == "found"]
    if not found:
        return "insufficient", ["None of the cited values was found in a study."]
    level = "supported"
    values_by_field: dict[str, set[str]] = {}
    for _, c in found:
        values_by_field.setdefault(c.field_key, set()).add(norm(c.value))
    # Free text is rarely worded identically, so only typed fields (number/choice/boolean/date) count as conflicting.
    if any(len(v) > 1 for k, v in values_by_field.items() if k not in text_fields):
        level = "conflicting"
        reasons.append("The cited studies report different values.")
    papers = {s.paper_id for s, _ in found}
    if len(papers) < 2:
        level = min(level, "uncertain", key=RANK.__getitem__)
        reasons.append("Based on a single study.")
    if any(c.status == "ambiguous" for _, c in cited):
        level = min(level, "uncertain", key=RANK.__getitem__)
        reasons.append("A cited value is ambiguous in its source.")
    weak = [s for s, _ in found if not s.appraisal or is_serious(s.appraisal["framework_key"], s.appraisal["overall"])]
    if weak:
        level = min(level, "uncertain", key=RANK.__getitem__)
        reasons.append(f"{len({s.paper_id for s in weak})} cited study(ies) not appraised or with serious concerns.")
    cited_numbers: set[str] = set()
    for _, c in cited:
        cited_numbers |= numbers_in(value_text(c.value)) | numbers_in(c.excerpt or "")
    missing = sorted(numbers_in(text) - cited_numbers)
    if missing:
        level = "uncertain"
        reasons.append(f"Number(s) {', '.join(missing)} in the text are not in the cited evidence.")
    if level == "supported":
        reasons.append(f"Consistent reviewed evidence from {len(papers)} appraised studies.")
    return level, reasons


def cap(claimed: str | None, computed: str) -> str:
    """A person or model may be more cautious than the rules, never more confident."""
    if claimed not in RANK:
        return computed
    return claimed if RANK[claimed] <= RANK[computed] else computed


# ---------- draft statements without AI ----------


def system_statements(
    fields: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    studies: list[Study],
    design_key: str | None,
) -> list[dict[str, Any]]:
    """Plain-language statements derived only from the signals; each cites the evidence items it describes."""
    by_id = {s.paper_id: s for s in studies}
    fdefs = {f["field_key"]: f for f in fields}
    out: list[dict[str, Any]] = []
    for g in groups:
        members = [by_id[p] for p in g["paper_ids"] if p in by_id]
        prefix = "" if g["key"] == "all" else f"Among studies grouped as '{g['label']}': "
        for key in g["field_keys"]:
            sig = field_signal(fdefs[key], members)
            label = sig["label"]
            cite_found = [i for v in sig["values"] for i in v["item_ids"]]
            if sig["pattern"] == "agreement":
                out.append(
                    _stmt(
                        "agreement",
                        f"{prefix}All {sig['studies_with_value']} studies reporting {label} "
                        f"report '{sig['values'][0]['value']}'.",
                        cite_found,
                        g,
                    )
                )
            elif sig["pattern"] in ("disagreement", "varied"):
                parts = "; ".join(f"'{v['value'][:80]}' ({len(v['paper_ids'])})" for v in sig["values"][:6])
                kind = "disagreement" if sig["pattern"] == "disagreement" else "finding"
                verb = "differ on" if kind == "disagreement" else "describe differently"
                out.append(_stmt(kind, f"{prefix}Studies {verb} {label}: {parts}.", cite_found, g))
            elif sig["pattern"] == "single_study":
                out.append(
                    _stmt(
                        "finding",
                        f"{prefix}Only one study reports {label}: '{sig['values'][0]['value'][:120]}'.",
                        cite_found,
                        g,
                    )
                )
            gaps = sig["gaps"]
            gap_items = [
                s.cells[key].item_id
                for s in members
                if key in s.cells
                and s.cells[key].status in ("not_located", "unavailable", "ambiguous")
                and reviewed(s.cells[key])
            ]
            if gap_items:
                bits = [
                    f"not found in the processed text of {len(gaps['not_located'])}" if gaps["not_located"] else "",
                    f"no full text for {len(gaps['unavailable'])}" if gaps["unavailable"] else "",
                    f"ambiguous in {len(gaps['ambiguous'])}" if gaps["ambiguous"] else "",
                ]
                out.append(
                    _stmt(
                        "gap",
                        f"{prefix}Evidence gap for {label}: "
                        f"{', '.join(b for b in bits if b)} study(ies). This is missing evidence, not "
                        f"evidence of absence.",
                        gap_items,
                        g,
                    )
                )
    lim = limitations(studies)
    if lim["serious_appraisal_concerns"]:
        items = _any_items(studies, lim["serious_appraisal_concerns"])
        out.append(
            _stmt(
                "limitation",
                f"{len(lim['serious_appraisal_concerns'])} of {len(studies)} studies have "
                "serious concerns in their approved appraisal; findings relying on them are uncertain.",
                items,
                None,
            )
        )
    md = method_differences(studies, design_key)
    if md["mixed_designs"]:
        desc = "; ".join(f"{k} ({len(v)})" for k, v in md["designs"].items())
        out.append(
            _stmt(
                "method_difference",
                f"The included studies use different designs: {desc}.",
                _any_items(studies, [p for v in md["designs"].values() for p in v], design_key),
                None,
            )
        )
    return out


def _any_items(studies: list[Study], paper_ids: list[str], prefer: str | None = None) -> list[str]:
    out = []
    for s in studies:
        if s.paper_id in paper_ids:
            cell = s.cells.get(prefer) if prefer else None
            cell = cell or next((c for c in s.cells.values() if reviewed(c)), None)
            if cell and reviewed(cell):
                out.append(cell.item_id)
    return out


def _stmt(kind: str, text: str, item_ids: list[str], group: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "item_ids": item_ids,
        "group": group["label"] if group else None,
        "origin": "system",
    }
