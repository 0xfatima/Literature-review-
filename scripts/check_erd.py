"""Structural sanity check for docs/architecture/erd.md (no Mermaid renderer needed).

Checks: every relationship endpoint has an entity block; every *_id FK column in an
entity points at an existing table (via explicit map for non-obvious names); every
relationship's child entity actually has a matching FK column.
"""
import re, sys, pathlib
src = pathlib.Path("docs/architecture/erd.md").read_text()
block = re.search(r"```mermaid\n(.*?)```", src, re.S).group(1)
rels = re.findall(r"^\s+(\w+) \|\|--o\{ (\w+) : (\w+)", block, re.M)
ents = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\w+) \{\n(.*?)^\s+\}", block, re.M | re.S)}
ents = {k: v for k, v in ents.items()}
errors = []
for parent, child, _ in rels:
    for e in (parent, child):
        if e not in ents: errors.append(f"relationship endpoint without entity block: {e}")
# FK columns: name -> parent table
FK_ALIAS = {"owner_id": "users", "user_id": "users", "actor_id": "users", "uploader_id": "users",
            "reviewer_id": "users", "approved_by": "users", "resolved_by": "users",
            "paper_a_id": "paper_records", "paper_b_id": "paper_records", "paper_id": "paper_records",
            "merged_into_id": "paper_records", "supersedes_id": None, "document_page_id": "document_pages",
            "document_id": "paper_documents", "extraction_schema_id": "extraction_schemas",
            "protocol_question_id": "protocol_questions", "model_run_id": "model_runs",
            "workflow_run_id": "workflow_runs", "synthesis_version_id": "synthesis_versions",
            "draft_version_id": "draft_versions", "evidence_item_id": "evidence_items",
            "search_run_id": "search_runs", "protocol_version_id": "protocol_versions",
            "protocol_criterion_id": "protocol_criteria", "screening_decision_id": "screening_decisions",
            "rerun_of_job_id": "jobs", "study_id": "studies", "owner_id": "users",
            "project_id": "projects", "conversation_id": "conversations"}
fk_by_entity = {}
for name, body in ents.items():
    for line in body.splitlines():
        m = re.match(r"\s*(uuid|string)\s+(\w+)\s+(PK|FK|UK)?", line.strip() and "  "+line.strip())
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("FK"):
            col = parts[1]; tgt = FK_ALIAS.get(col, "??")
            fk_by_entity.setdefault(name, []).append((col, tgt))
            if tgt == "??": errors.append(f"{name}.{col}: unmapped FK")
            elif tgt and tgt not in ents: errors.append(f"{name}.{col} -> missing table {tgt}")
# each relationship child must have an FK to the parent
for parent, child, label in rels:
    cols = fk_by_entity.get(child, [])
    if not any(t == parent for _, t in cols):
        # allow relationships documented as soft links (no FK declared) but report them
        errors.append(f"INFO no FK column on {child} -> {parent} ({label})")
hard = [e for e in errors if not e.startswith("INFO")]
for e in errors: print(e)
print(f"entities={len(ents)} relationships={len(rels)} hard_errors={len(hard)} info={len(errors)-len(hard)}")
sys.exit(1 if hard else 0)
