# Phase 13 report: controlled autonomy, loops and backtracking (2026-10-25)

## Definition of done
"The workflow can pause for approval, resume after a restart, recover from a provider failure, and rerun affected
stages without corrupting the review or erasing the audit trail." Implemented; pass/fail of the automated suites is
decided by CI (see "Tests").

## What was built
**Pipeline graph** (`app/workflows/pipeline.py`, LangGraph + the existing Postgres checkpointer): one small node per
stage — protocol planning, search, screening support, extraction, appraisal, synthesis, report. Nodes contain no
business logic: each calls `app/pipeline/steps.py`, where `evaluate(stage)` reads stored data and answers *done*,
*job* (routine background work) or *human* (a judgement), and `dispatch` starts the work through the normal services
(same permissions, consent gates, rate limits). A node issues at most one interrupt per execution and loops back to
itself; branches that depend only on checkpointed state (awaited job, backoff, failure awaiting a person) come first,
so a resumed node always reaches the same interrupt; every interrupt has a `key` and answers for another key are
ignored. The original protocol-planning graph is unchanged and still drives the protocol conversation; the
pipeline's protocol node waits for its approval and records protocol changes.

**Human approval interrupts** (always, regardless of autonomy): protocol approval; confirming the search plan
(default); screening decisions and full-text assessment; confirming decisions after a criteria change; reviewing
AI-extracted values; approving appraisals; accepting the synthesis; verifying the report (never "publication-ready").
Starting AI extraction or appraisal suggestions asks first by default. The autonomy policy (stored per run) lets the
researcher allow routine work: search, relevance ranking (default on), AI extraction/appraisal, synthesis drafting
(rule-based by default; AI statements separately), report drafting.

**Persistence and restart:** checkpoints per run thread in `lg_checkpoints`; `pipeline_runs` mirrors status, stage,
what it waits for, outputs and counters; `pipeline.advance` jobs are safe to redeliver. The job sweeper wakes runs
whose awaited job finished or whose backoff elapsed; a failed advance marks the run failed and `POST /recover`
continues from the last checkpoint.

**Bounded retries, technical vs research:** a failed job whose error looks transient (worker lost, timeout, rate
limit, unavailable, connection) is retried automatically with exponential backoff (`PIPELINE_MAX_RETRIES`,
`PIPELINE_BACKOFF_BASE_S`, `PIPELINE_BACKOFF_CAP_S`), recorded as `technical_retry`; anything else, or exhausted
retries, goes to a person (retry / skip / stop). Research changes are never retries: they are **reruns** — a new run
that supersedes the current one, with a recorded reason (`rerun` events).

**Backtracking, invalidation, versions:** `POST /rerun {stage, reason}` supersedes the active run, opens
`stage_invalidations` for every downstream stage and starts a new run there. When a newly approved protocol version
is detected, invalidations are opened for the stages its recorded impact affects. Nothing is deleted: synthesis and
report produce new versions; invalidations resolve automatically when a stage's output reflects the change (e.g. a
synthesis accepted after the change) or are dismissed with a reason. Each run keeps its outputs; the run view shows
"what changed compared with the previous run" per stage.

**Criteria change after screening** (the SRS example): `GET /pipeline/impact` classifies every decision recorded under
an earlier protocol version — *needs a new decision* (excluded for a removed criterion), *needs review* (criterion
wording changed, or criteria changed since the record was included), *carry forward* (basis unchanged). The screening
node will not complete while any remain. `POST /impact/confirm` re-records unchanged decisions under the current
version (a new decision row each, so the history shows the confirmation); the others are decided in Screening. Fixed
along the way: re-deciding the same decision under a NEWER protocol version is no longer treated as "unchanged".

**Idempotency:** dispatched jobs carry a deterministic idempotency key (`pl:{run}:{stage}:{operation}:{attempt}`; the
start services now accept `request_key` and derive their nonce from it), and evaluation finds running jobs before
dispatching, so a replayed node never starts a second job; identical queued advance jobs are rejected by the job
fingerprint; a resume is applied only while the checkpoint waits on its key.

**Limits:** per-run AI token budget (`PIPELINE_TOKEN_BUDGET`, extendable by a person) and job count
(`PIPELINE_MAX_JOBS`); process-wide cap on concurrent AI calls (`LLM_MAX_CONCURRENT_CALLS`, a semaphore in every
gateway); per-project 24-hour token budget (`LLM_DAILY_TOKEN_BUDGET`) enforced in the gateway for extraction,
relevance, appraisal, synthesis and the assistant; existing per-job time budgets and Celery time limit; job
`max_attempts`.

**Cancellation and recovery:** pause / resume / cancel; cancelling or rerunning requests cancellation of the awaited
job when it is cancellable; recovery continues from the checkpoint without redoing finished steps.

**Audit trail:** `pipeline_events` is append-only (trigger) and records run start, stage completions, jobs
dispatched/finished, retries, approvals requested/given, pauses, recoveries, reruns and confirmations.

**UI:** a new "Pipeline" tab: autonomy policy, stage states, the pending decision (with "Ask a question about this"),
pause/resume/cancel/recover, rerun-from-stage with reason, criteria-change impact with confirmation, flagged outputs
with dismiss-with-reason, changes since the previous run, and the run history. The status bar labels pipeline jobs.

## Design decisions
| Decision | Alternative rejected | Why |
|---|---|---|
| Separate pipeline graph; protocol graph unchanged | Merge into one graph | No regression in the working protocol conversation; each graph stays small |
| Nodes evaluate stored data, one interrupt per execution | Many interrupts per node | Replay after resume stays aligned even if data changed meanwhile |
| Reruns as new runs | Rewind the checkpoint | Old runs, outputs and events stay intact; diffs between runs are explicit |
| Invalidate, never delete | Delete stale outputs | Audit trail and earlier versions preserved |
| Sweeper wakes waiting runs | Callbacks inside every job | No changes to job executors; robust to lost messages |

## Tests
Added: `tests/unit/test_pipeline_policy.py` (5, stdlib-only: backoff, budgets, autonomy, diffs, criteria impact),
`tests/unit/test_pipeline_graph.py` (6: in-memory checkpointer, stale answers, idempotent dispatch keys, transient
retry with backoff, failure to a person, ask-first and stop), `tests/integration/test_pipeline.py` (5: routine work
dispatched once and awaited, human interrupts, pause/resume, stop, append-only events, rerun with downstream
invalidation and history, recovery from a failed step, impact/confirm), migration test lists,
`src/lib/pipeline.test.ts` (Vitest), `tests/e2e/pipeline.spec.ts`. Run while authoring: ruff format/check, mypy strict
(180 files, no issues), `tsc --noEmit`, `next lint`, Prettier, `check_erd.py`, `validate_static.py`, compiling the
graph. Not run: pytest, alembic round-trip/check, Vitest, Playwright.

## Known limitations
- The pipeline does not answer protocol questions or make screening/appraisal decisions; it waits for them.
- Waking relies on the job sweeper interval (`JOB_SWEEP_INTERVAL_S`, default 60 s) unless the job already finished
  when the run is advanced.
- The concurrent-call cap is per worker process; a cluster-wide cap would need a shared semaphore.
- Invalidations for "upstream rerun" of screening resolve once all decisions are under the current protocol and no
  records are pending; they do not track which new records a rerun search added.

## Migrations / configuration / deployment
`alembic upgrade head` (0011: pipeline_runs, pipeline_events + trigger, stage_invalidations). New settings:
`PIPELINE_MAX_RETRIES`, `PIPELINE_BACKOFF_BASE_S`, `PIPELINE_BACKOFF_CAP_S`, `PIPELINE_TOKEN_BUDGET`,
`PIPELINE_MAX_JOBS`, `LLM_MAX_CONCURRENT_CALLS`, `LLM_DAILY_TOKEN_BUDGET`. New job type `pipeline.advance`; Celery beat
already runs the sweeper that wakes runs.

## Manual verification
1. Pipeline tab → Start: with no approved protocol it waits on "Approve the review protocol"; approve it in the
   chat, press "I have done this — check again": the run moves on.
2. With relevance ranking allowed, the run starts the ranking job by itself and then asks you to screen.
3. Stop the worker while a job runs, start it again: the sweeper re-queues the job and the run continues.
4. Approve a protocol revision that changes a criterion: the Pipeline tab lists affected decisions; confirm the
   unchanged ones and re-decide the rest in Screening; screening completes only then.
5. "Rerun from here" at Search with a reason: the old run is superseded, later stages are flagged, the history keeps
   both runs, and after the rerun "what changed" lists the differences.

## Recommended next phase
Hardening and release: audit-log viewer, backup/restore and retention, security and load testing (including the
pipeline under concurrent runs), accessibility audit, deployment runbook.
