# Local development runbook

## 1. First-time setup on a machine with network access
```bash
cp .env.example .env            # set POSTGRES_PASSWORD; same password in DATABASE_URL
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip freeze --exclude-editable > requirements.lock.txt     # commit a lock; CI/Docker should install from it
(cd apps/web && npm install)    # creates package-lock.json: COMMIT IT; CI then uses `npm ci`
```
Dependency versions in `pyproject.toml`/`package.json` are **lower bounds I could not verify against the registries**; the first install may surface incompatibilities (notably FastAPI/Pydantic/Starlette, Next 15 + ESLint 8, Celery/redis-py). Fix by adjusting bounds, then commit the lockfiles.

## 2. Start the stack
`docker compose up --build` (see README). Services: `postgres:16`, `redis:7`, `migrate` (one-shot), `api` :8000, `worker` (Celery + beat), `web` :3000.

## 3. Automated validation
`python scripts/validate_all.py` runs install → lint/type/build → clean stack start → migrations → pytest (REQUIRE_INTEGRATION=1) → vitest → Playwright → drills (ownership, CSRF, audit immutability, session revocation, IP limit/isolation, broker outage, **worker SIGKILL**) and writes evidence to `validation-evidence/`. The checklist below is the manual equivalent.

## 3b. Manual verification checklist (do these; report anything that fails)

**A. Services and health**
1. `curl -s localhost:8000/healthz` → `{"status":"ok"}`
2. `curl -s localhost:8000/readyz` → `database: ok`, `redis: ok` (HTTP 200)
3. `curl -s localhost:8000/healthz/worker` → HTTP 200 and a worker name. Stop the worker (`docker compose stop worker`) → 503 `no_workers`; restart it.

**B. Account and project flow (browser)**
4. Open http://localhost:3000 → redirected to `/login`. No "Forgot password" link; recovery notice visible.
5. Create account (password ≥ 12 chars) → lands on Projects. Create a project → opens the workspace; refresh → still there.
6. Rename, go back, Archive → disappears; tick "Show archived" → visible; Unarchive. Sign out → `/projects` redirects to login. Sign in again → project still present.
7. Register a second user in a private window; paste the first user's `/projects/<id>` URL → "Project not found" and no topic text.
8. DevTools → Application → Cookies: `srs_session` is **HttpOnly**, SameSite=Lax. (Secure only when `COOKIE_SECURE=true`.)

**C. Database and audit**
9. `docker compose exec postgres psql -U srs -d srs -c "select action, entity_type, at from audit_events order by at"` → registration, login, project events.
10. `... -c "update audit_events set action='x'"` → must fail with `audit_events is append-only`. Same for `delete from audit_events` and `truncate audit_events`.
11. `... -c "select password_hash from users limit 1"` → starts with `$argon2id$`. `select token_hash from auth_sessions` → 64-char hex (not the cookie value).
12. `... -c "select (title) from projects"` vs `select after from audit_events where action='project.updated'` → audit rows contain `changed_fields`, never the title text.

**C2. Migrations**
13. `docker compose run --rm migrate alembic downgrade base` then `alembic upgrade head` → both succeed; `\dt` shows 5 tables + `alembic_version`.

**D. Jobs and worker recovery**
14. Log in via the browser, then in DevTools console run:
    `fetch('/api/v1/jobs/ping',{method:'POST',headers:{'Content-Type':'application/json','X-SRS-CSRF':'1','Idempotency-Key':'k1'},body:'{"delay_s":0}'}).then(r=>r.json()).then(console.log)`
    → `status: queued` then (GET `/api/v1/jobs/<id>`) `succeeded`, `progress 100`.
15. Repeat step 14 with the same key → HTTP 200 and the **same** job id (replay). Run it again with a **new** key after it finished → a new job (legitimate rerun).
16. Start a long one (`delay_s: 40`, new key). While it runs: `docker compose kill -s SIGKILL worker`. Wait > `JOB_STALE_AFTER_S` (120 s default; lower it in `.env` for the drill), `docker compose up -d worker` → the sweep re-queues it and it reaches `succeeded` with `attempts: 2`. (If your worker was killed three times it ends `failed` with "retry attempts exhausted": by design.)
17. Start `delay_s: 40` twice quickly with different keys → second returns 409 `job_already_active`.

**E. Security spot-checks (curl)**
18. `curl -i -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"a@b.co","password":"x"}'` → **403 csrf_rejected** (no `X-SRS-CSRF` header).
19. 11 wrong-password logins in a minute → 429 with `Retry-After`.
20. Log output (`docker compose logs api`) is one JSON object per line; grep it for your password → no match.

## 4. Operator tasks
- Reset a password: `docker compose run --rm api python -m app.cli set-password EMAIL`
- Create a user: `... python -m app.cli create-user EMAIL`
- Run the job-recovery sweep now: `... python -m app.cli sweep-jobs`
- Reset the dev database: `docker compose down -v` (destroys all local data).

## 5. Running the automated tests
See README "Checks and tests". Integration tests drop and recreate schema `public` of `TEST_DATABASE_URL`: point it at a disposable database (e.g. `srs_test`), never the dev database.
