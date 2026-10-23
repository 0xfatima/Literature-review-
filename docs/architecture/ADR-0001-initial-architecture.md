# ADR-0001: Initial architecture

- **Status:** **Accepted** (2026-10-20) with Amendments A1–A8 below, which supersede any conflicting text in §3–§8. D2 and D7 are accepted *provisionally*; their trade-offs and the security-test matrix are in A3.
- **Revision history:** 2026-10-20 Proposed → 2026-10-20 Accepted after product-owner review (decisions 1–8 incorporated).
- **Date:** 2026-10-20
- **Source of truth:** `docs/requirements/autonomous_systematic_review_scientist_requirements.docx` (v1.0). Where this ADR departs from or extends the spec, the departure is marked **[DEVIATION]** or **[ADDITION]**.
- **Operating principle (from spec):** AI proposes; deterministic code validates; humans approve consequential research decisions.

## 1. Context

Greenfield repository (see `repository-dependency-audit.md`). The product is a human-in-the-loop systematic-review workbench: protocol → search → screening → PDFs → extraction → synthesis → draft, with reproducible provenance and an audit trail. Priorities when requirements conflict (spec §Final): scientific traceability > user control > privacy/security > data integrity > apparent autonomy.

## 2. Architecture overview

```
Browser ── Next.js (UI only; same-origin proxy /api → FastAPI)
              │  HttpOnly session cookie
              ▼
        FastAPI  (auth, REST, validation, authorization, SSE)
              │  services (business logic)  ── repositories ── PostgreSQL
              │            │
              │            ├── providers/ (LLM, scholarly, web_search, storage, email, auth)  ← interfaces + adapters
              │            └── workflows/ (LangGraph; nodes call services)
              ▼
        Redis (broker only) ── Celery workers ── same `app` package
                                   runs: search, PDF parse, screening, extraction, synthesis, export, graph steps
```

Module rule (spec §14): **API routes validate and delegate → services coordinate repositories/providers → workflow nodes call services.** Nodes contain no business rules; LLM output is a *proposal* parsed by Pydantic and persisted only by a service function.

## 3. Decisions

### D1. Modular monolith, one Python package, separate worker *process*
Single package `app` under `services/api/app`; `services/worker/tasks/*` are thin Celery entrypoints importing `app.services`. Deployed as three processes (web, api, worker) from one repo. *Rejected:* microservices / one deployment per agent (spec non-goal; adds distributed-failure modes before any evidence of need).

### D2. Sync core: SQLAlchemy 2.x + psycopg 3, sync repositories/services **[DEVIATION from "async I/O" rationale in spec §6]**
- One code path shared by API handlers (threadpool), Celery workers (sync) and tests. External I/O (providers, LLM, PDF) happens in workers, not in request handlers, so async buys little in handlers.
- psycopg 3 is also what LangGraph's Postgres checkpointer uses, so there is **one** driver family.
- `async` is used only where it matters: SSE endpoints (read job/progress events).
- *Rejected:* async SQLAlchemy + asyncpg for API and sync for workers (two repository implementations or a leaky wrapper; two drivers). Reversible only with real cost, so flagged in §8.

### D3. LangGraph runs inside workers; Postgres is the system of record, the checkpointer is not
- Graph `thread_id` = `workflow_runs.id`. Checkpointer: LangGraph Postgres saver in a separate schema (`lg_checkpoints`) of the same database (one backup, one restore).
- `ReviewState` holds **IDs and compact metadata only** (spec §5.3). PDFs/text/records live in tables/storage.
- Human gates use LangGraph `interrupt`. When a node interrupts, the worker persists `workflow_runs.status = awaiting_input` + a typed `pending_question` row/payload and the task ends (no worker is held while a human thinks). `POST /workflow-runs/{id}/resume` enqueues a task that resumes with the user's typed payload.
- Typed domain tables (protocol versions, decisions, evidence, …) remain authoritative. If checkpoint and tables disagree, tables win and the run is marked `needs_attention`.
- Retries: bounded counters in state (`iteration_count`, `max_iterations`) with recorded stop reason. Idempotency (A5) and natural uniqueness constraints make node re-execution safe.
- Backtracking: a protocol/criteria change creates a **new** `protocol_versions` row; an impact service computes affected decisions/artifacts and marks them `stale`; the graph is re-entered at the earliest impacted node. Nothing is silently overwritten (rule 16).
- Graph modules for MVP (spec §5.1): Protocol/Search, Screening, Extraction/Appraisal, Synthesis/Writing, Audit/Verification.
- *Rejected:* storing business data only in graph state (not queryable, not migratable); a custom state machine (re-implements checkpoint/interrupt).

### D4. Jobs: Celery + Redis as transport; **Postgres `jobs` table as truth** **[ADDITION]** — idempotency design superseded by **A5**
Redis can be flushed/restarted; job status, progress, cancellation flags, attempt counts, and idempotency keys must survive. Each job row records type, project, status, progress (0–100 + message), attempts, `idempotency_key` (unique per project+type), error summary, timestamps. A recovery sweep re-enqueues `queued`/stale-`running` jobs. Cancellation is cooperative (flag checked between batches); only "safe" job types are cancellable. *Rejected:* ARQ/Dramatiq (smaller ecosystem than Celery; spec names Celery); FastAPI BackgroundTasks (dies with the process).

### D5. Provider interfaces (rule 6)
`providers/<kind>/base.py` defines a `Protocol`/ABC; adapters are selected by config via a registry; services receive providers by constructor injection.

| Kind | Interface | MVP adapters | Test adapter |
|---|---|---|---|
| LLM | `LLMClient.generate_structured(prompt, schema, settings) -> (parsed, ModelRunInfo)` | Gemini | `FakeLLM` (canned/recorded) |
| Scholarly | `ScholarlySource.search(query, filters, cursor) -> Page[RawRecord]`, `build_query(protocol)`, `capabilities` | OpenAlex + one of Europe PMC / arXiv (**discipline-dependent, §8**); Crossref for DOI resolution | recorded-fixture adapters |
| Web search | `WebSearchProvider` | none until a provider is chosen; flag-off by default | fake |
| Storage | `ObjectStore.put/get/delete/presign` | local FS | in-memory |
| Email | `EmailSender` | console (dev), SMTP | in-memory |
| Auth | `AuthProvider` (see D7) | local password+session | fake |

Prompts live in `workflows/prompts/` as versioned files (`<name>.v<N>.md`); the loaded version string is recorded on every `model_runs` row. The Gemini model ID is **configuration with no default** (`LLM_MODEL`) because availability must be verified at implementation time (spec §19).

### D6. Idempotency and provenance
- Natural uniqueness constraints (e.g. `paper_source_records(search_run_id, provider, provider_record_id)`, `screening_decisions` by `(paper_id, stage, protocol_version_id, reviewer, supersedes)`), plus `jobs.idempotency_key`. Retrying a task never duplicates records.
- **Evidence status is an explicit enum**, never null-as-unknown: `extracted | not_reported | unclear | not_applicable | not_assessed | source_unavailable`. *`source_unavailable`* (no full text obtained) is deliberately distinct from *`not_reported`* (full text read, value absent) — this is the "unavailable vs negative evidence" rule (rule 9).
- Every `evidence_items` row with a value requires `document_id + page + excerpt` (or an explicit reason it can't have one); a deterministic validator confirms the excerpt actually occurs in the stored page text before the value can be marked `extracted`. LLM-supplied quotes that don't match are rejected, not repaired.
- PRISMA counts are a **fold over append-only `record_status_events`**, validated by invariants (D8). No count is typed by a human or produced by an LLM.

### D7. Authentication: self-managed, behind `AuthProvider` **[ADDITION to spec's "prefer maintained library"]**
Argon2id via `argon2-cffi`; opaque random session tokens stored **hashed** in `auth_sessions`; HttpOnly + SameSite=Lax + Secure(non-dev) cookie; CSRF defence by Origin/Referer check on state-changing requests plus a custom header (SPA same-origin proxy makes this tractable); login/reset rate limits; password reset via single-use hashed tokens with TTL and an `EmailSender`. Ownership checked server-side in a single `get_owned_project` dependency used by every project-scoped route.
*Alternatives:* Auth.js / external IdP (adds a second identity store and cookie/JWT validation seam in FastAPI); `fastapi-users` (**maintenance status unverified offline** — check before adopting). The `AuthProvider` interface keeps an IdP swap cheap.

### D8. PRISMA & screening model **[ADDITION: `record_status_events`]** — corrected by **A6** (see `prisma-2020-validation.md`)
Each paper has a current status derived from the latest event in a small, validated state machine: `identified → deduplicated|duplicate_removed → ta_screened → excluded_ta | sought → not_retrieved | ft_assessed → excluded_ft(reason) | included`, with source tags (database, web search, manual, citation chasing). Transition legality is enforced in code; counts are computed per `protocol_version` and per source; the flow diagram refuses to render until invariants hold (e.g. screened ≤ deduplicated, Σ exclusions + included = assessed). Warnings are displayed, not hidden. Spec §3.6.

### D9. Frontend
Next.js App Router + TypeScript, Tailwind, shadcn/ui (Radix primitives, owned components), TanStack Query for server state, Zustand only for UI state (panel sizes, active tab). **Same-origin**: `next.config` rewrites `/api/*` to FastAPI so cookies are first-party and CORS stays an allowlist rather than a dependency. API types generated from FastAPI's OpenAPI (`openapi-typescript`) so UI/API drift fails CI. SSE for job/chat progress with polling fallback. Playwright for E2E. Every UI action must hit a real endpoint (rule 11); anything demo is labelled "Demo data".

### D10. Documents
PyMuPDF for page-aware text → `document_pages(document_id, page_no, text, text_version)` (kept out of hot rows). OCR (Tesseract is present in this sandbox; **deployment image must install it itself**) only when a page has no usable text layer and `FEATURE_OCR=true`. Upload validation: extension + declared MIME + magic bytes (`%PDF-`) + size cap + safe generated storage key (never the user's filename) + SHA-256. Parsing runs in a worker with a timeout; documents are untrusted input.

### D11. Prompt-injection posture
Paper/abstract/PDF text is passed as clearly delimited *data*; LLM calls in extraction/screening have **no tools**; outputs are schema-validated; only service functions write to the DB. The chat/planner agent's tool set is a fixed allowlist of service calls that cannot read other projects.

### D12. Observability & privacy
Structured JSON logs with correlation ID per request/job/graph run; a redaction filter for secrets/tokens; never log full PDFs or chat bodies (IDs, counts, timings, sanitized errors only). Health endpoints: `/healthz` (liveness), `/readyz` (DB + Redis, no model calls).

### D13. Tooling
Python 3.12 (present in sandbox), `uv` or `pip-tools` for locking (decide in Phase 1 based on availability), ruff + mypy (strict on `app/`), pytest + pytest-cov. Node 22, npm, ESLint, `tsc --noEmit`, Playwright. CI = same commands as local (`scripts/check.sh`). **All third-party versions are unverified in Phase 0** (no network) and must be pinned from the registry in Phase 1.

## 4. Data model
See `erd.md`. Adds to the spec's §8 table list: `auth_sessions`, `password_reset_tokens`, `jobs`, `protocol_questions`, `protocol_criteria`, `dedupe_candidates`, `screening_decision_criteria`, `record_status_events`, `document_pages`, `evidence_item_revisions`, `question_coverage`, `screening_rounds` is **not** added (decisions carry `stage` + `protocol_version_id`; YAGNI).

## 5. Local development and deployment configuration
- **Local:** `docker-compose.yml` → `web`, `api`, `worker`, `postgres`, `redis`; local-FS storage volume; email = console. One-command bring-up and `make`/script entrypoints for migrate, seed (clearly labelled demo seed, off by default), test, lint.
- **Staging/Production:** three container images (web, api, worker); managed Postgres + S3-compatible storage + managed Redis if available; secrets from the platform secret store; `COOKIE_SECURE=true`; strict CORS allowlist (empty if same-origin). API stateless, horizontally scalable; workers scaled independently. Hosting provider is **not** chosen yet (spec §19).
- Config is 12-factor: `pydantic-settings` reads env; startup fails fast on missing required settings in non-dev. See `environment-configuration.md`.

## 6. Scientific-integrity constraints baked into the architecture
1. No auto-exclusion from an AI score; AI returns `include | exclude | uncertain` and uncertain stays visible. Final inclusion/exclusion is a human decision record.
2. "Estimated relevance", not probability, until calibrated on a labeled set.
3. Title/abstract and full-text assessments are separate stages with separate decision rows.
4. Protocol approval is an immutable version with approver + timestamp; search cannot start without it (enforced in the service, not the UI).
5. Partial provider failure ⇒ search run status `partial`, surfaced in UI and PRISMA notes; never reported as complete.
6. Single-reviewer limitation: the spec has one human per project. Cochrane-style practice uses two independent screeners; this is documented as a **known methodological limitation** in exports/audit, and the schema stores `reviewer_id` so dual screening can be added later without migration pain.

## 7. Risks (ranked)

| # | Risk | Impact | Mitigation / owner |
|---|---|---|---|
| R1 (mitigated by A1) | **Build/test environment lacks network, Docker, Postgres, Redis; no Python/JS deps installed.** Integration, workflow and browser tests cannot run in the current sandbox. | Rules 13–14 (tests must actually run) can't be satisfied for most of the stack here. | Needs decision §8-Q1 before Phase 1. Every report will list tests *run* vs *not run* with commands. |
| R2 | Package/model/API facts unverifiable offline (versions, Gemini model names/quotas, OpenAlex auth/rate rules) | Wrong pins / broken adapters | Verify at Phase 1/3 start; keep all of them in config; adapter contract tests with recorded fixtures. |
| R3 | Free-tier LLM data-use terms may permit provider use of submitted content. **Unverified.** | Privacy: private PDFs/abstracts leaving the user's control | `LLM_SEND_PRIVATE_DOCUMENTS=false` by default; verify terms before enabling; surface consent in UI. |
| R4 | Sync-core decision (D2) diverges from spec's async emphasis | Rework if heavy in-request I/O appears | Keep I/O in workers; revisit at M2 with measurements. |
| R5 | LangGraph interrupt/resume semantics & checkpointer API differ by version | Resume bugs, crash-recovery failures | Spike in Phase 2 with a crash/resume test *before* building more nodes; pin versions. |
| R6 | Dedup false merges (preprint vs published; same title different works) | Lost/merged studies → wrong PRISMA | Conservative auto-merge (exact DOI / very high match), everything else → `dedupe_candidates` for review; false-merge unit tests. |
| R7 | LLM hallucinated quotes/values/citations | Scientific invalidity | D6 excerpt-must-exist validator; citations resolve only via `paper_records`; claim–evidence links required. |
| R8 | Scope: spec is large (M0–M8) | Many half-features | One thin vertical slice per phase (rule 17); meta-analysis, extra providers, OCR tuning deferred. |
| R9 | Target discipline not chosen | Defaults/adapters/appraisal frameworks unjustified | §8-Q2; no universal-coverage claims. |
| R10 | PDF parsing hostile inputs / resource exhaustion | DoS/security | size caps, page caps, worker timeouts, parse in worker, no execution of embedded content. |
| R11 | Frontend/API drift | Broken UI actions | OpenAPI-generated client + CI check. |

## 8. Open questions for the product owner
**[RESOLVED → A1] Q1 — test environment.** Which applies? (a) network access will be enabled for the build sandbox so dependencies can be installed and an embedded/local Postgres + Redis (or equivalents) set up — I'd then run what's feasible and say precisely what isn't; (b) code is produced here and you run tests locally/CI with Docker, and I label all such tests **"written, not run"**.
**[RESOLVED → A2] Q2 — target discipline** (biomedical → PubMed/Europe PMC + RoB tools; CS/AI → arXiv + OpenAlex). Provisional: OpenAlex + Europe PMC.
**[RESOLVED → A3] Q3 — D2/D7 accepted provisionally.**
**[RESOLVED → A4] Q4 — LLM data terms:** may private PDFs be sent to the chosen LLM tier at all?

## 9. Consequences
Positive: one deployable codebase, one DB for everything including checkpoints, single driver, testable provider seams, honest provenance model. Negative: sync core limits per-process concurrency (scale via worker count); Celery adds Redis operational surface; self-managed auth carries security responsibility.


---

# Amendments (2026-10-20, product-owner decisions)

## A1. Testing policy
Option (b) applies unless network access **and** the required services (real PostgreSQL, Redis, package registries) are *demonstrably* available in the authoring environment; availability is re-checked at the start of every phase and the evidence recorded in the phase report.
Every report classifies each test as exactly one of:
- **Executed** — run in this environment; result and runner stated (e.g. "stdlib shim", not "pytest").
- **Written, not run** — exists in the repo, could not be executed here; must not be described as passing.
- **Could not run** — cannot run anywhere yet (e.g. needs infrastructure that does not exist), with the reason.
Skipped tests are shown as skipped. Integration tests skip loudly when `TEST_DATABASE_URL` is unset.
Tests that need no third-party packages are marked `# stdlib-only` and can be executed by `scripts/run_stdlib_tests.py`; CI runs the same files under pytest.

## A2. Research sources (discipline-agnostic)
No discipline is assumed anywhere in code or defaults. The first two adapters are **OpenAlex** and **Europe PMC** behind the `ScholarlySource` interface (D5); further sources (e.g. arXiv, PubMed, Crossref, Semantic Scholar) are added as adapters without changing services. Adapters declare `capabilities` (filters, abstract availability, OA fields, pagination style), and the UI shows which source returned what. Discipline-specific choices (appraisal frameworks, extraction defaults) are protocol content chosen by the user, never hard-coded. Europe PMC's biomedical bias is documented as a *coverage limitation* shown in search reports, not hidden.

## A3. Provisional acceptance of D2 (sync SQLAlchemy core) and D7 (self-managed auth)

| Decision | Benefits | Costs / trade-offs | Revisit trigger |
|---|---|---|---|
| D2 sync core | One repository/service code path for API, workers, tests; one driver (psycopg 3) shared with LangGraph checkpointer; simpler transactions | Each in-flight request occupies a threadpool thread; no async fan-out in handlers; scale via replicas/workers; sync Celery tasks block a worker slot | p95 latency/threadpool saturation in load tests (Phase 9); any need for high-concurrency streaming beyond SSE |
| D7 self-managed auth | No second identity store; full control of session revocation and audit; small dependency surface | We own security-critical code: password policy, rate limiting, CSRF, session lifecycle; **no email verification, no MFA, no password-reset email** (A8); registration reveals whether an email exists (409) | Before any public/multi-tenant launch: add email verification + MFA or move to an IdP via the `AuthProvider` interface |

**Security-test matrix** (status as of Phase 1; see the Phase 1 report for run evidence):

| Control | Test(s) | Status |
|---|---|---|
| Argon2id hashing, salts, no plaintext | `unit/test_passwords.py`, `integration/test_auth_flow.py::test_register_*` | Written, not run |
| Session tokens random, stored hashed, revocable, expiring | `unit/test_security_primitives.py` (token/hash) · `integration/test_auth_flow.py` (logout replay, expiry, hashed at rest) | Token/hash: **Executed (shim)**; rest: Written, not run |
| Cookie flags HttpOnly/SameSite/Secure policy | `integration/test_auth_flow.py::test_login_cookie_flags…`; `unit/test_settings_checks.py` (Secure required outside local) | Settings rule: **Executed**; cookie header: Written, not run |
| CSRF (custom header + Origin/Referer allow-list) | `unit/test_security_primitives.py::test_csrf_*` · `integration/test_auth_flow.py::test_csrf_*` | Logic: **Executed**; middleware wiring: Written, not run |
| Login/registration rate limiting | `unit/test_rate_limiter.py` · `integration/test_auth_flow.py::test_login_is_rate_limited…` | Limiter: **Executed**; wiring: Written, not run. Redis limiter: **no test yet** |
| Credential-enumeration resistance (same error, one Argon2 verify on unknown user) | `integration/test_auth_flow.py::test_wrong_password_and_unknown_email…` | Written, not run; timing equivalence **not measured** |
| Server-side ownership on every project/job route; 404 for foreign vs missing | `integration/test_projects_and_ownership.py` (routes ×2 users, anon, job attach) | Written, not run |
| No secrets/free text in logs and audit | `unit/test_logging_redaction.py` · `integration/test_audit.py` | Logs: **Executed**; audit: Written, not run |
| Validation errors don't echo secrets | `integration/test_auth_flow.py::test_validation_errors_never_echo…` | Written, not run |
| Audit append-only (UPDATE/DELETE/TRUNCATE) | `integration/test_audit.py::test_audit_table_is_append_only…` | Written, not run |
| SQL injection resistance (ORM bound parameters) | `integration/test_projects_and_ownership.py::test_sql_metacharacters…` | Written, not run |
| Unsafe production config refused at startup | `unit/test_settings_checks.py` | Pure rule: **Executed**; `Settings` validator: Written, not run (needs pydantic) |

Known gaps (not tested, documented): breached-password screening; account lockout beyond rate limiting; session rotation on privilege change; IP trust behind proxies (`TRUST_FORWARDED_HEADERS` default false); CSRF for non-browser clients relies on the custom header alone; no dependency vulnerability scan yet; no penetration test.

## A4. Privacy: private-document transmission
Disabled by default; four gates (operator flag, reviewed provider-terms record, per-project revocable consent, per-call data-class logging), all enforced in a backend gateway. See `provider-data-handling.md`. The provider-terms record is **blank/not reviewed**; no claim about any provider is made. Phase 1 contains no LLM code.

## A5. Job idempotency redesign
The Phase-0 constraint `unique(project_id, type, idempotency_key)` is **withdrawn**: it would block legitimate repeated searches and reruns forever. Replaced by three separate mechanisms (documented in `app/jobs/fsm.py`, implemented in migration `0001_baseline`):
1. **Request replay safety:** optional `Idempotency-Key` header → `request_key`, unique per `(owner_id, request_key)` (partial index). Same key + same parameters returns the *same* job (HTTP 200); same key + different parameters → 409 `idempotency_key_reuse`. A new key is a new intentional request.
2. **No duplicate in-flight work:** `input_fingerprint` (SHA-256 of canonical JSON of type/project/input) is unique **only among `queued`/`running` jobs** (partial expression index). Once a job is `succeeded`, `failed` or `cancelled`, an identical job may be started again. Concurrent identical request → 409 `job_already_active` with the existing job id.
3. **At-least-once delivery / crashes:** compare-and-set claim (`queued→running` only if still queued), heartbeat, and a recovery sweep (stale `running` → `queued` while attempts remain, else visible `failed`; `queued` jobs whose message may never have been sent are re-dispatched). Duplicate or late deliveries are no-ops. Later phases add natural unique keys on data written by jobs.
Rerun lineage: `rerun_of_job_id`. `awaiting_input` (for graph approvals) is deliberately **not** "active" for mechanism 2.
Regression tests: `unit/test_job_fsm.py` (executed), `integration/test_jobs.py::test_LEGITIMATE_RERUNS…` and `integration/test_migrations_and_health.py::test_job_idempotency_indexes…` (written, not run). Mutation check confirmed the unit test fails if terminal states are added to the constrained set.

## A6. PRISMA 2020 validation
The event model was reviewed against the PRISMA 2020 flow-diagram structure; eight gaps were found and corrected (records/reports/studies, three removal categories, two pathways, identification counted pre-dedup per source, pending buckets, no AI-driven "automation" removals, retrieval states, controlled exclusion reasons) and eleven reconciliation invariants defined. See `prisma-2020-validation.md`. **Caveat:** checked against my recollection of the guidance, not the live official source; a human verification step with date is required before Phase 6 sign-off (blank record in that file).

## A7. LangGraph checkpoint / interrupt / resume — early integration gate
**Scheduling interpretation (please confirm):** the instruction to implement this early and the instruction to implement Phase 1 only are reconciled by making it the **first deliverable of Phase 2**, ahead of any other graph node, with Phase 1 supplying the job-level foundations it needs (jobs table, heartbeat, recovery sweep, claim). It is *not* implemented in Phase 1: LangGraph is not installable in the current environment (A1), so any such test would be unexecutable here.
Required test specification (real PostgreSQL checkpointer, real Celery worker **process**, no mocks of the graph runtime):
1. Run → graph reaches `interrupt` → `workflow_runs.status = awaiting_input` with a typed pending question; the worker task has ended (no worker held).
2. Discard all in-process objects/connections (simulate API+worker restart) → `resume(payload)` → graph continues from the checkpoint and completes; side effects occur exactly once.
3. `SIGKILL` the worker mid-node → heartbeat goes stale → sweep re-queues → graph resumes from the last checkpoint; the interrupted node's side effects are idempotent (no duplicate rows).
4. Duplicate resume (same payload twice) is a no-op; resume with a payload failing schema validation is rejected and the run stays `awaiting_input`; resume of a non-awaiting run is rejected.
5. Checkpoint/table disagreement ⇒ run marked `needs_attention`, tables win.
6. Retry/iteration caps stop with a recorded reason; exceeding `max_attempts` ends `failed`, never loops.
Exit gate: all six pass on real Postgres in CI before Phase 2 adds further nodes.

## A8. Password recovery — **explicitly deferred**
No email infrastructure exists, so a self-service reset flow cannot be configured or tested; building it would produce a non-functional feature. Therefore:
- `password_reset_tokens`, `EmailSender`, SMTP settings, and reset endpoints/UI are **not built** and no "Forgot password" link is shown. The login page states recovery is unavailable.
- **Operator-assisted recovery exists and is real:** `python -m app.cli set-password EMAIL` (password via stdin/prompt, never argv) updates the hash, **revokes all of the user's sessions**, and writes an audit event.
- Limitation (documented in README): a locked-out user must contact the operator. Email-based recovery will be built together with email verification (needs provider, SPF/DKIM, templates, single-use hashed tokens with TTL, rate limits, and tests) in a later phase; until then it must not be presented as complete.

## A9. Client-IP derivation for rate limiting (found in Phase 1 validation review)
Phase-1 code keyed per-IP limits on the TCP peer (a single global bucket behind the Next.js proxy) and offered a boolean that trusted the *first* `X-Forwarded-For` entry (client-controlled). Replaced by `TRUSTED_PROXY_HOPS=N`: the client is the N-th entry from the right; shorter chains, non-IP values, or N=0 fall back to the TCP peer (`app/core/net.py`). Status: logic **executed** (7 unit tests + mutation check); runtime behaviour through Next.js **unverified** until the `ip-isolation` drill passes. See `production-readiness-review.md` §2.


### Amendment A9 — nginx edge proxy in front of web and API (2026-10-23)

**Context.** The `ip-limit` and `ip-isolation` drills failed in CI. Verified on Next.js 15.5: rewrites forward a
client-supplied `X-Forwarded-For` verbatim and add nothing. With `TRUSTED_PROXY_HOPS=1` the API therefore (a) used
a forged address as the per-IP key (limit bypass) and (b) without the header saw every user as the web container
(one global bucket: about 10 bad logins per minute from anyone lock out all logins). The assumption that the
Next.js proxy is a trustworthy hop, marked "unverified until `ip-isolation` passes" above, was wrong.

**Decision.** An nginx service (`infra/proxy/nginx.conf`) is the only published port of the compose stack. It
overwrites `X-Forwarded-For` with `$remote_addr` and routes `/api/*` directly to the API; everything else goes to
Next.js, whose port is no longer published. The API port is bound to 127.0.0.1. `TRUSTED_PROXY_HOPS` stays 1, now
meaning the edge proxy. `npm run dev` without Docker keeps the Next.js rewrite with `TRUSTED_PROXY_HOPS=0`
(single developer, global bucket acceptable).

**Alternatives rejected.** (1) A Next.js route-handler proxy that sets the header: Next 15 removed `request.ip`,
so a handler has no trustworthy client address. (2) Trusting `X-Real-IP` from Next: not set either. (3) Dropping
per-IP limits and keeping only per-account limits: leaves credential-stuffing across accounts unthrottled.

**Production.** Use the platform load balancer or this proxy as the single ingress; set `TRUSTED_PROXY_HOPS` to the
real number of header-setting hops; never expose the API port directly, because a direct caller can forge the header.
Guarded by `tests/unit/test_edge_proxy_config.py` and the two drills.


### Amendment A10 — Review workflow and protocol (Phase 3, 2026-10-23)

**A10.1 Human answers are applied by the API, not by graph nodes.** `POST /projects/{id}/workflow/resume` validates
the answer, applies it through `protocol_service`, records it once in `workflow_events`, writes the chat message and
queues `workflow.advance`, in one transaction. The graph is then resumed with a compact *routing* payload. Nodes build
a deterministic question, call `interrupt()` and route; they write nothing. *Why:* LangGraph re-runs an interrupted
node from the top on resume and a worker can die anywhere, so any write in a node can happen twice; invalid answers are
rejected with a 422 before anything moves (A7.4). *Rejected:* applying answers inside nodes with idempotency keys
(works, but every node then needs its own replay analysis, and validation errors surface only asynchronously).

**A10.2 Deterministic question ids** = uuid5(thread, step, node). The worker writes the chat message for a question
with that id as `client_message_id`, so a replay cannot duplicate it.

**A10.3 Tables win, concretely.** Before advancing, the worker compares the checkpoint's `protocol_version_id` with
the run row (for a revision: with the version being revised). A mismatch marks the run and project `needs_attention`
and does not touch the graph. *Recover* starts a **new** checkpoint thread whose position is derived from the tables
(draft exists -> review; only approved -> idle at its stage); old checkpoints are kept, not deleted.

**A10.4 Spec route names.** The spec lists `/workflow-runs/{id}/resume` and `/projects/{id}/protocol/approve`.
Implemented as project-scoped routes (`/projects/{id}/workflow/...`) so every call passes the single
`get_owned_project` choke point; approval is the `approve` answer to the review question (one path, recorded with the
workflow). Route names may evolve per spec §9.

**A10.5 No LLM in Phase 3.** The framework advisor is a transparent keyword rule (`KeywordFrameworkAdvisor`, labelled
"not an AI judgement" in the UI) behind a `FrameworkAdvisor` protocol; criteria/question suggestions only restate the
user's own answers and start as *proposed*. The LLM gateway (A4 consent gates, model_runs) is deferred to the first
phase whose feature needs a model. *Rejected:* building the gateway now with no caller (untestable end to end, and a
fake "assistant" would contradict rule 11).

**A10.6 One workflow run per project**, `max_iterations` copied onto the run, debug node delay as a settings-guarded
test hook. Checkpointer: `PostgresSaver` on its own autocommit psycopg connection with `search_path=lg_checkpoints`;
`setup()` once per process under a Postgres advisory lock.

**A10.7 Versions pinned.** `langgraph==1.2.14`, `langgraph-checkpoint==4.2.0`, `langgraph-checkpoint-postgres==3.1.2`;
all new transitive pins resolved with `--uploaded-prior-to 2026-10-09` (>= 14 days old). `langgraph-sdk` requires
`websockets<17`, so `websockets` moved 17.3 -> 16.1.1. Spike results that the design relies on (real Postgres): a
resume value survives a crash mid-node; resuming a non-interrupted thread is a no-op; new input on a finished thread
restarts at START. A7 status: **all six gate tests pass** (`tests/integration/test_langgraph_gate.py`).
