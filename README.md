# Systematic Review Scientist

Human-in-the-loop systematic-review workbench (protocol → search → screening → PDFs → evidence extraction → synthesis → draft) with provenance and an audit trail.
Source of truth: `docs/requirements/`. Architecture: `docs/architecture/ADR-0001-initial-architecture.md` (Accepted, with amendments A1–A8).

**Status: Phase 1 (foundation).** Registration/login, projects, ownership enforcement, migrations, audit events, job queue/worker, health checks, Docker Compose, CI.
Lockfiles are committed. Backend lint/format/type checks, migrations and the full pytest suite (unit + integration on real PostgreSQL/Redis), and the web lint/format/typecheck/unit/build have been executed; see `docs/implementation-reports/validation-002.md` for exact results and for what has **not** yet been run (Playwright, Docker image builds, live drills: those run in CI).

## Quick start (Docker Compose)

Prerequisites: Docker with Compose v2, Node 22 (only for running browser tests), git.

```bash
git clone https://github.com/0xfatima/Literature-review-.git && cd Literature-review-
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD, and use the SAME password inside DATABASE_URL
docker compose up --build            # postgres, redis, migrate (one-shot), api, worker, web, proxy
```
- Web: http://localhost:3000 (served by the nginx `proxy`, which sends `/api/*` straight to the API) · API docs: http://localhost:8000/docs (bound to localhost only) · Health: `/healthz`, `/readyz`, `/healthz/worker`
- Why the proxy exists: Next.js rewrites pass a client-supplied `X-Forwarded-For` through untouched, which made the per-IP login limit spoofable and, without the header, global. See `infra/proxy/nginx.conf`.
- Migrations run automatically via the one-shot `migrate` service. Manually: `docker compose run --rm migrate alembic upgrade head`.
- Optional **development-only** demo data (clearly labelled "[Demo data]"; refuses unless `APP_ENV=development`):
  `docker compose run --rm api python -m app.cli seed-dev` → sign in with `DEV_SEED_EMAIL` / `DEV_SEED_PASSWORD` from `.env`.

## Local (no Docker for the app)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -c requirements.lock.txt -e ".[dev]"   # -c pins every package to the committed lockfile
cp .env.example .env    # set POSTGRES_PASSWORD and the same password in DATABASE_URL (host stays localhost)
docker compose up -d postgres redis          # or your own Postgres 16 + Redis 7
(cd services/api && alembic upgrade head)
(cd services/api && uvicorn app.main:app --reload)                       # terminal 1
(cd services/api && celery -A app.jobs.celery_app:celery_app worker -B -l INFO)   # terminal 2
(cd apps/web && npm ci && npm run dev)                              # terminal 3
```

The root `.env` is found by absolute path, so `alembic`, `uvicorn` and `celery` can be started from `services/api` as above. Real environment variables always override `.env`.

**Windows:** the repo enforces LF line endings via `.gitattributes`. If you cloned before that file existed, run `git add --renormalize . && git checkout -- .` once, otherwise shell scripts fail with `bash\r: No such file or directory`.

## Validation
Lockfiles are committed (`requirements.lock.txt`, `apps/web/package-lock.json`). To change dependencies, edit the manifests, run the **Generate lockfiles (manual)** workflow (or `scripts/lock.sh`) and commit the result; `python scripts/verify_locks.py` fails if they drift. `python scripts/validate_all.py` performs a full run with evidence in `validation-evidence/` and fails on any skipped test. See `docs/implementation-reports/validation-001.md` and `docs/runbooks/supported-runtimes.md`.

## Checks and tests

| Command | What it runs | Needs |
|---|---|---|
| `scripts/check.sh` | ruff, ruff format, mypy, pytest unit, web lint/prettier/typecheck/vitest | `pip install -c requirements.lock.txt -e ".[dev]"`, `npm ci` in `apps/web` |
| `python scripts/run_stdlib_tests.py` | the 38 tests marked `# stdlib-only` (no packages) | Python only |
| `python scripts/validate_static.py` | syntax / import / config-consistency checks | Python (+ `tsc` optional) |
| `scripts/test-integration.sh` | DB-backed API, ownership, audit, jobs, migration tests | real PostgreSQL (`TEST_DATABASE_URL`; **drops schema `public`**, so the fixture refuses any database whose name does not contain `test`) |
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
No email verification, MFA or password-reset email · registration reveals whether an email is registered · account deletion not implemented · no OpenAPI→TypeScript client generation yet (API types are hand-written) · no research features yet (protocol, search, screening, PDFs…) · the chat panel is a labelled, disabled placeholder.

