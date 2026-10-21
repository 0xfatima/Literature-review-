# Systematic Review Scientist

Human-in-the-loop systematic-review workbench (protocol → search → screening → PDFs → evidence extraction → synthesis → draft) with provenance and an audit trail.
Source of truth: `docs/requirements/`. Architecture: `docs/architecture/ADR-0001-initial-architecture.md` (Accepted, with amendments A1–A8).

**Status: Phase 1 (foundation) written. Registration/login, projects, ownership enforcement, migrations, audit events, job queue/worker, health checks, Docker Compose, CI.**
**The authoring environment could not install packages or run PostgreSQL/Redis/Docker, so most of this code has been written but NOT executed.** Exactly what was and was not run is in `docs/implementation-reports/iteration-001.md`. Do not assume anything passes until you (or CI) have run the steps below.

## Quick start (Docker Compose)

Prerequisites: Docker with Compose v2, Node 22 (only for running browser tests), git.

```bash
git clone <repo-url> && cd systematic-review-scientist
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, and use the SAME password inside DATABASE_URL
docker compose up --build            # postgres, redis, migrate (one-shot), api, worker, web
```
- Web: http://localhost:3000 · API docs: http://localhost:8000/docs · Health: `/healthz`, `/readyz`, `/healthz/worker`
- Migrations run automatically via the one-shot `migrate` service. Manually: `docker compose run --rm migrate alembic upgrade head`.
- Optional **development-only** demo data (clearly labelled "[Demo data]"; refuses unless `APP_ENV=development`):
  `docker compose run --rm api python -m app.cli seed-dev` → sign in with `DEV_SEED_EMAIL` / `DEV_SEED_PASSWORD` from `.env`.

## Local (no Docker for the app)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d postgres redis          # or your own Postgres 16 + Redis 7
(cd services/api && alembic upgrade head)
(cd services/api && uvicorn app.main:app --reload)                       # terminal 1
(cd services/api && celery -A app.jobs.celery_app:celery_app worker -B -l INFO)   # terminal 2
(cd apps/web && npm install && npm run dev)                              # terminal 3
```

## Validation (Phase 1 is NOT yet validated)
Lockfiles do not exist yet: run the **Generate lockfiles (manual)** workflow (or `scripts/lock.sh` locally with network), commit the result (CI fails without it, by design), then `python scripts/validate_all.py` (full run with evidence in `validation-evidence/`; fails on any skipped test). See `docs/implementation-reports/validation-001.md` and `docs/runbooks/supported-runtimes.md`.

## Checks and tests

| Command | What it runs | Needs |
|---|---|---|
| `scripts/check.sh` | ruff, ruff format, mypy, pytest unit, web lint/typecheck/vitest | `pip install -e ".[dev]"`, `npm install` in `apps/web` |
| `python scripts/run_stdlib_tests.py` | the 33 tests marked `# stdlib-only` (no packages) | Python only |
| `python scripts/validate_static.py` | syntax / import / config-consistency checks | Python (+ `tsc` optional) |
| `scripts/test-integration.sh` | DB-backed API, ownership, audit, jobs, migration tests | real PostgreSQL (`TEST_DATABASE_URL`; **drops schema `public`**) |
| `scripts/test-e2e.sh` | Playwright browser tests against the compose stack | Docker, Node, Chromium download |

Integration tests **skip** (visibly) without `TEST_DATABASE_URL`; a skip is not a pass. CI (`.github/workflows/ci.yml`) runs all of the above on every pull request.

## Password recovery (not available)
Self-service password reset is **deferred** (no email infrastructure; see ADR A8). The login page says so. Operator-assisted reset:
```bash
docker compose run --rm api python -m app.cli set-password user@example.com   # prompts; revokes all sessions
```

## Repository layout
`apps/web` Next.js UI · `services/api/app` FastAPI + services + SQLAlchemy + Celery tasks (`jobs/`) · `services/api/app/db/migrations` Alembic · `infra/docker` Dockerfiles · `docs/` requirements, architecture, runbooks, per-phase reports · `scripts/` dev/CI helpers.
Celery tasks live in `app/jobs/` (one package for API and worker) rather than `services/worker/tasks/` as sketched in the spec; the worker is a separate *process* from the same code.

## Known limitations (Phase 1)
No lockfiles yet (generate on first networked install, see runbook) · no email verification, MFA or password-reset email · registration reveals whether an email is registered · account deletion not implemented · no OpenAPI→TypeScript client generation yet (API types are hand-written) · no research features yet (protocol, search, screening, PDFs…) · the chat panel is a labelled, disabled placeholder.
