# Supported runtimes and dependency pinning

**Status: targets declared; NOTHING IS VERIFIED YET.** The "verified" column is filled from `validation-evidence/` after `scripts/validate_all.py` passes. Until then treat every cell as a claim to be proven.

| Component | Supported target | Declared in | Verified by evidence? |
|---|---|---|---|
| Python | **3.12.x** (`requires-python >=3.12`; `lock.sh` refuses other minors) | `pyproject.toml`, Dockerfiles, CI | no (sandbox has 3.12.3 but no packages) |
| Node.js | **22.x** (LTS line) | Dockerfile, CI, `lock.sh` | no (sandbox node v22.22.2, no packages) |
| PostgreSQL | **16** (features used need ≥ 13: partial+expression unique indexes, JSONB, plpgsql triggers) | `docker-compose.yml`, CI service | no |
| Redis | **7** | compose, CI | no |
| Docker Compose | v2 (`docker compose`) | scripts | no |
| Browsers (E2E) | Chromium via Playwright | `playwright.config.ts` | no |

## Pinning policy
- Backend: `requirements.lock.txt` = exact output of `pip freeze` from a clean venv created by `scripts/lock.sh` (direct **and** transitive). Installs use `pip install -c requirements.lock.txt -e ".[dev]"`, followed by `pip check`.
- Frontend: `apps/web/package-lock.json`; installs use `npm ci` only (fails if the lock and `package.json` disagree).
- `pyproject.toml` / `package.json` keep *ranges* as declared intent; the lockfiles are what runs. Dockerfiles warn loudly if a lock is missing; **CI and `validate_all.py` fail** if one is missing.
- `docs/runbooks/LOCK_ENVIRONMENT.txt` (written by `lock.sh`) records the interpreter/npm versions used to resolve.
- Upgrades: change ranges → rerun `scripts/lock.sh` → full validation → commit lock + evidence together.
- Not yet done: hash-pinned installs (`--require-hashes`), vulnerability scanning (e.g. `pip-audit`, `npm audit`), SBOM, image pinning by digest. Tracked in the production-readiness review.
