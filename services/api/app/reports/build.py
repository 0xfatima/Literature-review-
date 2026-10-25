"""Build a report draft from the STORED review data. No model is involved: methods come from the approved protocol
and the recorded search runs, counts from the PRISMA flow computed from record events, tables from reviewed
evidence and approved appraisals, synthesis text from the accepted synthesis version. Anything that must be written
by the authors is a visible [[placeholder]] that validation reports as incomplete."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.appraisal import frameworks as fwm
from app.db.models import (
    AppraisalRecord,
    EvidenceItem,
    ExtractionSchema,
    PaperRecord,
    Project,
    ProtocolVersion,
    ScreeningDecision,
    ScreeningState,
    SearchQuery,
    SearchRun,
    SynthesisVersion,
)
from app.extraction import schema_service
from app.extraction.extractor import numbers_in
from app.prisma import service as prisma_service
from app.services import protocol_service
from app.synthesis import service as synthesis_service
from app.synthesis.signals import value_text

STATEMENT_SECTION = {
    "finding": "synthesis",
    "agreement": "synthesis",
    "disagreement": "synthesis",
    "gap": "discussion",
    "limitation": "discussion",
    "method_difference": "discussion",
    "conclusion": "conclusion",
}
SECTIONS = [
    ("title_abstract", "Title and abstract", True),
    ("introduction", "Introduction and review objective", True),
    ("methods", "Methods and search strategy", True),
    ("eligibility", "Eligibility criteria", True),
    ("results", "Results and study characteristics", True),
    ("synthesis", "Evidence synthesis", True),
    ("discussion", "Limitations and discussion", True),
    ("conclusion", "Conclusion", True),
    ("references", "References", True),
    ("supplementary", "Supplementary material", False),
]


def _bid() -> str:
    return "b-" + uuid.uuid4().hex[:12]


def para(text: str, source: str, **kw: Any) -> dict[str, Any]:
    return {"id": _bid(), "type": "paragraph", "text": text, "source": source, "origin": "generated", **kw}


def table(key: str, caption: str, columns: list[str], rows: list[list[Any]], source: str) -> dict[str, Any]:
    return {
        "id": _bid(),
        "type": "table",
        "source": source,
        "table": {"key": key, "caption": caption, "columns": columns, "rows": rows},
    }


def placeholder(what: str) -> dict[str, Any]:
    return para(f"[[To be written by the authors: {what}]]", "placeholder")


# ---------- references ----------


def _family(p: PaperRecord) -> str:
    a = (p.authors or [{}])[0] if p.authors else {}
    name = str(a.get("family") or a.get("name") or "").strip() if isinstance(a, dict) else ""
    fam = name.split()[-1] if name and not a.get("family") else name
    return re.sub(r"[^a-z0-9]", "", fam.lower()) or "anon"


def format_reference(p: PaperRecord) -> str:
    names = [str(a.get("name") or a.get("family") or "") for a in (p.authors or []) if isinstance(a, dict)]
    names = [n for n in names if n]
    authors = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "") if names else "Anonymous"
    bits = [f"{authors} ({p.year or 'n.d.'}). {p.title.rstrip('.')}."]
    if p.venue:
        bits.append(f"{p.venue}.")
    if p.doi:
        bits.append(f"doi:{p.doi}")
    elif p.pmid:
        bits.append(f"PMID:{p.pmid}")
    if p.is_retracted:
        bits.append("[Flagged as retracted]")
    return " ".join(bits)


def build_references(papers: list[PaperRecord]) -> list[dict[str, Any]]:
    ordered = sorted(papers, key=lambda p: (_family(p), p.year or 0, p.title.lower(), str(p.id)))
    seen: dict[str, int] = {}
    out = []
    for p in ordered:
        base = f"{_family(p)}{p.year or 'nd'}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        key = base if n == 0 else f"{base}{chr(ord('a') + n - 1) if n <= 26 else n}"
        out.append(
            {
                "key": key,
                "paper_id": str(p.id),
                "formatted": format_reference(p),
                "doi": p.doi,
                "pmid": p.pmid,
                "year": p.year,
                "title": p.title,
            }
        )
    return out


# ---------- review state ----------


def _accepted_synthesis(db: Session, project: Project) -> SynthesisVersion | None:
    return db.scalar(
        select(SynthesisVersion)
        .where(SynthesisVersion.project_id == project.id, SynthesisVersion.status == "accepted")
        .order_by(SynthesisVersion.version.desc())
        .limit(1)
    )


def facts_from_flow(flow: dict[str, Any]) -> dict[str, Any]:
    return {
        "identified": int(flow["identified_a"]) + int(flow["identified_b"]),
        "removed_duplicates": int(flow["removed_duplicates"]),
        "screened": int(flow["screened"]),
        "ta_excluded": int(flow["ta_excluded"]),
        "sought_retrieval": int(flow["sought_retrieval"]),
        "not_retrieved": int(flow["not_retrieved"]),
        "ft_assessed": int(flow["ft_assessed"]),
        "ft_excluded": int(flow["ft_excluded"]),
        "included_studies": int(flow["included_studies"]),
        "included_reports": int(flow["included_reports"]),
        "pending": int(flow["pending_ta"]) + int(flow["pending_retrieval"]) + int(flow["pending_ft"]),
        "violations": list(flow.get("violations") or []),
    }


def review_state(db: Session, project: Project) -> dict[str, Any]:
    """What a draft is built from, with a fingerprint that changes whenever any of it changes."""
    pv = protocol_service.approved(db, project)
    schema = schema_service.current_schema(db, project)
    syn = _accepted_synthesis(db, project)
    flow = prisma_service.flow_view(prisma_service.get_flow(db, project.id))
    runs = list(
        db.execute(
            select(SearchRun.id, SearchRun.status, SearchRun.records_retrieved, SearchRun.finished_at)
            .where(SearchRun.project_id == project.id)
            .order_by(SearchRun.created_at)
        ).all()
    )
    ev = (
        db.execute(
            select(
                EvidenceItem.review_status,
                func.count(),
                func.max(EvidenceItem.row_version),
                func.max(EvidenceItem.updated_at),
            )
            .where(EvidenceItem.extraction_schema_id == schema.id)
            .group_by(EvidenceItem.review_status)
        ).all()
        if schema
        else []
    )
    ap = db.execute(
        select(AppraisalRecord.status, func.count(), func.max(AppraisalRecord.updated_at))
        .where(AppraisalRecord.project_id == project.id)
        .group_by(AppraisalRecord.status)
    ).all()
    state: dict[str, Any] = {
        "protocol_version": (
            {
                "id": str(pv.id),
                "version": pv.version,
                "content_hash": pv.content_hash,
                "approved_at": pv.approved_at.isoformat() if pv.approved_at else None,
            }
            if pv
            else None
        ),
        "extraction_schema": {"id": str(schema.id), "version": schema.version} if schema else None,
        "synthesis_version": (
            {
                "id": str(syn.id),
                "version": syn.version,
                "accepted_at": syn.accepted_at.isoformat() if syn.accepted_at else None,
                "row_version": syn.row_version,
            }
            if syn
            else None
        ),
        "prisma": facts_from_flow(flow),
        "search_runs": [
            {
                "id": str(r.id),
                "status": r.status,
                "records": r.records_retrieved,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
        "evidence": {
            str(s): {"count": n, "max_row_version": mv, "updated": u.isoformat() if u else None} for s, n, mv, u in ev
        },
        "appraisals": {str(s): {"count": n, "updated": u.isoformat() if u else None} for s, n, u in ap},
        "included_papers": len(schema_service.included_paper_ids(db, project)),
    }
    state["fingerprint"] = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    return state


def evidence_index(db: Session, schema_id: uuid.UUID | None) -> dict[str, dict[str, Any]]:
    if schema_id is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for it in db.scalars(select(EvidenceItem).where(EvidenceItem.extraction_schema_id == schema_id)):
        out[str(it.id)] = {
            "revision": it.row_version,
            "reviewed": it.review_status in ("approved", "edited"),
            "numbers": sorted(numbers_in(value_text(it.value)) | numbers_in(it.excerpt or "")),
        }
    return out


def paper_index(db: Session, paper_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [uuid.UUID(p) for p in paper_ids]
    found = (
        {
            str(i): r
            for i, r in db.execute(
                select(PaperRecord.id, PaperRecord.is_retracted).where(PaperRecord.id.in_(ids))
            ).all()
        }
        if ids
        else {}
    )
    return {p: {"exists": p in found, "retracted": bool(found.get(p))} for p in paper_ids}


# ---------- sections ----------


def _protocol_blocks(pv: ProtocolVersion) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    c = protocol_service.to_content(pv)
    return c, [
        para(
            f"The review followed protocol version {pv.version}, approved on "
            f"{pv.approved_at.date().isoformat() if pv.approved_at else 'an unrecorded date'}"
            f"{f' (content hash {pv.content_hash[:12]})' if pv.content_hash else ''}.",
            "data:protocol",
        )
    ]


def _search_rows(db: Session, project: Project) -> tuple[list[list[Any]], list[list[Any]]]:
    rows = db.execute(
        select(SearchRun, SearchQuery)
        .join(SearchQuery, SearchQuery.search_run_id == SearchRun.id)
        .where(SearchRun.project_id == project.id)
        .order_by(SearchRun.created_at, SearchQuery.position)
    ).all()
    history, strategies = [], []
    for run, q in rows:
        when = q.retrieved_at or q.finished_at or run.finished_at or run.created_at
        history.append(
            [
                when.date().isoformat() if when else "",
                q.provider,
                q.status,
                q.total_reported if q.total_reported is not None else "",
                q.records_retrieved,
            ]
        )
        strategies.append(
            [
                when.date().isoformat() if when else "",
                q.provider,
                q.query_text,
                json.dumps(q.filters_applied or {}, sort_keys=True),
            ]
        )
    return history, strategies


def _exclusion_log(db: Session, project: Project) -> list[list[Any]]:
    rows = db.execute(
        select(ScreeningState, ScreeningDecision, PaperRecord.title)
        .join(ScreeningDecision, ScreeningDecision.id == ScreeningState.decision_id)
        .join(PaperRecord, PaperRecord.id == ScreeningState.paper_id)
        .where(ScreeningState.project_id == project.id, ScreeningState.decision == "exclude")
        .order_by(ScreeningState.stage, PaperRecord.title)
    ).all()
    return [
        [
            st.stage.replace("_", " "),
            title,
            d.reason_label or d.reason_code or "Not recorded",
            d.created_at.date().isoformat(),
            str(st.paper_id),
        ]
        for st, d, title in rows
    ]


def build(db: Session, project: Project) -> dict[str, Any]:
    pv = protocol_service.approved(db, project)
    if pv is None:
        raise ValueError("protocol_not_approved")
    state = review_state(db, project)
    facts = state["prisma"]
    content, proto_blocks = _protocol_blocks(pv)
    schema: ExtractionSchema | None = schema_service.current_schema(db, project)
    syn = _accepted_synthesis(db, project)
    included = schema_service.included_paper_ids(db, project)
    papers = list(db.scalars(select(PaperRecord).where(PaperRecord.id.in_(included))))
    refs = build_references(papers)
    key_of = {r["paper_id"]: r["key"] for r in refs}
    studies = synthesis_service.load_studies(db, project, schema) if schema else []
    fields = list(schema.field_definitions or []) if schema else []
    history, strategies = _search_rows(db, project)
    sources = ", ".join(content.get("sources") or []) or "the configured scholarly sources"
    dates = (
        f"{content.get('date_from') or 'earliest'}–{content.get('date_to') or 'latest'}"
        if content.get("date_from") or content.get("date_to")
        else "no date limits"
    )
    objective = (content.get("objective") or "").strip()
    sec: dict[str, list[dict[str, Any]]] = {k: [] for k, _, _ in SECTIONS}

    # abstract
    sec["title_abstract"] += [
        para(f"Objective: {objective or '[[state the review objective]]'}", "data:protocol.objective"),
        para(
            f"Methods: We searched {sources} ({dates}) and screened records against predefined eligibility "
            "criteria at title/abstract and full-text stages.",
            "data:protocol",
        ),
        para(
            f"Results: {facts['identified']} records were identified, {facts['screened']} records were screened and "
            f"{facts['included_studies']} studies were included.",
            "data:prisma",
        ),
        placeholder("conclusions of the abstract, consistent with the accepted synthesis and its certainty"),
    ]
    # introduction
    sec["introduction"].append(placeholder("background and rationale for the review"))
    if objective:
        sec["introduction"].append(
            para(f"This review aims to {objective[0].lower() + objective[1:]}", "data:protocol.objective")
        )
    for i, q in enumerate(content.get("questions") or [], start=1):
        sec["introduction"].append(para(f"Review question {i}: {q['text']}", "data:protocol.questions"))
    # methods
    sec["methods"] += proto_blocks
    fw_bits = [f"{e['label']}: {e['value']}" for e in content.get("framework_elements") or [] if e.get("value")]
    if content.get("framework") and content["framework"] != "none":
        sec["methods"].append(
            para(
                f"The question was structured with the {str(content['framework']).upper()} "
                f"framework ({'; '.join(fw_bits) or 'elements not recorded'}).",
                "data:protocol",
            )
        )
    sec["methods"].append(
        para(
            f"Information sources: {sources}. Date range: {dates}. Languages: "
            f"{', '.join(content.get('languages') or []) or 'not restricted'}.",
            "data:protocol",
        )
    )
    sec["methods"].append(
        para(f"Search strategy: {content['search_strategy']}", "data:protocol.search_strategy")
        if content.get("search_strategy")
        else placeholder("search strategy narrative (the executed queries are listed in the supplementary material)")
    )
    sec["methods"].append(
        table(
            "search_history",
            "Searches executed (recorded by the application)",
            ["Date", "Source", "Status", "Results reported", "Records retrieved"],
            history,
            "data:search_queries",
        )
    )
    sec["methods"].append(
        para(
            "Records were screened at title/abstract and full-text stages against the eligibility criteria; each "
            "decision was recorded with the reviewer, time and reason. AI relevance scores, where used, were advisory "
            "and every inclusion or exclusion was a human decision.",
            "data:process",
        )
    )
    if schema:
        sec["methods"].append(
            para(
                f"Data were extracted with extraction schema version {schema.version} ({len(fields)} fields). "
                "Only values a reviewer approved or entered, each linked to a page of the source document "
                "where available, are "
                "reported.",
                "data:extraction_schema",
            )
        )
        sec["methods"].append(
            table(
                "extraction_fields",
                "Extraction fields",
                ["Field", "Type", "Options"],
                [
                    [f.get("label") or f["field_key"], f.get("type") or "text", "; ".join(f.get("options") or [])]
                    for f in fields
                ],
                "data:extraction_schema",
            )
        )
    used_fw = sorted({s.appraisal["framework_key"] for s in studies if s.appraisal})
    if used_fw:
        names = "; ".join(
            f"{fwm.FRAMEWORKS[k].label} ({fwm.FRAMEWORKS[k].based_on})" for k in used_fw if k in fwm.FRAMEWORKS
        )
        sec["methods"].append(
            para(
                f"Studies were critically appraised with design-specific frameworks: {names}. "
                "No summary quality score was computed.",
                "data:appraisal",
            )
        )
    sec["methods"].append(
        para(
            f"Synthesis: {content.get('synthesis_plan') or 'narrative synthesis'}. "
            "Findings were synthesised narratively; no statistical meta-analysis was performed.",
            "data:protocol.synthesis_plan",
        )
    )
    # eligibility
    crit = [c for c in content.get("criteria") or [] if c.get("state", "accepted") == "accepted"]
    sec["eligibility"].append(
        table(
            "criteria",
            "Eligibility criteria (approved protocol)",
            ["Type", "Criterion", "Rationale"],
            [[c["kind"], c["text"], c.get("rationale") or ""] for c in crit],
            "data:protocol.criteria",
        )
    )
    if content.get("study_designs"):
        sec["eligibility"].append(
            para(f"Eligible study designs: {', '.join(content['study_designs'])}.", "data:protocol")
        )
    # results
    flow_v = prisma_service.flow_view(prisma_service.get_flow(db, project.id))
    by_src = "; ".join(f"{k}: {v}" for k, v in sorted((flow_v["identified_a_by_source"] or {}).items()))
    sec["results"].append(
        para(
            f"{facts['identified']} records were identified{f' ({by_src})' if by_src else ''}. "
            f"{facts['removed_duplicates']} duplicates were removed. {facts['screened']} records were screened and "
            f"{facts['ta_excluded']} records were excluded at title/abstract screening. {facts['sought_retrieval']} "
            f"reports were sought for retrieval and {facts['not_retrieved']} reports could not be retrieved. "
            f"{facts['ft_assessed']} full-text reports were assessed and {facts['ft_excluded']} were excluded. "
            f"{facts['included_studies']} studies were included, reported in {facts['included_reports']} report(s).",
            "data:prisma",
        )
    )
    sec["results"].append(
        table(
            "prisma_flow",
            "PRISMA 2020 flow counts (computed from recorded events)",
            ["Stage", "Count"],
            [
                ["Records identified", facts["identified"]],
                ["Duplicates removed", facts["removed_duplicates"]],
                ["Records screened", facts["screened"]],
                ["Excluded at title/abstract", facts["ta_excluded"]],
                ["Reports sought for retrieval", facts["sought_retrieval"]],
                ["Reports not retrieved", facts["not_retrieved"]],
                ["Full-text reports assessed", facts["ft_assessed"]],
                ["Excluded at full text", facts["ft_excluded"]],
                ["Studies included", facts["included_studies"]],
                ["Reports included", facts["included_reports"]],
            ],
            "data:prisma",
        )
    )
    reasons = {o["code"]: o["label"] for o in prisma_service.ft_reason_options(pv)}
    ft_by = flow_v["ft_excluded_by_reason"] or {}
    sec["results"].append(
        table(
            "ft_exclusion_reasons",
            "Full-text exclusions by reason",
            ["Reason", "Reports"],
            [[reasons.get(k, k), v] for k, v in sorted(ft_by.items())],
            "data:prisma",
        )
    )
    char_fields = fields[:6]
    char_rows = []
    for s in studies:
        row: list[Any] = [f"[{key_of.get(s.paper_id, '?')}]", s.year or ""]
        for f in char_fields:
            c = s.cells.get(f["field_key"])
            if c is None or c.review_status not in ("approved", "edited"):
                row.append("Not reviewed")
            elif c.status == "found":
                row.append(value_text(c.value) + (f" (p. {c.page_no})" if c.page_no else ""))
            else:
                row.append(
                    {
                        "not_located": "Not reported in processed text",
                        "unavailable": "No full text",
                        "ambiguous": "Ambiguous",
                        "not_applicable": "N/A",
                    }.get(c.status, c.status)
                )
        char_rows.append(row)
    sec["results"].append(
        table(
            "study_characteristics",
            "Characteristics of included studies (reviewed values)",
            ["Study", "Year", *[f.get("label") or f["field_key"] for f in char_fields]],
            char_rows,
            "data:evidence",
        )
    )
    sec["results"].append(
        table(
            "appraisal_summary",
            "Critical appraisal (approved judgements only)",
            ["Study", "Framework", "Overall judgement"],
            [
                [
                    f"[{key_of.get(s.paper_id, '?')}]",
                    fwm.FRAMEWORKS[s.appraisal["framework_key"]].label
                    if s.appraisal and s.appraisal["framework_key"] in fwm.FRAMEWORKS
                    else "Not appraised",
                    (s.appraisal["overall"] or "").replace("_", " ") if s.appraisal else "—",
                ]
                for s in studies
            ],
            "data:appraisal",
        )
    )
    # synthesis statements
    if syn is not None:
        for st in syn.statements or []:
            if st.get("review") not in ("accepted", "edited"):
                continue
            target = STATEMENT_SECTION.get(st.get("kind", ""), "synthesis")
            cite = " ".join(f"[@{key_of[c['paper_id']]}]" for c in st.get("citations") or [] if c["paper_id"] in key_of)
            cite = " ".join(dict.fromkeys(cite.split()))
            sec[target].append(
                {
                    "id": _bid(),
                    "type": "statement",
                    "origin": "generated",
                    "source": f"synthesis:{syn.version}:{st['id']}",
                    "text": f"{st['text']} {cite}".strip(),
                    "certainty": st.get("certainty"),
                    "kind": st.get("kind"),
                    "evidence": [
                        {
                            "item_id": c["evidence_item_id"],
                            "revision": c["revision"],
                            "paper_id": c["paper_id"],
                            "field_key": c["field_key"],
                        }
                        for c in st.get("citations") or []
                    ],
                }
            )
    if not any(b["type"] == "statement" for b in sec["synthesis"]):
        sec["synthesis"].append(placeholder("evidence synthesis (accept a synthesis version first)"))
    sec["discussion"].append(placeholder("discussion of the findings and of the limitations of the review process"))
    if not any(b["type"] == "statement" for b in sec["conclusion"]):
        sec["conclusion"].append(placeholder("conclusions supported by the accepted synthesis"))
    sec["references"].append({"id": _bid(), "type": "references"})
    # supplementary
    sec["supplementary"] += [
        table(
            "search_strategies",
            "Supplementary: executed search queries",
            ["Date", "Source", "Query", "Filters"],
            strategies,
            "data:search_queries",
        ),
        table(
            "exclusion_log",
            "Supplementary: exclusion log",
            ["Stage", "Record", "Reason", "Date", "Record id"],
            _exclusion_log(db, project),
            "data:screening",
        ),
    ]
    if schema:
        ev_rows = []
        for s in studies:
            for f in fields:
                c = s.cells.get(f["field_key"])
                if c is None:
                    continue
                ev_rows.append(
                    [
                        f"[{key_of.get(s.paper_id, '?')}]",
                        f.get("label") or f["field_key"],
                        c.status,
                        value_text(c.value),
                        c.page_no or "",
                        c.excerpt or "",
                        c.review_status,
                    ]
                )
        sec["supplementary"].append(
            table(
                "evidence_table",
                "Supplementary: extraction table with source references",
                ["Study", "Field", "Status", "Value", "Page", "Excerpt", "Review"],
                ev_rows,
                "data:evidence",
            )
        )
    title = objective[:200] if objective else project.title
    return {
        "title": f"{title}: a systematic review (draft)",
        "sections": [
            {"key": k, "title": t, "required": req, "blocks": sec[k], "verified_by": None, "verified_at": None}
            for k, t, req in SECTIONS
        ],
        "references": refs,
        "review_state": {**state, "generated_at": datetime.now(UTC).isoformat()},
        "protocol_version_id": pv.id,
        "extraction_schema_id": schema.id if schema else None,
        "synthesis_version_id": syn.id if syn else None,
    }
