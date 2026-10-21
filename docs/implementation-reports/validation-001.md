# Validation report 001 — Phase 1

## Verdict
**Phase 1 is NOT validated.** Items 1–5 of the validation request (dependency resolution, Ruff/mypy/tsc/Next build/CI, clean-environment start, integration/unit/frontend/Playwright runs, the 20 manual checks including worker-kill) **could not be executed** because this environment has no network, no Docker, no PostgreSQL, no Redis and none of the required packages. No result in the "NOT EXECUTED" column below should be read as passing. Phase 2 has not been started.

What this session did instead: proved the blockage precisely, desk-reviewed items 6–7 (finding and fixing real defects), strengthened the code and CI so the skipped work cannot be skipped silently, and built a one-command harness so the validation can be run elsewhere with evidence captured.

## 1. Environment evidence (observed this session, 2026-10-20/21)
| Probe (exact command) | Result |
|---|---|
| `pip download --no-deps -d /tmp/pt fastapi` | `No matching distribution found for fastapi` |
| `npm view next version` | no output (registry unreachable) |
| `getent hosts pypi.org registry.npmjs.org github.com` | nothing resolved (no DNS) |
| `curl -m 10 https://pypi.org/simple/fastapi/` · `https://files.pythonhosted.org/` · `https://registry.npmjs.org/next` | HTTP `000` (no connection) ×3 |
| `apt-get download redis-server` | `Could not resolve 'archive.ubuntu.com'` |
| `command -v docker docker-compose podman psql postgres pg_ctl initdb redis-server redis-cli` | all absent |
| `importlib.util.find_spec` for fastapi, sqlalchemy, alembic, pydantic, pydantic_settings, celery, redis, psycopg, pytest, httpx, argon2, fakeredis, kombu, starlette, uvicorn | none importable |
| `command -v ruff mypy eslint next playwright vitest` | absent; `apps/web/node_modules` absent |
| pip/npm config files named by `PIP_CONFIG_FILE` / `NPM_CONFIG_USERCONFIG` | files do not exist (no mirror) |
Available runtimes: Python 3.12.3, pip 24.0, Node v22.22.2, npm 10.9.7, git 2.43.0, PyYAML 6.0.3, `tsc` 6.0.3 (syntax checks only). **Dependency versions: none resolved; no lockfile exists** (I will not fabricate one).

## 2. Status of each requested item
| # | Request | Status | Evidence |
|---|---|---|---|
| 1 | Resolve/pin deps, commit lockfiles, document runtimes | **NOT DONE** (needs network). Tooling ready: `scripts/lock.sh`; policy + targets in `docs/runbooks/supported-runtimes.md`; CI/Docker/harness now **fail** or warn if locks are absent | harness step `locks` = FAIL (observed) |
| 2 | Run Ruff, mypy, tsc, Next build, CI | **NOT EXECUTED.** Desk-fixed 6 certain `mypy --strict` errors by inspection (bare `dict` generics, untyped `client` param, untyped middleware); added 2 *justified* mypy overrides (see §5); not verified by mypy | — |
| 3 | Start PG/Redis/API/worker/Next from clean env | **NOT EXECUTED** | harness step `stack-up` = BLOCKED |
| 4 | Integration/unit/frontend/Playwright, no silent skips | **NOT EXECUTED.** Skip-proofing built and **its logic executed** (§4) | synthetic-JUnit guard test |
| 5 | 20 manual checks incl. worker-kill | **NOT EXECUTED.** 8 of the checks are now automated drills (`scripts/drills.py`); syntax-checked only | — |
| 6 | Investigate reported risks | **Desk analysis done; runtime verification pending** (§5) | — |
| 7 | Production implications | **Done** → `docs/architecture/production-readiness-review.md` | — |
| 8 | LangGraph gate first in Phase 2, after Phase 1 passes | **Recorded** (ADR A7; plan). Not started | — |

## 3. Commands actually executed this session and their results
| Command | Result |
|---|---|
| `python3 scripts/run_stdlib_tests.py` | **33 passed, 0 failed** (shim, *not* pytest). 26 from Phase 1 + 7 new `test_client_ip.py` |
| Mutation check on `core/net.py` (restore "first X-Forwarded-For entry" semantics) | **5 failed** as intended; restored → 33 pass |
| `python3 scripts/validate_static.py` | **23 PASS, 0 FAIL** (Python syntax of 71 files, internal import resolution, unused imports (approx.), `.env.example`↔`Settings`, YAML ×2, JSON ×4, `bash -n` ×5, `node --check` ×2, TypeScript *syntax* of 18 files; **types not checked**) |
| `python3 scripts/check_erd.py` | 31 entities, 47 relationships, 0 errors |
| `python3 -m compileall -q services scripts` | OK, 71 files |
| `python3 -c "yaml.safe_load(ci.yml)"` + inspection of embedded guard script | OK (after fix, §6) |
| Synthetic-JUnit test of the skip/count guards (5 cases: all pass; 1 skipped; all integration skipped; too few tests; 1 failure) | CI guard and harness guard each returned the expected verdict in all 5 cases |
| `python3 scripts/validate_all.py` (full, real, in this sandbox) | 2 PASS (`stdlib`, `static`), 2 FAIL (`env`: docker/compose missing; `locks`: missing), **15 BLOCKED**; exit code 1; **no step falsely passed** |
| `python3 scripts/validate_all.py --dry-run` | prints plan, executes nothing, labelled "DRY RUN" |

## 4. Test inventory (exists vs executed)
| Suite | Count | Executed? | Skipped |
|---|---|---|---|
| stdlib-only unit (shim) | 33 | **Yes — all pass** | 0 |
| pytest unit needing argon2 | 4 | No | — |
| pytest integration (real PG) | 52 functions (+3 parametrized audit cases; +2 added this session: index parity, constraint-name parity) | No | would be *skipped* locally; now **error** under `REQUIRE_INTEGRATION=1` |
| vitest | 4 | No | — |
| Playwright | 5 | No | — |
| Live drills | 8 (`worker-kill`, `broker-outage`, `ip-limit`, `ip-isolation`, `session-revocation`, `csrf`, `audit-immutable`, `ownership`) | No | — |
Coverage: **no figure exists.** Skipped tests executed this session: 0 (none were run).

## 5. Desk review of the reported risks (analysis only — nothing here is verified)
| Risk | Analysis | Action taken | Verified by (pending) |
|---|---|---|---|
| SQLAlchemy index construction (`Index` mixing strings + `text()` expression + `postgresql_where`) | Accepted column-expression forms in SQLAlchemy 2.x include strings and `text()`; probably fine, but expression indexes round-trip poorly through autogenerate (we author migrations by hand) | Added `test_every_model_declared_index_exists_in_the_migrated_database` (also forces the model to import/construct) | pytest integration |
| Logout 204 | Returned FastAPI's *injected* `Response`; unusual | Switched to the documented pattern (explicit `Response(204)` + `delete_cookie`, `response_class=Response`); test now asserts empty body and cookie-clearing header | pytest integration |
| Migration naming conventions | Explicit non-`ck` names are not re-prefixed; `ck` conventions use `%(constraint_name)s` so I wrapped them in `op.f()` | Added `test_every_model_declared_constraint_name_matches_the_migrated_database` | pytest integration |
| Broker outage | `send_task(retry=False)` should raise quickly, but connect time depends on kombu's timeout; `_dispatch` swallows the error and leaves the job `queued` for the sweeper | Bounded with `broker_transport_options={"socket_connect_timeout": 2}` (connect only; a read timeout would break worker long-polling); drill asserts 202, `queued|enqueued_at null`, request < 15 s, recovery after Redis returns | `broker-outage` drill; `test_broker_outage…` |
| Frontend tool compatibility | Next ^15 + React ^19 + ESLint ^8.57 + `eslint-config-next` ^15 + Tailwind ^3.4 + TS ^5.4 are mutually consistent *as I understand them*; `next lint` may print deprecation notices in later 15.x. No definite defect found by reading | none | `npm ci`, lint, typecheck, build |
| Strict typing | 6 certain errors fixed by inspection. Likely remaining: SQLAlchemy `Mapped` helper typing, pydantic plugin, FastAPI route return types. Two mypy overrides added and **justified in `pyproject.toml`**: (1) `ignore_missing_imports` for celery/kombu/argon2/redis (no reliable stubs); (2) `disallow_untyped_decorators=false` for `app.jobs.tasks` only (untyped Celery decorator). No rule was disabled globally | as stated | `mypy` |

## 6. Defects found and fixed during this session (all caught by reading or by the static checks, none by running the app)
1. **Rate-limit client key was effectively global behind the Next.js proxy, and the XFF handling was spoofable** (first entry trusted). Replaced by `TRUSTED_PROXY_HOPS` rightmost-N logic; 7 tests executed; new `ip-isolation` drill added to prove it at runtime. *(Design defect, High.)*
2. Unused `shutil` import in the new orchestrator (ruff would fail; caught by `validate_static`).
3. Harness bug: `urlopen` raises on HTTP 401, so the stack-health check (expects 401 through the Next proxy) could never pass.
4. My CI edit produced **invalid YAML** (block scalar in a flow mapping); caught by `validate_static`, which itself crashed instead of reporting FAIL — both fixed.
5. Drill bugs found by self-review before first run: cookie domain (`localhost` → `localhost.local` for `http.cookiejar`), and drills tripping our own login limiter.
6. CI could previously pass with integration tests skipped and with floating dependency versions; now `REQUIRE_INTEGRATION=1`, a post-pytest skip/count guard, `test -f` lockfile checks, `pip install -c`, `npm ci`, and one-shot-service-safe startup (no `--wait` on `migrate`).

## 7. Worker-kill recovery — evidence
**None obtained. This test was not run.** What will constitute evidence when it is run (`python scripts/drills.py run worker-kill`, or the full harness):
- Fast timers applied via a temporary compose override (stale after 20 s, sweep every 5 s).
- A `delay_s=60` job reaches `running` with progress > 0 → `docker compose kill -s SIGKILL worker` → job observed *not* completing → `docker compose up -d worker`.
- PASS requires: final `status=succeeded`, `attempts=2`, `result={"pong": true}`, and exactly one `job.recovered_queued` audit row for that job id. The timeline (with timestamps), the audit rows and the final `jobs` row are written to `validation-evidence/drills-evidence.json`.
- The unit-level rules (stale detection, retry cap, bounded attempts) *were* executed (`test_job_fsm.py`, 7 tests pass); the DB-level recovery tests exist but have not run.

## 8. How to complete the validation (one person, one machine with network + Docker + Python 3.12 + Node 22)
```bash
scripts/lock.sh                          # resolves & pins; commit requirements.lock.txt, apps/web/package-lock.json, docs/runbooks/LOCK_ENVIRONMENT.txt
python scripts/validate_all.py           # ~15-25 min; writes validation-evidence/<UTC>/{REPORT.md,results.json,*.log,pytest.xml,vitest.xml,playwright.json,coverage.xml}
python scripts/drills.py run all         # (already included above; run alone to repeat)
```
Return `validation-evidence/` and I will triage failures. The harness passes only if **every** step passes with 0 skipped tests and counts at or above the repo inventory. Expected first-run failures (my prediction, unverified): `ruff format --check` (hand-formatted code), some `mypy --strict` errors, possibly dependency-resolution tweaks, and any of the runtime details in §5.
The remaining manual checks that are not automated (cookie flags in DevTools, visual/a11y, UI flows beyond Playwright) stay in `docs/runbooks/local-development.md` §3.

## 9. Decisions needed from you
1. Where will this be run (your machine / CI)? If you can enable network + Docker for the sandbox instead, say so and I will run everything myself and report real numbers.
2. Confirm the pilot prerequisite: implement an operator `delete-user` CLI before any pilot (production review §4). I have not built it (validation-only scope).
