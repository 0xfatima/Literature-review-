# Phase 3 report: research protocol and review state machine (2026-10-23)

## Definition of done

> A user can create and approve a protocol, close the application, return later, and continue from the correct
> stage. A protocol edit is versioned and identifies which previous results may need to be recalculated.

| Criterion | Evidence (all executed in this environment) |
|---|---|
| Create and approve a protocol | Playwright `protocol.spec.ts`: objective, framework (suggested PICO accepted), elements, question, scope, criteria (suggestions accepted), review, approve. Integration `test_full_protocol_journey_to_approval`: approver, time, SHA-256 content hash recorded; 7 questions and 7 answers in the chat, none duplicated. |
| Close, return, continue at the correct step | Playwright: sign out at the *scope* step, open the URL, sign in, the *scope* card is waiting; after approval, reload shows stage **Search**. A7 gate 2: API **and** worker processes restarted, connection pool disposed, a fresh app instance and login resume from the checkpoint. |
| Survives worker crashes | A7 gate 3: real Celery worker **SIGKILLed inside a graph node**; sweep requeues; a new worker finishes the step; job attempts = 2; exactly one answer, one question message per question, objective stored once. |
| Edit is versioned with reason, impact identified | Integration `test_revision_creates_a_new_version_and_reports_downstream_impact`, Playwright revision section: reason required; v2 starts as an exact copy; changing one criterion yields impact `earliest_stage = RECORD_SCREENING` and the affected stages; v1 becomes *superseded* and is unchanged; the database rejects edits to v1 and to v2's criteria even when the service is bypassed. |
| Later stages cannot silently change approved criteria | DB triggers (migration 0003) + service guard; probed with raw SQL (update, delete, insert child, un-supersede all rejected; project deletion still cascades). |

## What was built

**Domain rules (`app/protocol/`, stdlib only).** Frameworks (PICO, PECO, SPIDER, custom, not applicable); a
`KeywordFrameworkAdvisor` that explains which of the user's words matched and never guesses (no match -> "custom"
with an explanation); protocol normalisation with per-field errors; approval validation (blocking errors vs
warnings); canonical hash; plain-text summary built only from stored content; per-item diff by `stable_key`;
stage impact (`SEARCH` for scope/framework, `RECORD_SCREENING` for criteria, `EXTRACTION` for questions,
`SYNTHESIS` for objective); suggestions that only restate the user's own answers and start as *proposed*.

**Persistence (migration `0003_protocol_workflow`).** `protocol_versions`, `protocol_questions`, `protocol_criteria`,
`workflow_runs`, `workflow_events`, `jobs.workflow_run_id`, schema `lg_checkpoints`, immutability triggers. Details
in `docs/architecture/erd.md` (Phase 3 notes).

**Workflow (`app/workflows/`).** LangGraph `StateGraph` with nodes objective -> framework -> (elements) -> questions ->
scope -> criteria -> review -> approved. Branches: framework *none* skips elements; review routes to approve or back
to any section and then returns to review; a revision re-enters at review; recovery rebuilds from tables. State holds
ids and compact metadata only. Postgres checkpointer in `lg_checkpoints`. Bounded loop (`max_iterations`, recorded
`stop_reason`). The worker task `workflow.advance` is idempotent: it inspects the checkpoint before resuming.

**API** (all behind `get_owned_project`): `GET /projects/{id}/protocol`, `GET .../protocol/versions/{version_id}`,
`PATCH .../protocol/draft` (optimistic concurrency), `POST .../protocol/revisions` (reason required),
`GET|POST .../workflow`, `POST .../workflow/resume`, `POST .../workflow/recover`, `GET .../workflow/events`.

**UI.** Protocol tab (full draft editor; approved version, locked; revision with reason; version history with impact
and content fingerprint); question cards in the *Protocol setup* chat thread; workflow stage track and restart
button on Review status; status bar labels the workflow job and does not offer to cancel it (the server refuses).
The chat banner states that no AI model is used.

## Design decisions (full record: ADR-0001 amendment A10)

1. **Answers applied by the API in one transaction; graph nodes only route.** Rejected: writing in nodes with
   idempotency keys (replay analysis per node; validation errors only asynchronous).
2. **Deterministic question ids** (uuid5 of thread, step, node) make messages and answers write-once.
3. **Tables win:** checkpoint/table disagreement -> `needs_attention`; recovery starts a new thread from the tables.
4. **No LLM yet.** Nothing in this phase needs one; a rule-based advisor behind an interface avoids a fake assistant.
   The LLM gateway moves to the first phase that calls a model.
5. **Project-scoped routes** instead of the spec's `/workflow-runs/{id}` so authorization has one choke point.
6. **One run per project**; `thread_id` separate from run id so recovery can start a fresh thread.

## Tests (actual results)

| Suite | Result |
|---|---|
| Backend pytest, unit + integration, real PostgreSQL 16 + Redis 7, `REQUIRE_INTEGRATION=1` | **189 passed, 0 skipped, 0 failed** (Phase 2: 132). Line+branch coverage 85% (`app/`). Green on 4 consecutive full runs after the last fix (3 x 184 tests, then 1 x 189 after the final unit tests were added). |
| ADR A7 gate (`test_langgraph_gate.py`): real Celery worker subprocesses, real broker | **6/6 passed**: (1) interrupt persisted, worker released; (2) full restart then exactly-once resume; (3) SIGKILL mid-node recovered without duplicates; (4) duplicate resume no-op, invalid payload rejected and run still waiting, out-of-turn rejected; (5) disagreement -> needs_attention, tables win; (6) iteration cap and exhausted attempts end *failed* with a reason. |
| Mutation checks | Disabling the tables-win check -> gate 5 fails. Removing the duplicate-answer check -> 2 tests fail. Both reverted. |
| Stdlib-only runner | 71 passed |
| ruff, ruff format, mypy --strict (86 files), migrations up/down/up + `alembic check`, `verify_locks`, `validate_static` | clean |
| Web: ESLint, Prettier, tsc, Vitest | clean; **35 passed** (27 before) |
| Playwright (Chromium 1194, native stack: uvicorn + Celery + `next start`) | **14/14 passed, 0 flaky, 0 skipped** (13 Phase 2 + the protocol journey). The protocol journey also passed **6/6** with `--repeat-each=6`. |

Coverage of new modules (pytest process only; code run inside the gate's worker subprocesses is not counted):
`protocol_service` 89%, `workflow_service` 86%, `graph` 93%, `runtime` 78%, `protocol/content` 85%.

**Not run here:** the Docker Compose stack and the CI drills (`scripts/drills.py`) were not run in this environment
(no Docker). CI runs both. CI floors raised: backend `tests >= 180`, Playwright `expected >= 14`.

## Defects found by the tests (all fixed)

1. Re-saving an unchanged question/criterion violated `uq_(version, stable_key)`: SQLAlchemy inserted the new child
   rows before deleting the old ones. Fixed by clearing and flushing first.
2. Starting a revision marked the run `needs_attention`: the consistency check compared the checkpoint with the
   *new* draft instead of the version being revised. The revise job now carries the version it revises.
3. Migration CHECK names were double-prefixed by the naming convention (caught by the existing schema-parity test).
4. **UI race:** approving within ~0.5 s of saving the draft sent the old `row_version`; the server correctly refused
   (`version_conflict`, so nothing unseen is ever approved) but the user saw an error. Fixed: the saved version is
   written into the cache immediately and answers are disabled while the protocol refetches. Found by Playwright
   (2/3 passes before, 6/6 after).
5. Test isolation: the gate's fresh app instances shared the Redis rate limiter, and the global recovery sweep
   re-sent other tests' jobs to the gate worker. Both isolated.

## Known limitations and security notes

- **No AI assistance yet.** Framework suggestion is a keyword rule; criteria/question suggestions restate answers.
- **Search is not implemented**, so the impact report can only name stages; it says explicitly that no results
  exist yet. Counting affected records arrives with the tables that hold them.
- A started revision cannot be discarded; approving it unchanged records a version with no impact.
- Rejected suggestions are kept on the version (visible in history, excluded from the summary and from diffs).
- The workflow advances in the worker, so each answer takes a poll cycle (~0.7 s) before the next card appears.
- `workflow_events` stores answers in plain text (project data, deleted with the project); `audit_events` still holds
  only ids and field names.
- `langchain-core` (a LangGraph dependency) can send traces to LangSmith if `LANGSMITH_TRACING` /
  `LANGCHAIN_TRACING_V2` are set; `.env.example` sets both to false.
- Protocol tables have no TRUNCATE guard (admin-only operation), unlike `audit_events`.
- Old checkpoint threads are kept after a recovery; no retention policy yet.

## Migrations, configuration, deployment

- `alembic upgrade head` applies `0003_protocol_workflow` (reversible; downgrade drops `lg_checkpoints`).
- New settings: `WORKFLOW_MAX_ITERATIONS` (60), `WORKFLOW_DEBUG_NODE_DELAY_S` (0; refused outside dev/test). New
  documented env: `LANGSMITH_TRACING=false`, `LANGCHAIN_TRACING_V2=false`.
- Dependencies: `langgraph 1.2.14`, `langgraph-checkpoint 4.2.0`, `langgraph-checkpoint-postgres 3.1.2` (+ transitive,
  all >= 14 days old); `websockets` 17.3 -> 16.1.1. Images must be rebuilt.
- The worker needs no new process; checkpointer tables are created on first use.

## Manual verification

1. `docker compose up --build`, open http://localhost:3000, register, create a project with a topic such as
   "Effect of aerobic exercise compared with usual care on depression".
2. **Protocol** tab -> **Start protocol setup**. Answer the cards in the *Protocol setup* thread; note the framework
   suggestion explains the words it matched.
3. Midway, sign out, sign back in: the same card is waiting. `docker compose restart api worker`: still there.
4. Accept or reject every suggested criterion; at the review, approve. Review status shows stage **Search**.
5. Protocol tab -> give a reason -> **Start a revision**. Change one criterion, save, approve. Open version 2 in
   the history: the impact names Record screening onwards; version 1 is unchanged.
6. Optional crash drill: run a worker with `WORKFLOW_DEBUG_NODE_DELAY_S=5` (dev only), answer a question, `kill -9`
   the worker, start a normal one, wait for the sweep (`JOB_STALE_AFTER_S`): the next card appears once.

## Recommended next phase

Phase 4: scholarly search (OpenAlex and Europe PMC adapters behind `ScholarlySource`), started only from an approved
protocol version, with exact queries logged, pagination/backoff, normalisation and conservative deduplication. The
workflow already waits at the Search stage.
