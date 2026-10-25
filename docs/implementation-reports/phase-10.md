# Phase 10 report: critical appraisal and evidence synthesis (2026-10-25)

## Definition of done
"The user can compare extracted findings across studies, inspect the source of a claim, and distinguish supported
conclusions from uncertain interpretations." Implemented end to end (API, workers, UI). Pass/fail of the automated
suites is decided by CI; see "Tests" for what was and was not run while authoring.

## What was built
**Migration `0009_appraisal_synthesis` (reversible):** `appraisal_records`, append-only `appraisal_revisions`
(trigger), `synthesis_versions`; `evidence_outputs.kind` gains `synthesis`.

**Critical appraisal** (`app/appraisal/`)
- Five design-specific frameworks: randomised trials (RoB 2 domain structure), non-randomised intervention studies
  (ROBINS-I structure), cohort/case-control (Newcastle-Ottawa domains, judged per domain, no stars), analytical
  cross-sectional (JBI item structure), qualitative (CASP structure). Prompts are short paraphrases; each framework
  links the official guidance. A framework is suggested from the reviewed extracted design / publication type; the
  user can choose another per study.
- No single quality score: categorical domain judgements; for risk-of-bias tools the overall is SUGGESTED by the
  worst-domain rule and departing from it needs a justification; checklists compute no overall and always need one.
- Stored per domain: reviewer judgement, justification, optional verbatim excerpt + page (verified against the linked
  PDF), who decided and when, and separately the AI suggestion. Approval requires every domain to be judged by a
  reviewer; editing an approved appraisal needs a reason and reopens it. Every change is an append-only revision.
- `appraisal.suggest` job (consent gates G1-G3): AI proposes a judgement per domain; a substantive judgement without
  a verified quote becomes the framework's "no information" value. Suggestions never count as judgements.

**Comparison and synthesis** (`app/synthesis/`)
- Comparison tables from REVIEWED extraction values only (unreviewed AI values are counted and excluded), grouped by
  any schema field (design, population, method, dataset, outcome...) or by research question (schema question map).
  Per field: agreement / disagreement / single study / reported differently (free text) / numeric range (never
  pooled); gaps keep reasons apart (not located, no full text, ambiguous, not reviewed); methodological differences
  (designs, frameworks) and limitations (serious approved appraisals, unappraised studies).
- `synthesis.draft` job creates a draft version: rule-based statements (agreements, disagreements, gaps, limitations,
  method differences) and, optionally, AI-drafted statements (consent gates). AI statements must cite evidence keys
  that were given; uncited statements and any statement implying statistical pooling are dropped.
- Certainty is computed deterministically per statement (supported / uncertain / conflicting / insufficient):
  single study, ambiguous source, unappraised or seriously-flawed studies, conflicting typed values, or numbers not
  present in the cited evidence lower it. A reviewer or model may only lower certainty, never raise it.
- Each statement records the evidence item + revision and approved appraisal + revision it cites; the UI opens the
  value, excerpt and page. Statement review: accept / edit / reject / add / refresh. Acceptance requires every
  statement reviewed, at least one accepted, and no stale citations; it supersedes the previous accepted version and
  records an evidence output, so later evidence edits mark the synthesis out of date (Phase 9 lineage). Accepted
  versions are immutable; "revise" creates a new draft with changed statements back to unreviewed.
- Meta-analysis: explicitly out of scope (`meta_analysis.in_scope = false`), shown in the UI; no pooled estimate is
  computed anywhere and none may come from a model.

**API** (all scoped by `get_owned_project`): `/appraisal`, `/appraisal/frameworks`, `/appraisal/papers/{paper_id}`
(GET/PUT), `/approve`, `/history`, `/appraisal/suggestions` (GET/POST); `/synthesis/comparison`, `/synthesis/versions`
(GET/POST), `/synthesis/versions/{synthesis_id}` (+ `/review`, `/accept`, `/revise`), `/synthesis/drafts` (GET/POST).

**Frontend:** new Appraisal and Synthesis tabs (AI suggestions visually and textually separate from reviewer
judgements; certainty spelled out with its reasons; citations expand to value, excerpt and page; stale flags).

## Design decisions
| Decision | Alternative rejected | Why |
|---|---|---|
| Framework registry in code, versioned | User-defined tools stored in DB | Validated domain structures first; custom tools need their own review UI |
| AI suggestion stored beside, never in, the judgement | One field with an origin flag | Approval logic and UI cannot confuse the two |
| Certainty computed by rules, can only be lowered | Model-assigned certainty | Keeps conclusions from overstating the evidence |
| Statements as versioned JSON with item revisions | `claim_evidence_links` table now | Reuses Phase 9 output lineage for staleness; a link table can follow with report drafting |
| No meta-analysis | LLM-computed pooling | Required to be a separately specified, validated statistical feature |
| Shared `llm_jobs` helpers | Copying extraction job code | One place for gates and model-run bookkeeping |

## Tests
Added: `tests/unit/test_appraisal_logic.py` (7, stdlib-only), `tests/unit/test_synthesis_logic.py` (6, stdlib-only),
`tests/integration/test_appraisal_synthesis.py` (6), authorization matrix extended with `synthesis_id`, migration test
table/trigger list, `src/lib/synthesis.test.ts` (Vitest, 3), `tests/e2e/appraisal-synthesis.spec.ts`.
Run while authoring: `ruff format`/`ruff check` (clean), `mypy` strict (160 files, no issues), `tsc --noEmit`
(clean), `next lint` (clean), Prettier on changed files, `check_erd.py` and `validate_static.py` (pass).
Not run: pytest, alembic round-trip/`alembic check`, Vitest, `next build`, Playwright. No coverage figures yet.

## Known limitations
- No custom (user-defined) appraisal frameworks; no inter-rater (dual reviewer) workflow.
- Grouping by research question depends on the schema's question map; the comparison shows one table per group.
- Synthesis is not yet a LangGraph node (same job pattern as earlier phases); protocol edits do not mark syntheses
  stale (evidence and appraisal edits do).
- Large projects: the AI draft prompt is truncated at 60 000 characters (reported in the job result).

## Migrations / configuration / deployment
`alembic upgrade head` (0009). No new settings (AI jobs reuse the extraction budget, chunk size and per-hour limit).
Worker picks up `appraisal.suggest` and `synthesis.draft` automatically.

## Manual verification
1. With two included papers and reviewed extraction values, open Appraisal: the suggested framework follows the
   extracted design. Ask AI for suggestions (with consent): suggestions appear in dashed boxes; "Approve" stays
   disabled until each domain has a reviewer judgement.
2. Synthesis → group by design: tables show agreement/disagreement, numeric ranges marked "not pooled", and gaps by
   reason. Generate a draft: open a statement's sources to see value, excerpt and page; certainty reasons explain the
   label. Accept/reject every statement, accept the synthesis, then edit a cited value: the statement shows "Out of
   date"; revise to get a new draft.

## Recommended next phase
Phase 11 — report drafting and export: PRISMA-structured sections built from the accepted synthesis, a citation
verifier (`claim_evidence_links`), bibliography, PRISMA flow diagram and checklist, DOCX/PDF export.
