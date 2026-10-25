"""Validation of a report draft. Stdlib only; deterministic.

Checks every block of every section and returns issues with a severity:
  error    unknown citation key; reference whose paper record no longer exists; a count in the text that contradicts
           the stored PRISMA flow; PRISMA flow violations
  warning  statement missing a citation for its evidence; citation to a paper that is not among the claim's evidence;
           evidence changed since it was cited; a number in an evidence-linked claim that is not in the evidence;
           unsupported claim (edited text in a results/discussion/conclusion section without any citation);
           conclusion resting on uncertain/conflicting/insufficient evidence; placeholder text; empty required
           section; retracted reference
The summary never states that a draft is publication-ready: `publication_ready` is always False, and the label
says what human verification is still needed.
"""

from __future__ import annotations

import re
from typing import Any

from app.extraction.extractor import numbers_in

CITE = re.compile(r"\[@([A-Za-z0-9_.:-]+)\]")
PLACEHOLDER = re.compile(r"\[\[[^\]]*\]\]")
CLAIM_SECTIONS = frozenset({"title_abstract", "results", "synthesis", "discussion", "conclusion"})
LOW_CERTAINTY = frozenset({"uncertain", "conflicting", "insufficient"})

# "<number> <phrase>" patterns whose number must equal a stored PRISMA count.
_N = r"(\d{1,3}(?:,\d{3})+|\d+)"
FACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("identified", re.compile(_N + r"\s+(?:records?|citations?)\s+(?:were\s+)?identified", re.I)),
    ("removed_duplicates", re.compile(_N + r"\s+duplicates?\b", re.I)),
    ("screened", re.compile(_N + r"\s+(?:records?\s+)?(?:were\s+)?screened", re.I)),
    ("ta_excluded", re.compile(_N + r"\s+(?:records?\s+)?(?:were\s+)?excluded\s+(?:at|during)\s+title", re.I)),
    ("sought_retrieval", re.compile(_N + r"\s+reports?\s+(?:were\s+)?sought", re.I)),
    ("not_retrieved", re.compile(_N + r"\s+(?:reports?\s+)?(?:were\s+|could\s+)?not\s+(?:be\s+)?retrieved", re.I)),
    ("ft_assessed", re.compile(_N + r"\s+(?:full[- ]text\s+)?(?:reports?|articles?)\s+(?:were\s+)?assessed", re.I)),
    ("included_studies", re.compile(_N + r"\s+(?:studies|study)\s+(?:were\s+|was\s+)?included", re.I)),
    ("included_reports", re.compile(_N + r"\s+reports?\s+(?:were\s+|was\s+)?included", re.I)),
]


def strip_markers(text: str) -> str:
    return CITE.sub("", text)


def cite_keys(text: str) -> list[str]:
    return list(dict.fromkeys(CITE.findall(text or "")))


def check_numbers(text: str, facts: dict[str, int]) -> list[dict[str, Any]]:
    """Counts stated in the text that contradict the stored facts."""
    out = []
    clean = strip_markers(text)
    for key, pat in FACT_PATTERNS:
        if key not in facts:
            continue
        for m in pat.finditer(clean):
            n = int(m.group(1).replace(",", ""))
            if n != facts[key]:
                out.append({"fact": key, "stated": n, "recorded": facts[key], "phrase": m.group(0)[:80]})
    return out


def _issue(code: str, severity: str, section: str, block: str | None, message: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "section": section, "block_id": block, "message": message}


def _substantive(block: dict[str, Any]) -> bool:
    if block.get("type") == "table":
        return bool(block.get("table", {}).get("rows"))
    if block.get("type") == "references":
        return True
    text = PLACEHOLDER.sub("", block.get("text") or "").strip()
    return len(text) > 0


def validate(
    sections: list[dict[str, Any]],
    references: list[dict[str, Any]],
    facts: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    papers: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """`evidence`: item_id -> {revision, reviewed, numbers}; `papers`: paper_id -> {exists, retracted}."""
    issues: list[dict[str, Any]] = []
    ref_by_key = {r["key"]: r for r in references}
    numeric_facts = {k: v for k, v in facts.items() if isinstance(v, int) and not isinstance(v, bool)}
    fact_numbers = {str(v) for v in numeric_facts.values()}
    for r in references:
        p = papers.get(str(r["paper_id"]))
        if p is None or not p.get("exists"):
            issues.append(
                _issue(
                    "reference_not_stored",
                    "error",
                    "references",
                    None,
                    f"Reference [{r['key']}] no longer matches a stored paper record.",
                )
            )
        elif p.get("retracted"):
            issues.append(
                _issue(
                    "retracted_reference",
                    "warning",
                    "references",
                    None,
                    f"Reference [{r['key']}] is flagged as retracted by a source.",
                )
            )
    issues.extend(
        _issue(
            "prisma_inconsistent",
            "error",
            "results",
            None,
            f"PRISMA flow check failed: {v.get('message') or v.get('rule') or v}",
        )
        for v in facts.get("violations") or []
    )
    if facts.get("pending"):
        issues.append(
            _issue(
                "prisma_not_final",
                "warning",
                "results",
                None,
                f"The PRISMA flow is not final: {facts['pending']} record(s) still await a decision.",
            )
        )
    verified = 0
    for sec in sections:
        skey = sec["key"]
        if sec.get("verified_by"):
            verified += 1
        blocks = sec.get("blocks") or []
        if sec.get("required") and not any(_substantive(b) for b in blocks):
            issues.append(_issue("incomplete_section", "warning", skey, None, f"Section '{sec['title']}' is empty."))
        for b in blocks:
            if b.get("type") not in ("paragraph", "statement"):
                if b.get("type") == "table" and sec.get("required") and not b.get("table", {}).get("rows"):
                    issues.append(
                        _issue(
                            "empty_table",
                            "warning",
                            skey,
                            b.get("id"),
                            f"Table '{b['table'].get('caption')}' has no rows yet.",
                        )
                    )
                continue
            text = b.get("text") or ""
            bid = b.get("id")
            if PLACEHOLDER.search(text):
                issues.append(
                    _issue("placeholder", "warning", skey, bid, "Placeholder text must be written or removed.")
                )
            keys = cite_keys(text)
            issues.extend(
                _issue("unknown_citation", "error", skey, bid, f"Citation [@{k}] does not match any stored reference.")
                for k in keys
                if k not in ref_by_key
            )
            cited_papers = {str(ref_by_key[k]["paper_id"]) for k in keys if k in ref_by_key}
            issues.extend(
                _issue(
                    "inconsistent_number",
                    "error",
                    skey,
                    bid,
                    f"'{mm['phrase']}' states {mm['stated']}, but the recorded count is {mm['recorded']}.",
                )
                for mm in check_numbers(text, numeric_facts)
            )
            ev = b.get("evidence") or []
            if ev:
                ev_papers = {str(e["paper_id"]) for e in ev}
                missing = ev_papers - cited_papers
                if missing:
                    issues.append(
                        _issue(
                            "missing_citation",
                            "warning",
                            skey,
                            bid,
                            f"{len(missing)} study(ies) supporting this statement are not cited.",
                        )
                    )
                extra = cited_papers - ev_papers
                if extra:
                    issues.append(
                        _issue(
                            "citation_not_supporting",
                            "warning",
                            skey,
                            bid,
                            f"{len(extra)} cited reference(s) are not among this statement's evidence.",
                        )
                    )
                ev_numbers: set[str] = set()
                for e in ev:
                    cur = evidence.get(str(e["item_id"]))
                    if cur is None or not cur.get("reviewed") or cur.get("revision") != e.get("revision"):
                        issues.append(
                            _issue(
                                "stale_evidence",
                                "warning",
                                skey,
                                bid,
                                f"Evidence for {e.get('field_key', 'a field')} changed or is no longer "
                                "reviewed since this draft was generated.",
                            )
                        )
                    if cur is not None:
                        ev_numbers |= set(cur.get("numbers") or [])
                numeric_text = re.sub(r"\(\d+\)", "", strip_markers(text))
                unverified = sorted(numbers_in(numeric_text) - ev_numbers - fact_numbers)
                if unverified:
                    issues.append(
                        _issue(
                            "unverified_number",
                            "warning",
                            skey,
                            bid,
                            f"Number(s) {', '.join(unverified)} are not in the cited evidence.",
                        )
                    )
            elif (
                skey in CLAIM_SECTIONS
                and b.get("type") == "paragraph"
                and b.get("origin") == "edited"
                and not keys
                and PLACEHOLDER.sub("", text).strip()
                and not str(b.get("source") or "").startswith("data:")
            ):
                issues.append(
                    _issue(
                        "unsupported_statement", "warning", skey, bid, "This claim has no citation to a stored record."
                    )
                )
            if skey in ("conclusion", "title_abstract") and b.get("certainty") in LOW_CERTAINTY:
                issues.append(
                    _issue(
                        "uncertain_conclusion",
                        "warning",
                        skey,
                        bid,
                        f"Rests on {b['certainty']} evidence; the wording must say so.",
                    )
                )
    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    total = len(sections)
    if errors:
        label = "Draft with unresolved errors — not verified"
    elif verified < total:
        label = "Unverified draft — every section needs human verification"
    elif warnings:
        label = "Human-checked draft with open warnings — not publication-ready"
    else:
        label = "Human-checked draft — publication still requires author sign-off and peer review"
    return {
        "issues": issues,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "sections_total": total,
            "sections_verified": verified,
            "label": label,
            "publication_ready": False,
        },
    }
