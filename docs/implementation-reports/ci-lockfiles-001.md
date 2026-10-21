# CI lockfile failure: response and status

## Report from the first GitHub Actions run (supplied by the product owner, not observed by me)
Backend `test -f requirements.lock.txt` failed · Web `test -f package-lock.json` failed · Docker build succeeded · E2E skipped (it was gated behind backend+web). These failures are **correct**: the lockfiles do not exist and the CI requirement exists to catch exactly that. The requirement has not been weakened.

## Why I could not generate the lockfiles
This sandbox has no network (PyPI/npm/GitHub all unreachable, re-checked 2026-10-21 04:29 UTC) and no GitHub access. A lockfile is the recorded output of a real resolution against the registries; writing one by hand would be fabrication. None was written.

## What was done (all verifiable in the repo)
| Item | Change | Executed here? |
|---|---|---|
| Generate locks | `.github/workflows/bootstrap-locks.yml` (manual `workflow_dispatch`): Python 3.12 + Node 22 on a runner → `scripts/lock.sh` → proof step with the same commands CI/Docker use (`pip install -c requirements.lock.txt -e ".[dev]"`, `pip check`, `npm ci`) → uploads artifact → pushes branch `chore/lockfiles-<run id>` | YAML parsed only |
| Verify transitive pinning | `scripts/verify_locks.py`: exact `name==version` lines only; every direct dep pinned; **transitive closure walked through installed `Requires-Dist` metadata (markers/extras evaluated) and required to be fully pinned**; pins equal installed versions; npm lock: version 2/3, root equals `package.json`, every entry has `version`+`resolved`+`integrity`. Runs inside `lock.sh` and in CI backend | **Self-test executed: 15/15 pass; mutation (broken closure walk) → 3 fail; real mode without locks → FAIL (exit 1)** |
| Environment record | `lock.sh` writes `docs/runbooks/LOCK_ENVIRONMENT.txt`: UTC time, Python, pip, Node, npm, OS/arch, pin count, npm package count | no (needs network) |
| E2E never silently skipped | E2E no longer `needs` backend/web; it enforces lockfiles itself; added `ci-gate` job (`if: always()`, needs all four jobs, passes only if every result is `success`; skipped/cancelled = failure) — **make `ci-gate` the single required status check in branch protection** | structure checked by static validator; 5 CI-weakening mutations each make it FAIL |
| More coverage | `docker-build` also builds the web image (uses `npm ci`, so it needs the lock) and enforces lockfiles; `permissions: contents: read` on CI | not executed |
| Actions versions | `scripts/check_actions.py` reports pinned ref, latest release, and the runtime (`runs.using`) of each action; also runs in the bootstrap workflow (`actions-report.md` artifact). **I did not change any action version**: I cannot check current versions/runtimes from here and a wrong tag would break CI | offline parse executed: 4 distinct actions found (`checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4`) |
| Cache key | `setup-python` cache now keyed on `requirements.lock.txt` | no |

## Status of the seven requested items
| # | Item | Status |
|---|---|---|
| 1 | Run `scripts/lock.sh`, genuine lockfiles + environment record | **NOT DONE** — needs network. One click: run the "Generate lockfiles (manual)" workflow |
| 2 | Verify lock works with the install command and pins transitive deps | **Logic built and self-tested; real verification NOT RUN** (runs in bootstrap + CI backend) |
| 3 | `npm ci` succeeds from committed `package-lock.json` | **NOT RUN** (no lock, no registry) |
| 4 | Commit lockfiles, report exact Python/Node/npm versions | **NOT DONE.** No versions can be reported: nothing has been resolved |
| 5 | Update deprecated Actions after checking compatibility | **NOT DONE (deliberately).** Tooling to check exists; needs network. Please paste the deprecation warnings from your CI log and/or the `actions-report.md` artifact |
| 6 | Rerun CI; report first lint/type/test/build failures | **NOT DONE.** I have no access to GitHub or its logs; I will not predict results as findings |
| 7 | Confirm E2E executes | **NOT CONFIRMED.** Structure now prevents it being skipped by other jobs; actual execution requires the locks and a CI run |

## Procedure to complete (about 10 minutes of your time)
1. Actions → **Generate lockfiles (manual)** → Run workflow. Wait for green (it fails if the locks are incomplete or `npm ci`/`pip check` fail).
2. Open a PR from branch `chore/lockfiles-<run id>` (a human-opened PR triggers CI). Also download the artifact: `LOCK_ENVIRONMENT.txt` has the exact versions, `actions-report.md` has the action versions/runtimes.
3. CI then runs for real. Paste me the **first failing step of each job** (ruff / mypy / tests / next build / playwright / docker) and the artifact contents. Do not merge until `ci-gate` is green.
4. Set branch protection to require `ci-gate`.
Phase 2 has not been started and will not be until CI is genuinely green.
