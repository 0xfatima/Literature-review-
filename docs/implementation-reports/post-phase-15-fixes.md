# Post-Phase-15 fixes: full-text screening, studies, question coverage, error envelope

Scope: close the gaps found while mapping the Phase 16 critical journey to the code, before Phase 16 starts.

## What was wrong and what changed

| # | Problem | Fix | Proof |
| --- | --- | --- | --- |
| 1 | **PRISMA flow bug.** Every step supersedes the record's previous event, so once a record had a full-text outcome its `ta_included` event was no longer current. The record dropped out of "sought for retrieval", reappeared as awaiting title/abstract screening, and check V5 failed. Existing tests only asserted `included_reports >= 1`. | `prisma/flow.py`: a full-text outcome (`included`, `ft_excluded`, `not_retrieved`) implies title/abstract inclusion when no explicit `ta_included` event is current. | `test_flow_stays_consistent_after_full_text_outcomes` (integration, regression), two unit tests in `test_prisma_flow.py` |
| 2 | **"Reports not retrieved" could never be recorded.** No code emitted `not_retrieved`, so that PRISMA box was always 0. | `POST /projects/{id}/prisma/retrieval/{paper_id}` marks a report not retrieved (optional note) or undoes it (`{"retrieved": true}`). Both are new events that supersede the current one; both are audited (`full_text.not_retrieved`, `full_text.retrieval_restored`). | `test_not_retrieved_can_be_undone_and_is_audited` |
| 3 | **No full-text screening screen.** The API existed; the UI did not. | New **Full text** tab (`components/fulltext/FullTextPanel.tsx`) backed by `GET /prisma/ft-queue` (records past title/abstract with status awaiting / uncertain / included / excluded / not retrieved, counts, filter). Include, Uncertain, Exclude with a required reason (protocol criteria first, then standard reasons; "Other" needs a note), "Could not get the full text", optimistic concurrency. The stage navigator's Full text stage now opens this tab. | `test_full_text_queue_lists_only_records_past_title_abstract`, `lib/fulltext.test.ts` |
| 4 | **Study grouping had no UI**, and the PRISMA panel pointed at a non-existent "Studies tab". Grouping accepted reports that were not included, and reports already in a study. | "Studies and reports" section in the Full text tab; `GET /studies`, `DELETE /studies/{id}` (ungroup). Grouping now requires reports included at full text and not already grouped. PRISMA panel text fixed, with a button to open full-text screening. | `test_studies_list_group_and_ungroup`; the authorization matrix now covers `{study_id}` |
| 5 | **Fields could not be linked to research questions in the UI**, so per-question coverage was always empty for schemas made in the app; no question-level status existed. | Extraction editor: per-field checkboxes for the approved protocol's questions (saved as `question_map`); draft list shows "→ Q1, Q2". Coverage view shows each question as Answered / Partially addressed / Not found / Not found: no full text / Not assessed / No fields mapped, with missing full text kept apart from absent information. | `questionCoverage` tests in `lib/extraction.test.ts` |
| 6 | **500 errors had no reference number.** Starlette's catch-all handler runs outside the request middleware, after the correlation id is reset: `correlation_id: null`, no `X-Correlation-ID`, no security headers, not logged as a request. | `main.py` handles unexpected exceptions inside the request middleware. | `test_failed_export_is_reported_and_leaves_no_partial_record` (also proves a failed export leaves no export row or audit event and the next export works) |
| 7 | README stale (assistant "not connected", amendments A1–A8, Phase 2/3 limitations). | Rewritten status lines and one current known-limitations list. | n/a |

Already handled by Phase 15 and not changed: backup/restore drill (`scripts/restore-drill.sh`, run in CI).

## Tests run (this environment: PostgreSQL 16, Redis 7, Python 3.12, Node 22)

- Backend unit: **454 passed**.
- Backend integration, all 22 files including `test_langgraph_gate.py` with a real Celery worker: **245 passed, 0 skipped** (run in four batches).
- ruff, ruff format, mypy (strict): clean.
- Frontend: Prettier, ESLint, `tsc --noEmit` clean; Vitest **83 passed**; `next build` succeeded.
- **Not run here:** Playwright E2E and `scripts/drills.py` (no Docker or browser download in this sandbox). CI must confirm them. No new E2E spec was added for the Full text tab; Phase 16's journey test should cover it.

## Migrations and configuration

None. New routes only; no schema or setting changes.

## Known limitations

- Ungrouping deletes the `studies` row (audited as `study.ungrouped`); the grouping history is in the audit log only.
- Renaming a field's key in the schema editor drops its question links (they are keyed by `field_key`).
- "Answered" means evidence was located for every linked field in every included paper; it does not judge what the evidence concludes.

## Follow-up: CI failure on PR #28 (migration round-trip)

CI failed `test_upgrade_downgrade_upgrade_roundtrip` and, as a knock-on effect, `test_every_model_declared_index_exists_in_the_migrated_database` (the half-finished downgrade left the database without Phase 15's indexes).

**Cause: a latent bug in migration 0007, exposed by test order.** 0007's downgrade re-adds a `stage in ('title_abstract')` check to `screening_decisions` and `screening_states`, which fails on any database holding a full-text decision. The round-trip test only ever passed because the files that create full-text decisions (`test_prisma_extraction.py`, …) sort *after* `test_migrations_and_health.py`; the new `test_fulltext_and_studies.py` sorts before it. Running the two files in that order reproduced CI's two failures exactly. A real rollback past 0007 on a database with full-text decisions would have failed the same way.

**Fix.** 0007's downgrade now deletes the full-text rows that cannot exist below 0007 (trigger disabled for that one statement, since `screening_decisions` is append-only), then restores the checks. Destructive, like the rest of that downgrade; back up first. The round-trip test now creates a full-text decision itself before downgrading, so it no longer depends on test order.

**Verified:** both tests fail on the old code and pass on the new code in CI's order; all 699 backend tests pass; `alembic upgrade head && downgrade base && upgrade head && check` passes on a clean database; mypy and ruff are clean.
