# Iteration 001 — Phase 1: Foundation, auth, projects, audit, jobs

**Bottom line:** the code is written and statically checked; **the application has never been started.** The sandbox had no network, Docker, PostgreSQL or Redis, and none of FastAPI/SQLAlchemy/Celery/Next.js could be installed (re-verified at the start of this phase). Per ADR A1 (option b) every test is classified below. Nothing unexecuted is reported as passing. Phase 1 is therefore **not accepted as complete** until the "written, not run" tests are executed by you or CI and pass.

## 1. Environment evidence (A1)
`pip download fastapi` → "No matching distribution" (DNS failure); `npm view next` → no response; `docker`, `psql`, `postgres`, `redis-server` absent; none of fastapi/sqlalchemy/alembic/pydantic/celery/redis/psycopg/pytest/httpx/argon2 importable. Available: Python 3.12.3, Node 22, git, PyYAML, `tsc` 6.0.3. → Option (b).

## 2. What was implemented
| Area | Implementation |
|---|---|
| Backend | FastAPI app factory; sync SQLAlchemy 2 + psycopg 3; layering routes → services → repositories; typed settings with fail-fast outside local envs |
| Auth | Register/login/logout/me; Argon2id; opaque 256-bit session tokens stored as SHA-256; HttpOnly SameSite=Lax cookie (Secure via config); CSRF = custom header + Origin/Referer allow-list; per-account + per-client login rate limit (Redis impl + in-memory); constant-work login for unknown emails |
| Projects | Create/list/get/rename/archive/unarchive/delete; optimistic concurrency (`row_version`, 409 on conflict); immutable `topic_original`; ownership in every repository query; foreign and missing projects both return identical 404 |
| Audit | Same-transaction audit events for register/login ok+fail/logout/project lifecycle/job enqueue/cancel/recovery; no free text or raw email stored; DB triggers make the table append-only (UPDATE/DELETE/TRUNCATE) |
| Jobs | `jobs` table, Celery+Redis transport, `system.ping` job, CAS claim, heartbeat, cooperative cancel, recovery sweep (beat every 60 s), **A5 idempotency redesign** |
| Migrations | Alembic `0001_baseline`: users, auth_sessions, projects, audit_events (+triggers), jobs (+partial indexes); downgrade implemented |
| Health | `/healthz` (liveness), `/readyz` (DB+Redis), `/healthz/worker` (Celery ping) |
| Logging/errors | JSON logs with correlation IDs and recursive secret redaction; uniform error envelope; validation errors never echo submitted values |
| Frontend | Next 15 App Router: login, register, projects (create/archive/delete-confirm/show archived), project workspace shell (rename, overview), same-origin `/api` proxy, security headers, route gate middleware. Chat panel is a **labelled, disabled** placeholder |
| Ops | Dockerfiles (api, worker, web), `docker-compose.yml` (postgres, redis, one-shot migrate, api, worker, web), CI workflow (backend/web/e2e/docker-build), scripts, `.env.example`, README, runbook, dev-only seed CLI, operator password reset CLI |
| Docs | ADR **Accepted** with amendments A1–A8; ERD updated (31 entities); `prisma-2020-validation.md`; `provider-data-handling.md`; plan updated |

## 3. Tests — actual outcomes
| Class | Tests | Result |
|---|---|---|
| **Executed** — stdlib shim `scripts/run_stdlib_tests.py` (NOT pytest) | 26: log redaction (5), security primitives incl. CSRF/token/email/password (9), rate limiter (2), settings safety (3), job FSM/fingerprint/recovery rules (7) | **26 passed, 0 failed** |
| **Executed** — mutation check of the above | 8 deliberate bugs injected one at a time (stop redaction, accept any CSRF origin, allow terminal→running, constrain `failed` in the in-flight index, order-sensitive fingerprint, infinite retry, min password length 1, disable rate limiter) | **8/8 detected** (each made ≥1 test fail); source restored, 26/26 pass again |
| **Executed** — `scripts/validate_static.py` | 22 checks: syntax of 64 Python files; internal `app.*` import resolution; unused imports (approx.); `.env.example`↔`Settings` consistency; YAML (compose, CI); JSON configs; `bash -n` ×4; `node --check` ×2; TypeScript **syntax** of 18 files | **22/22 passed** after fixing 3 real defects it found (missing `app/api/__init__.py`, 8 unused imports) |
| **Executed** — `scripts/check_erd.py` | ERD structural check | pass: 31 entities, 47 relationships, 0 errors |
| **Written, not run** — pytest unit | `test_passwords.py` (4) | needs argon2-cffi |
| **Written, not run** — pytest integration | 50 test functions (+3 parametrized) in `tests/integration/`: auth flow & security (cookies, revocation, expiry, enumeration, CSRF, rate limit, error envelope, no secret echo), projects & ownership (every route × two users, anonymous, foreign vs missing, SQL metacharacters, optimistic concurrency), audit (events, no free text, append-only triggers), jobs (replay, key reuse, in-flight duplicate, **legitimate reruns after succeeded/failed/cancelled**, CAS claim, broker outage, worker-crash recovery, attempt cap, cancel, ownership), migrations (up/down/up, partial index definitions, constraint names, model↔schema columns), health | need PostgreSQL (+Redis for `/readyz` redis check). Show as **SKIPPED** without `TEST_DATABASE_URL` |
| **Written, not run** — vitest | 4 (`api.test.ts`) | needs `npm install` |
| **Written, not run** — Playwright | 5 (`auth-projects.spec.ts`: gate, full lifecycle, wrong password, cross-user URL, recovery-not-advertised) | needs full stack + Chromium |
| **Not run, no tests exist** | ruff, ruff format, mypy (strict), `next lint`, `tsc` **type** checking, `next build`, Docker image builds, `docker compose up`, CI workflow itself | tools/infrastructure unavailable. **Expect first-run failures** here (mypy strict and ruff `S`/`BLE` rules in particular) |
| **Could not run / does not exist** | coverage figures (no coverage run possible); Redis rate-limiter test; auth-timing measurement; load/resilience tests; accessibility audit (axe); LangGraph tests (Phase 2, A7) | no coverage numbers exist to report |

## 4. Architecture/design decisions made in this phase (beyond the ADR)
- **Celery tasks live in `app/jobs/`**, not `services/worker/tasks/`: one importable package for API and worker avoids path hacks; the worker is still a separate process (rejected: duplicate package layout).
- **Audit has no foreign keys and stores no free text** so it can be append-only *and* survive project deletion without retaining personal text (rejected: cascade-deleting audit rows; storing titles in the log).
- **404 for foreign projects** instead of 403 (no existence leak). Cost: slightly harder debugging.
- **Operator CLI for password reset** because recovery email is deferred (A8); resets revoke all sessions.
- **Rate limiter fails open** if Redis is unavailable (availability over lockout; logged). Documented trade-off.
- **Worker health is a separate endpoint** from `/readyz` so a worker outage doesn't remove the API from rotation.
- **Sessions are fixed-lifetime** (7 days) with server-side revocation; no sliding renewal yet.

## 5. Known bugs / risks / limitations
**Unverified, high likelihood of needing fixes on first run:** dependency lower bounds never resolved; SQLAlchemy `Index(...)` mixing string columns with `text()` expression in `Job.__table_args__`; returning the injected `Response` from the 204 logout route; Alembic naming-convention interaction (mitigated with `op.f()` for CHECK names); `celery send_task(retry=False)` behaviour when the broker is down; Next 15 + ESLint 8 (`next lint`) pairing; mypy strict on SQLAlchemy `Mapped` helpers.
**Security / product limitations:** no email verification, MFA or recovery email; registration reveals whether an email exists; no breached-password check; Redis limiter untested; behind a proxy all clients share one IP unless `TRUST_FORWARDED_HEADERS` is set correctly; sessions not rotated on password change except via CLI reset; no dependency vulnerability scan; no pen-test; account deletion not implemented.
**Process:** no lockfiles; no OpenAPI→TS generation (API types hand-written, drift possible); CI workflow has never run; compose stack never started.
**Spec deviations:** sync core (D2), tasks location (above).

## 6. Migrations, configuration, deployment implications
- Migration `0001_baseline` (applies clean-DB only; downgrade drops everything incl. trigger function). Requires PostgreSQL ≥ 13 features used: partial/expression unique indexes, JSONB, plpgsql triggers (tested target: 16, untested).
- Config: new/changed variables listed in `.env.example`; `POSTGRES_PASSWORD` must match `DATABASE_URL`; production refuses to start with placeholder secrets, `COOKIE_SECURE=false`, wildcard CORS (rule executed in unit test; the pydantic wiring is not run).
- Deployment: three processes (web, api, worker); run **exactly one** beat (worker is started with `-B` for dev; split beat out before scaling workers); run migrations as a one-shot before rollout.

## 7. Manual verification
See `docs/runbooks/local-development.md` §3 (20 numbered checks: health, account/project flow, cross-user access, cookie flags, audit append-only, argon2 hashes, migration round-trip, job replay/rerun/duplicate, **worker SIGKILL recovery**, CSRF/rate-limit/log-redaction).

## 8. Recommended next step
1. **Run it** (you or CI with network): `pip install -e ".[dev]"`, fix dependency/lint/type issues, commit lockfiles, then `scripts/test-integration.sh`, `scripts/check.sh`, `scripts/test-e2e.sh`, and the manual checks. Report failures; I will fix them before any new feature work.
2. Then **Phase 2** (chat + protocol), whose first deliverable is the LangGraph checkpoint/interrupt/resume gate (A7).
