# Phase 11 report: review report and export artifacts (2026-10-25)

## Definition of done
"A user can export a draft that reflects the approved review data and can trace references, reported counts, and
extracted claims back to their sources. The application must not claim that the review is publication-ready without
appropriate human verification." Implemented end to end; pass/fail of the automated suites is decided by CI.

## What was built
**Migration `0010_report_drafts`:** `draft_versions` (immutable report versions), `claim_evidence_links` (citation ->
stored paper record, optional evidence item + revision), `report_exports` (format, filename, SHA-256, size, review
state, validation summary, whether data changed after the draft).

**Generation** (`app/reports/build.py`, no AI): ten sections — title/abstract, introduction and objective, methods
and search strategy, eligibility criteria, results and study characteristics, evidence synthesis, limitations and
discussion, conclusion, references, supplementary material.
- Methods come from the approved protocol (version, approval date, content hash, framework, sources, dates,
  languages, search strategy, synthesis plan) and the recorded search queries (date, source, status, results).
- Counts come from the PRISMA flow computed from record events; tables (criteria, search history, PRISMA counts,
  full-text exclusion reasons, study characteristics from reviewed values with page numbers, approved appraisals,
  extraction fields, supplementary search queries, exclusion log and full extraction table) are built from stored
  rows and cannot be edited.
- Synthesis, limitation/gap and conclusion text comes from the accepted synthesis version, each statement keeping
  its certainty label and evidence (item + revision).
- Everything the authors must write (background, discussion, abstract conclusions) is a visible `[[placeholder]]`.
- References are the included studies' stored records with stable keys (`smith2020`, `smith2020a`, ...); text cites
  them as `[@key]`. Authors can add other stored records of the project as references; nothing else can be cited.
- `review_state` snapshots protocol/schema/synthesis versions, PRISMA counts, search runs, evidence and appraisal
  status with a SHA-256 fingerprint.

**Validation** (`app/reports/validate.py`, deterministic, re-run live on every view): unknown citation keys and
references whose record no longer exists (errors); stated counts that contradict the PRISMA flow, PRISMA violations
(errors); missing citations for a statement's evidence, citations not among its evidence, evidence changed since
generation, numbers absent from the cited evidence, uncited edited claims in result/discussion/conclusion sections,
conclusions on uncertain/conflicting/insufficient evidence, placeholders, empty required sections/tables, retracted
references (warnings). The summary label always says what verification is missing; `publication_ready` is always
false and every export carries a notice that the draft is not publication-ready.

**Versions and editing:** every edit creates a new version (set text, add/remove paragraph, add reference, set title,
verify section); tables and the reference list cannot be edited; a content change resets that section's human
verification; optimistic check against the latest version; earlier versions stay readable and can be edited (restore).

**Exports:** DOCX (WordprocessingML written with the standard library: headings, tables, notice), Markdown,
supplementary ZIP (report.md, report.docx, references.csv, every table as CSV, prisma_flow.json, manifest.json,
README notice) and PRISMA JSON. Each export is recorded with version, SHA-256 of the exact bytes, review state and
whether the data changed after the draft; CSV cells are protected against formula injection. PDF is not included
(not required; DOCX converts losslessly in word processors).

**Tracing:** `GET /reports/{id}/trace?claim_id=` returns the claim, its references (DOI/PMID/venue/retraction flag),
evidence (value, excerpt, page, cited vs current revision) and, for counts, the recorded PRISMA facts and how they
were computed; without `claim_id` it lists all claims with link counts.

**API:** `GET/POST /reports`, `GET /reports/{draft_id}`, `POST /reports/{draft_id}/edits`,
`GET /reports/{draft_id}/trace`, `GET/POST /reports/{draft_id}/exports` (all via `get_owned_project`).

**Frontend:** the "Artifacts" placeholder tab is replaced by a working "Report" tab: generate/regenerate, version
picker, verification banner, per-block issues, "Show sources" tracing, edit mode (save as new version with a change
summary), per-section verification, exports and export history.

## Design decisions
| Decision | Alternative rejected | Why |
|---|---|---|
| Deterministic generation, placeholders for author text | LLM-written report | Rule 6 (no fabrication); every sentence is traceable |
| Immutable versions | In-place edits with history | Simple restore/compare; exports point at a fixed version |
| Tables stored in the version | Re-query at export | A version reproduces exactly what was reviewed |
| Stdlib DOCX writer | python-docx | No new dependency/lock change; output is minimal but valid |
| Live re-validation + fingerprint | Validation only at creation | Shows when evidence changed after a draft |

## Tests
Added `tests/unit/test_report_logic.py` (9, stdlib-only), `tests/integration/test_reports.py` (4), authorization
matrix extended with `draft_id`, migration table list, `src/lib/report.test.ts` (Vitest, 3),
`tests/e2e/report.spec.ts`. Run while authoring: ruff format/check, mypy strict (167 files, no issues), `tsc
--noEmit`, `next lint`, Prettier on changed files, `check_erd.py`, `validate_static.py`, and an ad-hoc render/validate
smoke run (DOCX parts parse as XML; output is byte-identical across runs). Not run: pytest, alembic round-trip/check,
Vitest, `next build`, Playwright. No coverage figures yet.

## Known limitations
- No PDF export; DOCX has no Word-native citations/fields (references are plain text keys).
- Generation is synchronous (fast: database reads only); very large projects may need a background job.
- The PRISMA flow is exported as counts (JSON/table), not as a drawn diagram.
- Number checking covers PRISMA-style counts and evidence-linked statements, not every number in free text.
- Protocol re-approval does not automatically regenerate a draft (the fingerprint flags it).

## Migrations / configuration / deployment
`alembic upgrade head` (0010). No new settings or dependencies.

## Manual verification
1. With an accepted synthesis, open Report → Generate draft. Check the banner ("Unverified draft"), the PRISMA
   paragraph against the PRISMA Flow tab, and "Show sources" on a synthesis statement (page + excerpt).
2. Edit the results paragraph to a wrong count and add an uncited claim; save: v2 shows an error and a warning; v1 is
   unchanged in the version picker.
3. Export DOCX and the supplementary ZIP; open them: the notice is on top, tables match the app, manifest.json names
   the version and fingerprint. Edit a cited extraction value; the draft flags changed data and later exports record it.

## Recommended next phase
Hardening and release: audit-log review UI, backup/restore and data retention, load and security testing
(OWASP ASVS checks, dependency scanning), accessibility audit of the new tabs, and a deployment runbook.
