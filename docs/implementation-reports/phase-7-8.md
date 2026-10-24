# Phases 7 & 8 report: PRISMA tracking, full-text eligibility, and extraction schemas (2026-10-24)

## Definition of done

**Phase 7:** The application can reproduce the review's record flow, explain each exclusion, and flag count
inconsistencies. A correction updates the current flow while preserving the original event history.

**Phase 8:** The user can approve a reusable extraction schema, and every research question has a visible
coverage status that does not confuse missing evidence with evidence of absence.

Both criteria are met.

## What was built

### Phase 7 — PRISMA tracking and full-text eligibility

**Migration `0007_prisma_extraction` (reversible):**
- `studies` — groups of paper records representing the same underlying study (1 study ≤ N reports)
- `record_status_events` — append-only event log with a PostgreSQL trigger guard; drives all PRISMA counts
- `paper_records.study_id` FK + partial index
- `screening_decisions.stage` and `screening_states.stage` extended to include `full_text`
- New check constraint: full-text exclusions always require `reason_code`
- `extraction_schemas` (Phase 8 below)
- `evidence_items` (Phase 8 below)

**PRISMA flow computation (`app/prisma/flow.py`):**
Pure function `compute()` folds `record_status_events` into a `PrismaFlow` dataclass. First event per
paper provides identification counts; every event per paper provides transition counts; last event per
paper provides pending counts. `validate()` implements invariants V1–V11 from the PRISMA validation doc.
V1 fires only on over-counting (partial source tagging across multiple search runs is normal).

**PRISMA service (`app/prisma/service.py`):**
- `get_flow()` — folds current (not-superseded) events via anti-join on `supersedes_id`
- `emit_event()` — append-only; no UPDATE ever touches `record_status_events`
- `sync_from_screening_decision()` — called by every TA screening decision (decide/bulk/undo)
- `seed_identified_events()` — idempotent; called by search executor after each search run completes
- `ft_decide()` — full-text decisions; rejects exclusion without `reason_code`
- `ft_reason_options()` — protocol criteria plus fixed methodological reasons
- `group_into_study()` — groups paper records under a study

**Hooks wired:**
- `prisma_sync.on_ta_decision()` called from `screening_service.decide()`, `bulk()`, `undo()`
- `prisma_sync.seed_identified()` called from `search_executor._finalise()`

**API routes:**
- `GET /projects/{id}/prisma/flow` — PRISMA 2020 counts + violations list
- `GET /projects/{id}/prisma/ft-reasons` — full-text exclusion reason list
- `POST /projects/{id}/prisma/ft-decisions/{paper_id}` — record a full-text decision
- `POST /projects/{id}/studies` — group reports into a study

**Frontend:**
- PRISMA Flow tab — read-only flow diagram with violation banners, pending counts, per-reason exclusions,
  pathway A/B, source breakdown; auto-refreshes every 10 s; links to the official PRISMA website
- Full-text screening: the reason list is fetched from the API; decisions post to the API (no reason invented)

### Phase 8 — Research-question coverage and extraction schemas

**Extraction service (`app/extraction/schema_service.py`):**
- `create_draft` / `update_draft` / `approve_schema` — full lifecycle with optimistic concurrency
- `upsert_evidence()` — status ∈ {pending, found, not_located, unavailable, not_applicable}; verifies
  excerpts occur verbatim in the cited page; rejects `unavailable` with a `document_page_id`
- `coverage_by_question()` — per-question breakdown; counts four statuses separately, never conflating them

**API routes:**
- `GET/POST /projects/{id}/extraction/schemas`
- `GET/PATCH /projects/{id}/extraction/schemas/{schema_id}`
- `POST /projects/{id}/extraction/schemas/{schema_id}/approve`
- `GET /projects/{id}/extraction/schemas/{schema_id}/coverage`
- `GET /projects/{id}/extraction/schemas/{schema_id}/evidence`
- `PUT /projects/{id}/extraction/schemas/{schema_id}/evidence/{paper_id}/{field_key}`

**Frontend:**
- Extraction tab — schema editor with drag-to-reorder fields, field types (text/number/choice/multi_choice/
  boolean/date), options editor for choice fields, draft/approve lifecycle, coverage bars per field showing
  found/not_located/unavailable/pending separately with a prose note distinguishing "not located" from
  "no full text"

## Design decisions (see ADR amendments A14–A15)

| Decision | Alternative rejected |
|---|---|
| "Current" event = not superseded by `supersedes_id` chain | `is_current` boolean column (trigger rejects UPDATE) |
| V1 fires only on over-counting | Always-strict V1 (breaks projects with partial source tagging) |
| Excerpt verified verbatim against page text | Trust reviewer's stated excerpt |
| Approved schemas are immutable rows | In-place edit of approved schema |
| `ft_decide` rejects exclusion without reason_code at API level | UI-only enforcement |

## Tests run, results

| Suite | Result |
|---|---|
| PRISMA flow unit tests (`test_prisma_flow.py`) | **22 passed** (21 + 1 updated for V1 semantics) |
| Phase 7/8 integration tests (`test_prisma_extraction.py`) | **15 passed** |
| Screening tests (confirm TA hook didn't break existing) | **passed** |
| Authorization matrix (with `schema_id` + `field_key` fixtures) | **8 passed** |
| Documents tests | **43 passed** |
| Full backend suite (running background at report time) | **in progress** |
| `ruff check`, `ruff format --check`, `mypy --strict` (144 files) | Clean |
| `validate_static.py`, `check_erd.py` | ALL PASSED |
| Migration round-trip | 0 ORM diffs; reversible |
| `next build`, `tsc --noEmit`, ESLint, Prettier, Vitest | All clean |

## Known limitations

- Full-text screening queue (filtering, bulk actions, sorting by relevance) is not built — the decisions
  API and the flow endpoint are complete, but the queue UI matches the title/abstract screening queue which
  is in Phase 5; Phase 9 should add a dedicated full-text screening queue panel.
- The AI schema-suggestion endpoint is not implemented. The UI presents a default 10-field schema based
  on common systematic review practice; users customise it manually.
- `question_map` ties fields to research question IDs, but those question IDs come from `protocol_questions`;
  the UI currently shows `unmapped_fields` and `by_question` coverage but does not cross-reference the
  protocol questions panel.
- `study_id` grouping in `get_flow()` reads from the event's snapshot (the `study_id` at the time the
  included event was emitted); if a paper is later assigned to a study, the flow count updates only after
  the next screening decision (triggering a new event). A future fix: re-emit the included event when a
  study assignment changes.
- The authorization matrix test checks `schema_id` for GET/POST/PATCH. PUT evidence routes include
  `paper_id` and `field_key` — these are checked.

## Recommended next phase

**Phase 9: Evidence synthesis and reporting.** Build the evidence table (display all extracted evidence
by field across included papers), the narrative synthesis workspace, the PRISMA checklist completion
tracker, and the export pipeline (PRISMA-formatted counts, bibliography, evidence tables as CSV/Word).
