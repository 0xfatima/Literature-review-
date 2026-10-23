"""Review stages and which stages a protocol change invalidates. Stdlib only.

The stage list is a *conceptual* order used to answer "how far back must we go". The workflow itself branches,
pauses and loops; it is not a single linear sequence.
"""

from __future__ import annotations

from typing import Any

STAGES = (
    "DRAFT",
    "PROTOCOL_REVIEW",
    "SEARCH",
    "RECORD_SCREENING",
    "FULL_TEXT_SCREENING",
    "EXTRACTION",
    "SYNTHESIS",
    "REPORT",
)
STAGE_LABELS = {
    "DRAFT": "Protocol draft",
    "PROTOCOL_REVIEW": "Protocol review",
    "SEARCH": "Search",
    "RECORD_SCREENING": "Record screening",
    "FULL_TEXT_SCREENING": "Full-text screening",
    "EXTRACTION": "Extraction",
    "SYNTHESIS": "Synthesis",
    "REPORT": "Report",
}
DOWNSTREAM = STAGES[STAGES.index("SEARCH") :]  # stages that consume an approved protocol

# Earliest stage each protocol field feeds. Everything after it may also change (a different search finds
# different records, which changes screening, extraction and synthesis), so impact runs to the end.
FIELD_EARLIEST_STAGE = {
    "framework": "SEARCH",
    "framework_elements": "SEARCH",
    "date_range": "SEARCH",
    "languages": "SEARCH",
    "study_designs": "SEARCH",
    "sources": "SEARCH",
    "search_strategy": "SEARCH",
    "criteria": "RECORD_SCREENING",
    "questions": "EXTRACTION",
    "extraction_plan": "EXTRACTION",
    "objective": "SYNTHESIS",
    "synthesis_plan": "SYNTHESIS",
}
WHY = {
    "SEARCH": "queries are built from the framework, scope and sources",
    "RECORD_SCREENING": "records are screened against the eligibility criteria",
    "EXTRACTION": "extraction fields follow the review questions and extraction plan",
    "SYNTHESIS": "synthesis is framed by the objective and synthesis plan",
}


def index(stage: str) -> int:
    return STAGES.index(stage)


def impact(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Which downstream stages may need to be rerun, and why. Reordering alone affects nothing."""
    material = [c for c in changes if c["change"] != "reordered"]
    reasons: dict[str, list[str]] = {}
    for c in material:
        stage = FIELD_EARLIEST_STAGE.get(c["field"])
        if stage:
            reasons.setdefault(stage, [])
            if c["field"] not in reasons[stage]:
                reasons[stage].append(c["field"])
    if not reasons:
        return {"earliest_stage": None, "affected_stages": [], "triggers": {}, "material_change_count": 0}
    earliest = min(reasons, key=index)
    affected = [s for s in DOWNSTREAM if index(s) >= index(earliest)]
    return {
        "earliest_stage": earliest,
        "affected_stages": affected,
        "triggers": {s: {"fields": f, "why": WHY[s]} for s, f in sorted(reasons.items(), key=lambda kv: index(kv[0]))},
        "material_change_count": len(material),
    }


def backtrack_target(current: str, earliest_affected: str | None) -> str:
    """Stage to return to after a revision is approved: never forward, never past the earliest affected stage."""
    if earliest_affected is None:
        return current
    return STAGES[min(index(current), index(earliest_affected))]
