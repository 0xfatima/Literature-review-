# Phase 9 report: evidence extraction with provenance (2026-10-25)

## Definition of done
"A user can trace a claim in the extraction table back to the source document, correct it, and see which outputs
were generated from that claim." Implemented end to end (API, worker, UI). Whether the automated suites pass is
decided by CI on the pull request: no test, linter or migration check was run against PostgreSQL or a browser while
authoring (see "Tests").

## What was built
**Migration `0008_evidence_extraction` (reversible)**
- `evidence_items`: `origin` (manual | ai | system), `review_status` (unreviewed | approved | edited),
  `extraction_version`, `pages_inspected`, `validation`; status `ambiguous` added.
- `evidence_item_revisions`: append-only (trigger) snapshot before/after of every change, with actor, model run and
  reason. Earlier extraction versions stay inspectable after edits and reruns.
- `evidence_outputs` + `evidence_output_items`: which revision of each claim an output used; an output is stale when
  any claim's current `row_version` is above the recorded revision.
- `llm_consents`: per-project consent (gate G3).

**Extraction pipeline** (`app/extraction/extractor.py`, stdlib only; `app/services/extraction_service.py`)
- Background job `extraction.run` (cancellable, time budget, per-job paper limit, `model_runs` row per request).
- Included papers without a linked, parsed PDF get `unavailable` (origin `system`) with no AI call.
- Page text is split into page-aligned chunks; the model returns found / not_located / ambiguous per field.
- Deterministic validation: unknown fields dropped; a found value is kept only if its quote occurs in the cited
  page (normalised whitespace, case, ligatures, quotes/dashes) and is stored as the exact source substring, with a
  page correction if the quote is on another page of the same request; values are coerced to the field type; numbers
  and % units are checked against the quote (numeric fields discarded on mismatch, text fields flagged); conflicting
  values across chunks become `ambiguous`; `not_located` requires every chunk to have answered and records the pages
  inspected. A discarded proposal leaves the field `pending` with a warning. Nothing is filled by guessing.
- Approved or edited values are never overwritten. "Rerun unreviewed" (force) only replaces unreviewed AI/system
  values; failed or unanswered fields stay pending and are retried by the next run. A new schema version gets fresh
  items; `/affected` lists fields added/changed since the superseded version.

**Review and provenance**
- Manual edits (`PUT …/evidence/{paper}/{field}`) accept `document_id` + `page_no`, require the cited page to belong
  to a document linked to that paper (previously any page id was accepted, including another project's), verify the
  excerpt verbatim, require a note for `ambiguous`, compute numeric warnings, and store a reason in the revision.
- `POST …/approve` records reviewer approval. `GET …/history` returns the item, revisions, the cited page text (flagged
  if from an earlier text version), the model run, and outputs with a stale flag.
- `GET …/table`, `POST …/exports` (CSV, formula-injection-safe, recorded as an output), `GET …/affected`,
  `GET/POST …/runs`, `GET/POST/DELETE /llm-consent`.

**Frontend**: the Tables tab is now the evidence table (status spelled out in every cell, page, review state,
warnings), with a detail panel showing the source page with the excerpt highlighted, approve/correct, history and
outputs; extraction start/rerun/cancel; consent notice and revoke; CSV export; stale-export banner.

## Design decisions
| Decision | Alternative rejected | Why |
|---|---|---|
| Implement gates G2/G3 now | Keep private documents refused | The ADR forbids sending document text without them; extraction needs it |
| Validation in a pure module | Trust structured output | Schema-valid JSON can still contain invented quotes/numbers |
| Locate quotes with normalisation, store exact substring | Exact `in` match only | PDF text has line breaks/ligatures; stored excerpt still passes the verbatim rule |
| Revisions table + current row | Versioned rows per change | Keeps the unique (paper, schema, field) row and simple queries |
| Output lineage by revision number | Copy values into outputs | Staleness is one comparison; no duplication |
| Extraction as a job beside the LangGraph workflow | New graph node | Same pattern as search/screening; the graph stage model is unchanged |

## Tests
Added: `tests/unit/test_extractor.py` (14, stdlib-only), gate/terms tests in `test_llm_provider.py`, settings check,
frontend contract for evidence statuses, `tests/integration/test_evidence_extraction.py` (9), migration-test table
list, `src/lib/extraction.test.ts` (Vitest), `tests/e2e/evidence-table.spec.ts`.
Run while authoring: `ruff format`/`ruff check --fix`, `mypy` (strict, 149 files: no issues), `scripts/check_erd.py`,
`scripts/validate_static.py` (all passed). **Not run**: pytest (unit/integration), alembic round-trip and `alembic
check`, ESLint/Prettier/tsc/Vitest/next build, Playwright. CI runs all of these. No coverage figures yet.

## Known limitations / risks
- The provider-terms record is a human task; until filled in, AI extraction stays off (by design).
- The evidence table UI loads at most 200 papers (CSV export has all). No dedicated full-text screening queue yet.
- No AI schema suggestion; no LangGraph node for extraction; protocol edits do not yet mark evidence for rerun
  (schema versions do).
- CSV is the only output kind; synthesis (next phase) should write `evidence_output_items` for its claims.
- Web formatting was hand-written to Prettier settings; CI's `format:check` may request reflow.

## Migrations / configuration / deployment
`alembic upgrade head` (0008). New settings: `LLM_TERMS_RECORD_VERSION`, `LLM_TERMS_REVIEWED_ON`,
`LLM_TERMS_REVIEW_INTERVAL_DAYS`, `EXTRACTION_JOB_BUDGET_S`, `EXTRACTION_CHUNK_CHARS`,
`EXTRACTION_MAX_PAPERS_PER_JOB`, `RATE_LIMIT_EXTRACTION_PER_HOUR`. The worker must run the new `extraction.run` task
(included automatically via `app.jobs.tasks`).

## Manual verification
1. Include two papers; upload a PDF for one; approve a schema with a number, a choice and a text field.
2. Tables tab: with `LLM_PROVIDER=none`, "Extract evidence" marks the paper without a PDF as "No full text" only.
3. Configure an LLM, set G1/G2, give consent in the panel, extract: open a found cell; the page text shows the excerpt
   highlighted and the page number matches the PDF.
4. Correct a value with an excerpt and page; history shows both revisions. Export CSV, edit again: the banner and the
   cell detail mark the export out of date. Revoke consent: the next run sends nothing.

## Recommended next phase
Phase 10 — synthesis: narrative/structured synthesis from approved evidence only, claims linked to evidence items
(`claim_evidence_links`) and recorded as outputs so edits mark them stale; PRISMA checklist and report exports.
