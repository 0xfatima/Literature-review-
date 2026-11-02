# Systematic Review Scientist

Human-in-the-loop systematic-review workbench (protocol → search → screening → PDFs → evidence extraction → synthesis → draft) with provenance and an audit trail.
Source of truth: `docs/requirements/`. Architecture: `docs/architecture/ADR-0001-initial-architecture.md` (Accepted, with amendments A1–A13).

**Status: Phase 15 (performance, concurrency and deployment) complete pending CI.** Fair job admission across users, cross-process limits that protect AI and scholarly provider quotas, Prometheus metrics, measured performance (`docs/performance.md`), a production Compose stack with TLS and Docker secrets, and a documented deploy with smoke test and backup/restore drill (`docs/runbooks/deployment.md`). Earlier phases: see `docs/implementation-reports/`.
AI help is optional and advisory: with `LLM_PROVIDER=none` (the default) every stage still works without a model, and the chat says the assistant is not connected. With a model configured (Gemini), AI suggests relevance, evidence values, appraisal judgements and synthesis statements; code checks each suggestion against the source text and a person decides. Private PDF text is sent only when all four gates in `docs/architecture/provider-data-handling.md` pass.

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
| `python scripts/run_stdlib_tests.py` | the 71 tests marked `# stdlib-only` (no packages) | Python only |
| `python scripts/validate_static.py` | syntax / import / config-consistency checks | Python (+ `tsc` optional) |
| `scripts/test-integration.sh` | DB-backed API, ownership, audit, jobs, migration tests | real PostgreSQL (`TEST_DATABASE_URL`; **drops schema `public`**, so the fixture refuses any database whose name does not contain `test`) |
| `pytest services/api/tests/integration/test_langgraph_gate.py` | ADR A7 gate: starts **real Celery worker processes** (Redis DB 9), restarts and SIGKILLs them | PostgreSQL + Redis, `celery` on PATH (installed with the package) |
| `scripts/test-e2e.sh` | Playwright browser tests against the compose stack | Docker, Node, Chromium download |

Integration tests **skip** (visibly) without `TEST_DATABASE_URL`; a skip is not a pass. CI (`.github/workflows/ci.yml`) runs all of the above on every pull request.

## Production deployment
See `docs/runbooks/deployment.md` (`scripts/gen-secrets.sh`, `scripts/deploy.sh`, `scripts/smoke.py`). Load testing: `scripts/loadtest/`, results in `docs/performance.md`.

## Backups and restore
`scripts/backup.sh [OUT_DIR]` (database + uploaded files + checksummed manifest) and `scripts/restore.sh BACKUP_DIR --yes`
(destructive). Retention periods, deletion semantics and the post-restore checklist: `docs/runbooks/data-retention-and-recovery.md`.

## Password recovery (not available)
Self-service password reset is **deferred** (no email infrastructure; see ADR A8). The login page says so. Operator-assisted reset:
```bash
docker compose run --rm api python -m app.cli set-password user@example.com   # prompts; revokes all sessions
```

## Repository layout
`apps/web` Next.js UI · `services/api/app` FastAPI + services + SQLAlchemy + Celery tasks (`jobs/`) · `services/api/app/db/migrations` Alembic · `infra/docker` Dockerfiles · `docs/` requirements, architecture, runbooks, per-phase reports · `scripts/` dev/CI helpers.
Celery tasks live in `app/jobs/` (one package for API and worker) rather than `services/worker/tasks/` as sketched in the spec; the worker is a separate *process* from the same code.

## Review protocol (Phase 3)
Open a project, choose the **Protocol** tab and **Start protocol setup**. Questions appear as cards in the *Protocol setup* chat thread; every answer is saved immediately, so you can sign out and continue at the same step later. The same draft can be edited field by field on the Protocol tab. Approval records approver, time and a SHA-256 of the exact content; an approved version can never be edited (database triggers enforce this). **Start a revision** (a reason is required) creates the next version; approving it shows which stages may need rerunning. The questions come from a fixed workflow, and the framework suggestion is a transparent keyword rule (not an AI judgement). After approval the workflow moves on to *Search*.

## Full-text screening and studies
The **Full text** tab lists every record that passed title/abstract screening. Read the PDF from the paper inspector, then **Include**, mark **Uncertain**, or **Exclude** with a required reason (your protocol's criteria plus standard reasons). **Could not get the full text** records a PRISMA “report not retrieved” (with an optional note) and can be undone. Under **Studies and reports**, group several included reports of the same study; this lowers “studies included” in the PRISMA flow without changing any decision. All of it is audited and counted from the event log, never typed in.

## Known limitations (current)
Earlier per-phase limitation lists are in `docs/implementation-reports/`. Still open:
- **Accounts:** no email verification, MFA, self-service password reset or account deletion (operator reset via `python -m app.cli set-password`); registration reveals whether an email is registered.
- **Search:** OpenAlex, Europe PMC, arXiv and Crossref only; no subscription databases, trial registries, citation chasing or RIS/CSV import, so PRISMA pathway B (“other methods”) has no way to receive records yet. Up to 500 records per source per query.
- **Screening:** one reviewer per decision (no dual screening); possible duplicates are flagged but cannot be merged in the UI.
- **Extraction and reports:** the evidence table exports CSV (no XLSX) and shows up to 200 papers on screen; no PDF export of the report, section regeneration or version comparison; the PRISMA flow is exported as counts, not a drawn diagram.
- **AI:** no labelled evaluation set yet (`evals/` is empty), so relevance is shown as an estimate, never a probability.
- **Frontend:** API types in `apps/web/src/lib/types.ts` are hand-written.
