# Validation 002 — Phase 1 audit and fixes (2026-10-23)

Scope: audit of `main` (06db5ba) plus the local, unpushed commit `fix(ci): resolve 31 ruff findings…` (fcea27f),
which had been verified only "by emulation; real ruff not run". Every command below was actually executed.

Environment: Ubuntu 24.04, Python 3.12.3, Node 22.22, PostgreSQL 16, Redis 7, deps installed with
`pip install -c requirements.lock.txt -e ".[dev]"` and `npm ci`.

## Defects found and fixed

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | 41 Python files not `ruff format`-clean | CI `ruff format --check` fails | Ran `ruff format` |
| 2 | 18 `mypy --strict` errors (migration `_now()` untyped; `Result.rowcount` ×4; `Job | None` in task; `Response` vs `JSONResponse` in middleware) | CI `mypy` fails | `db.base.affected_rows()` helper (CursorResult-checked); typed `_now`; explicit `None` handling in `system.ping`; annotated `response: Response` |
| 3 | `env_file=".env"` resolved against CWD; README starts alembic/uvicorn/celery from `services/api` | Local (non-Docker) setup silently ignored `.env` and connected with the `CHANGE_ME` default password | Root `.env` loaded by absolute path; CWD `.env` second; env vars still win. Unit tests added |
| 4 | Worker task bodies (`app/jobs/tasks.py`) had 0% test coverage | Queue→worker→DB path untested outside Docker drills | `tests/integration/test_worker_tasks.py` (success, progress, duplicate delivery, cancellation, missing row) → 89% |
| 5 | Integration fixture `DROP SCHEMA public` on any `TEST_DATABASE_URL` | A mis-set variable wipes a dev DB | Fixture refuses databases whose name lacks `test` (verified against `srs`) |
| 6 | No `.gitattributes`; Windows checkout had CRLF everywhere | `bash scripts/*.sh` and container entrypoints break on Windows clones | `.gitattributes` enforcing LF |
| 7 | Web code not Prettier-clean (12 files); not checked in CI | Style drift | Formatted; `.prettierignore`; `format:check` in CI and `scripts/check.sh` |
| 8 | Test/build artefacts (`.coverage`, `pytest.xml`, `*.egg-info`, `*.tsbuildinfo`, `celerybeat-schedule*`) not ignored | Easy to commit noise | `.gitignore` |
| 9 | README stale ("not executed", "no lockfiles", stray "lockfile generated", unpinned `pip install`, wrong clone dir, test count) | Misleading setup | README and `pyproject.toml` comments updated |
| 10 | No model/migration drift check | Model change without migration passes CI | `alembic check` added to CI migration step |

## Results (actual)

- `pip check`: no broken requirements. `scripts/verify_locks.py`: PASS (63 Python pins, 511 npm packages).
- `scripts/run_stdlib_tests.py`: 35 passed, 0 failed.
- `ruff check` / `ruff format --check` (services/api): clean. `mypy` (strict): no issues in 66 files.
- Alembic: upgrade → downgrade → upgrade on clean DB succeeds; `alembic check`: no drift.
- `pytest services/api/tests` with `REQUIRE_INTEGRATION=1`: **100 passed, 0 skipped, 0 failed**; line coverage 82%.
- Web: `npm run lint` clean, `format:check` clean, `typecheck` clean, vitest 4/4 passed, `next build` succeeded.
- Live smoke (API + Celery worker/beat + Next standalone server, no Docker), through the Next.js proxy:
  register 201 → login 200 → create project → enqueue ping (delay 2 s) → observed `running 0 → running 45 → succeeded 100`;
  `/healthz`, `/readyz` (db+redis), `/healthz/worker` all ok; 422 validation envelope and 403 CSRF envelope carry correlation IDs.
  `seed-dev` run twice → one demo user, one "[Demo data]" project (idempotent).

## NOT run here (must be confirmed by CI)

- Playwright E2E (no browser download possible in the audit sandbox).
- `docker compose` stack, Docker image builds and `scripts/drills.py` (no Docker daemon).
- `scripts/check_actions.py` (GitHub API rate-limited).

## Remaining issues / recommendations

- GitHub Actions pinned at `checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4` run on Node 20,
  which GitHub is retiring; bump to the current majors and pin by SHA.
- `next lint` is deprecated (removed in Next 16): migrate to the ESLint CLI with flat config before upgrading.
- Starlette warns that `TestClient` over `httpx` is deprecated; track before the next Starlette major.
- `cli.py` has 0% test coverage (seed-dev / set-password verified manually only).


## Addendum — CI e2e drills `ip-limit` and `ip-isolation` failed (2026-10-23)

CI evidence: `ip-limit` returned 14×401 with forged `X-Forwarded-For` values (no 429: limit bypassed);
`ip-isolation` returned 429 for a second client address (one global bucket). All other drills passed.

Root cause, reproduced locally against Next.js 15.5 with a header-echo upstream: the rewrite forwarded
`X-Forwarded-For: 10.9.9.9` exactly as the client sent it, and sent no header at all when the client sent none.

Fix: nginx edge proxy (ADR A9). Local re-run of both drill patterns with the real API, Redis limiter and
`TRUSTED_PROXY_HOPS=1`:

| Path | Forged XFF ×14 | Client 1 ×13 then client 2 |
|---|---|---|
| Through nginx (new) | 401×10, then 429×4 | 401×10, 429×3; client 2 → **401** |
| Straight to Next.js (old, control) | (not repeated) | 401×10, 429; client 2 → **429** |

Register → login → create project → server-rendered `/projects` all succeed through the proxy.
New guard: `tests/unit/test_edge_proxy_config.py` (stdlib-only; a mutation to `$proxy_add_x_forwarded_for` fails it).
Still to confirm in CI: the compose stack with the `proxy` service, Playwright and all drills.
