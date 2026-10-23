# Environment configuration plan

Principles: 12-factor; all secrets/URLs/model names/timeouts/flags via environment variables; `.env.example` holds names and placeholders only; `pydantic-settings` validates at startup and **fails fast** in `staging`/`production` if a required value is missing or unsafe (e.g. `COOKIE_SECURE=false`, a missing or placeholder `SESSION_SECRET`).

## Environments

| Concern | local (Docker Compose) | CI | staging / production |
|---|---|---|---|
| Processes | web, api, worker, postgres, redis | api tests + worker tests + web build/E2E | web, api, worker as separate containers |
| Postgres | compose service | service container (real PG, not SQLite) | managed Postgres, daily backups, **restore tested before launch** |
| Redis | compose service | service container | managed Redis |
| Storage | `local` adapter, volume `./storage` (git-ignored) | in-memory/tmp | S3-compatible bucket, private, server-side encryption, least-privilege key |
| LLM | `gemini` with a developer key, or `fake` | `fake` only (no live calls; live evals are opt-in/manual) | `gemini` or successor; quotas set from verified limits |
| Scholarly | live public APIs for manual dev; recorded fixtures for tests | recorded fixtures only | live, with contact email + rate limiters |
| Email | **not built** (password-reset deferred, ADR A8) | — | — |
| Cookies | `Secure=false` | n/a | `Secure=true`, HttpOnly, SameSite=Lax |
| CORS | allowlist `http://localhost:3000` (but same-origin proxy is the default path) | n/a | same-origin ⇒ empty or exact allowlist |
| Seeds | optional, labelled "Demo data", off by default | fixtures | never |

> **Phase 1 note:** only the variables present and uncommented in `.env.example` are read by code today; `scripts/validate_static.py` checks `.env.example` ↔ `Settings` consistency. LLM, scholarly, storage, upload and email variables from the Phase-0 draft are reserved for later phases (email/SMTP removed until A8 is lifted).

## Variable groups
See `.env.example` (authoritative list). Groups: App, Database, Redis/worker, Auth, Email, LLM, Scholarly, Web search (flag-off), Storage, Upload limits, Quotas/rate limits, Frontend-public.

Rules:
1. Only `NEXT_PUBLIC_*` may reach the browser, and it must never carry secrets.
2. `LLM_MODEL` has **no default**; the app refuses to run live LLM calls until it is set, because model availability/quotas must be verified (spec §19).
3. `LLM_SEND_PRIVATE_DOCUMENTS=false` by default (ADR R3).
4. `FEATURE_WEB_SEARCH=false` by default; per-project opt-in is additionally stored in the DB and audited.
5. Provider timeouts, retry caps and rate limits are config, not constants in code.
6. Secrets are never logged; the log redaction filter is unit-tested (spec §12.1).

### Review workflow (Phase 3)
| Variable | Default | Notes |
|---|---|---|
| `WORKFLOW_MAX_ITERATIONS` | 60 | Answered questions per checkpoint thread before the run stops with `stop_reason=max_iterations` (bounded loop, spec §5.2). Copied onto each run at creation. |
| `WORKFLOW_DEBUG_NODE_DELAY_S` | 0 | Test hook for the worker-SIGKILL gate test. Startup refuses a non-zero value outside development/test. |
| `LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2` | false | Not read by the app: read by `langchain-core` (a LangGraph dependency). Keep false so no workflow data leaves the deployment. |

LangGraph checkpoints live in schema `lg_checkpoints` of `DATABASE_URL` (created by migration 0003; tables created by the
library on first use under an advisory lock). Back it up with the database; it is not authoritative (tables win).

## Developer commands (exist as of Phase 1)
`scripts/check.sh` · `scripts/migrate.sh` · `scripts/test-integration.sh` · `scripts/test-e2e.sh` · `scripts/run_stdlib_tests.py` · `scripts/validate_static.py` (see README).

## Deployment checklist (stub, expanded in M8)
Secrets in secret store · HTTPS · cookie flags · CORS · migrations run before rollout · DB backup + restore drill · bucket policy · log redaction verified · quotas/alerts · health checks wired (`/healthz`, `/readyz`).
