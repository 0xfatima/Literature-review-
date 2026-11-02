# Phase 15 report: performance, concurrency and deployment

## Definition of done
"The app can handle representative concurrent workloads without losing state, leaking data between users, or
exhausting external-provider quotas. Performance targets must be measured and documented rather than assumed."
Met for the measured workload (8 concurrent users, 3,000-paper reviews): no lost state (every job finished, exact
paper counts), no leaks (0 across five isolation checks), provider limits held exactly. Numbers, conditions and caveats:
`docs/performance.md`; raw output `docs/performance/loadtest-8-users.json`. CI decides the automated suites and the
production deployment check (see "Tests").

## What was built (by task)
1. **Long-running work in workers.** Already true for search, relevance, PDFs, extraction, appraisal, synthesis and the
   pipeline. Report generation was measured (p50 0.6 s under load) and deliberately stays synchronous.
2. **Queues, bounded concurrency, provider limits, backpressure.**
   - *Fair admission* (`job_service.pump`): jobs are always persisted; at most `JOB_MAX_INFLIGHT_PER_USER` per user and
     `JOB_MAX_INFLIGHT_GLOBAL` in total are handed to the broker, in fair-share order: a waiting job's rank is its
     position in its owner's queue plus the owner's admissions in the last `JOB_FAIR_SHARE_WINDOW_S`, so the
     least-recently-served user goes first (ties: user-facing steps before bulk PDF/AI work, then age). A first
     version ranked by queue position only; `test_round_robin_interleaves_users_when_capacity_frees` showed that under
     tight capacity a later user then waited behind a heavy user's entire older backlog, which this design fixes. Runs after every enqueue, after every finished job (Celery `task_postrun`) and every
     `JOB_PUMP_INTERVAL_S` (beat); serialised by a Postgres advisory lock (bounded 500 ms wait in requests). The sweeper
     re-sends only admitted jobs; never-admitted ones (including after a broker outage) belong to the pump. The API
     exposes `waiting_for_capacity`; the status bar shows "Waiting for capacity".
   - *Queues*: `interactive`, `heavy`, `system`, plus the legacy `celery` queue (a rolling deploy's old API must never
     strand messages — found by the real-Celery gate tests).
   - *Provider protection*: a Redis lease semaphore bounds AI calls across all processes
     (`LLM_GLOBAL_MAX_CONCURRENT_CALLS`; crashed holders free their slot); a 429 from any provider sets a shared cooldown
     every worker honours; a deployment-wide 24 h token budget (`LLM_GLOBAL_DAILY_TOKEN_BUDGET`).
3. **Indexes and expensive queries** (measured before/after): two indexes (migration `0013`, built CONCURRENTLY);
   ingestion −27 % SQL statements; search summary 7 → 2 queries per scope.
4. **Pagination.** Paper lists, the screening queue, evidence table and audit log were already paginated; last-page cost
   at 3,000 papers measured (21 ms vs 14 ms), so no keyset change was needed.
5. **Caching.** None added; justification and rejected options in `docs/performance.md`.
6. **Observability.** `GET /internal/metrics` (Prometheus text, `METRICS_TOKEN`, 404 otherwise; not routed by the
   proxy): request latency histograms by route template (per replica); queue delay, run time, failure ratio, backlog
   and waiting-for-capacity by job type; AI calls/tokens and scholarly requests/rate-limited queries — job and provider
   figures are computed from Postgres so they cover every process.
7. **Concurrent users** — `test_phase15_concurrency.py` (CI) and the load harness's isolation checks.
8. **Load test** — `scripts/loadtest/` (searches, read mix, screening, PDF parsing, AI extraction, reports) against a
   mock provider that records peak concurrency and request rate.
9. **Retry-safe recovery and graceful shutdown.** Existing acks-late + sweeper + idempotent claims, plus a drain: on
   SIGTERM the worker main process writes a sentinel that its prefork children check at every existing checkpoint, so
   long jobs stop with resumable state inside the stop grace period. Uvicorn drains requests (≤25 s).
10. **Production configuration.** `docker-compose.prod.yml` (only the proxy publishes ports; password-protected Redis
    with AOF; separate interactive/heavy pools; exactly one beat; stop grace periods), `infra/proxy/nginx.prod.conf`
    (TLS 1.2/1.3, HTTP→HTTPS, ACME webroot, HSTS, `/internal/` refused), Docker secrets via `SECRETS_DIR`,
    `scripts/gen-secrets.sh`, `.env.production.example`, configurable scholarly endpoints (https enforced in production).
11. **Documented deploy.** `scripts/deploy.sh` (pre-flight → build → backup before upgrade → migrate → start → wait →
    smoke) and `docs/runbooks/deployment.md` (topology, secrets, TLS, upgrade, rollback, shutdown, monitoring).
12. **Verification.** `scripts/smoke.py` (stdlib only) and `scripts/restore-drill.sh` (marker user, project and parsed
    PDF; backup; delete; restore; verify rows, audit history and the file). CI job `deploy-verify` runs both against the
    production compose file with a self-signed certificate on every pull request.

## Files
Created: `app/core/{distributed,metrics,shutdown}.py`, `app/services/metrics_service.py`, `app/api/internal.py`,
`app/db/migrations/versions/0013_performance_indexes.py`, `tests/unit/test_{metrics,shutdown_and_throttle,
phase15_config,distributed_slots}.py`, `tests/integration/test_phase15_concurrency.py`, `scripts/loadtest/{run,
mock_providers}.py`, `scripts/{smoke.py,deploy.sh,gen-secrets.sh,restore-drill.sh}`, `docker-compose.prod.yml`,
`infra/proxy/nginx.prod.conf`, `.env.production.example`, `docs/performance.md`, `docs/performance/loadtest-8-users.json`,
`docs/runbooks/deployment.md`, this report.
Modified: `core/{config,settings_checks}.py`, `main.py`, `jobs/{celery_app,tasks}.py`, `services/{job_service,
search_executor,llm_jobs,extraction_service,relevance_service}.py`, `appraisal/service.py`, `repositories/{jobs,
search}.py`, `schemas/jobs.py`, `providers/llm/{__init__,base,gemini}.py`, `providers/scholarly/{__init__,http,throttle,
openalex,europepmc,arxiv,crossref}.py`, `db/models.py`, web `StatusBar.tsx` + `types.ts`, `tests/integration/conftest.py`,
`scripts/{restore.sh,validate_static.py}`, `.github/workflows/ci.yml`, `.env.example`, `.gitignore`,
`docs/architecture/{erd,security}.md`, `docs/runbooks/data-retention-and-recovery.md`, `README.md`.

## Design decisions and rejected alternatives
- **Admission in Postgres, not broker priorities/rate limits.** Celery priorities on Redis are approximate and per-queue;
  Postgres already owns job state, so fairness, limits and "waiting" status come from one source of truth and survive
  broker loss. Rejected: rejecting requests with 429 when busy (a user uploading 50 PDFs must not get errors).
- **Redis lease semaphore** (sorted set, Lua) over a database counter (hot row) or per-process limits (N processes × limit).
  Fails open to the per-process limit on Redis outage, consistent with the existing limiter (Redis down also stops the queue).
- **Metrics without a new dependency**: exposition format written directly; job/provider metrics derived from Postgres
  instead of per-process counters (prometheus_client multiprocess mode would need shared directories per container).
- **Drain via sentinel file**: Celery signals fire in the main process while prefork tasks run in children.
- **Single-host Compose as the documented target**; the same images, secrets layout and rules apply to Kubernetes.
- **Load test in-process** so it can run anywhere with PostgreSQL + Redis (including CI-like sandboxes); the real broker
  and proxy are covered by `e2e` and `deploy-verify`.

## Tests
Run in the authoring sandbox (PostgreSQL 16, Redis 7, Python 3.12, CI's locked dependencies):
- ruff check / ruff format --check / mypy (strict): clean. `validate_static.py`: all pass. Stdlib shim: 139 passed.
- New unit tests: 13 passed (pytest). Migration up → down → up + `alembic check`: no differences.
- Full backend suite against PostgreSQL 16 + Redis 7 with CI's environment: **691 tests, 0 failures, 0 errors,
  0 skipped** (CI post-check passes). Run as 22 per-file pytest sessions because the sandbox was restarted several
  times; the tests and settings are the ones CI runs in one session.
- Load test: 8 users, results above (exit 0: no leaks, no job errors).
**Not run here:** web lint/typecheck/vitest/build, Playwright, the drills, `dependency-audit` and `deploy-verify`
(Docker is unavailable in the sandbox) — CI decides them. `deploy-verify` is new and has never run; the production
compose file and scripts were statically checked and the settings/secrets loading was executed, but not booted.

## Known limitations
- Latencies under load come from a single-process harness (pessimistic); production numbers should be taken from
  `/internal/metrics` after deployment and compared with the targets in `docs/performance.md`.
- A drained job ends as "stopped (time budget)" and is continued by the user or the pipeline, not automatically.
- Request histograms are per process (scale API with replicas of one process each, as configured).
- Single-host deployment; object storage is a local volume (no S3 adapter yet); no autoscaling.
- Smoke-test users remain in the database (no self-service account deletion yet).

## Migrations, configuration, deployment
Migration `0013_performance_indexes` (indexes only, CONCURRENTLY, reversible). New settings (all in `.env.example`):
`JOB_MAX_INFLIGHT_PER_USER`, `JOB_MAX_INFLIGHT_GLOBAL`, `JOB_PUMP_INTERVAL_S`, `WORKER_DRAIN_TIMEOUT_S`,
`JOB_FAIR_SHARE_WINDOW_S`, `LLM_GLOBAL_MAX_CONCURRENT_CALLS`, `LLM_GLOBAL_DAILY_TOKEN_BUDGET`, `SCHOLARLY_ENDPOINTS`, `METRICS_TOKEN`,
`METRICS_WINDOW_S`, `SECRETS_DIR`. Workers must run beat exactly once (the dev compose keeps `-B`).

## Manual verification
1. `scripts/gen-secrets.sh`; self-signed cert (runbook); `cp .env.production.example .env.production` with
   `https://localhost`; `scripts/deploy.sh --insecure` → ends with "smoke: ALL PASSED".
2. Upload 20 PDFs as one user and start a search as another: the second user's search starts immediately; the first
   user's extra uploads show "Waiting for capacity" and then process.
3. `COMPOSE=... scripts/restore-drill.sh https://localhost --insecure` → marker project and PDF restored.
4. Scrape `/internal/metrics` from inside the network with the token; check `srs_jobs_active`, `srs_job_queue_delay_seconds`.
5. `docker compose ... stop worker-heavy` during an extraction: the job stops at a checkpoint; restart; continue it.

## Recommended next phase
Release hardening: self-service account deletion and data export, S3-compatible storage, CSP nonces, an
external monitoring stack (Prometheus/Grafana/alerting) wired to the documented targets, and a pilot with real
provider quotas to replace mock-provider figures with production ones.
