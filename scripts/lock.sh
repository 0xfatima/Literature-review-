#!/usr/bin/env bash
# Resolve and pin ALL dependencies. Needs network. Commit the outputs:
#   requirements.lock.txt  (backend, exact versions incl. dev + transitive)
#   apps/web/package-lock.json
set -euo pipefail
cd "$(dirname "$0")/.."
python3 --version | grep -q "^Python 3\.12\." || { echo "Python 3.12.x required (found: $(python3 --version))" >&2; exit 1; }
node --version | grep -q "^v22\." || { echo "Node 22.x required (found: $(node --version))" >&2; exit 1; }
rm -rf .venv-lock && python3 -m venv .venv-lock
# shellcheck disable=SC1091
. .venv-lock/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
pip freeze --exclude-editable | LC_ALL=C sort > requirements.lock.txt
pip check
( cd apps/web && npm install --package-lock-only --ignore-scripts && npm ci --ignore-scripts )
.venv-lock/bin/python scripts/verify_locks.py   # fails the script if the locks are incomplete/inconsistent
{
  echo "locked_at_utc: $(date -u +%FT%TZ)"
  echo "python: $(python --version 2>&1)"; echo "pip: $(pip --version)"
  echo "node: $(node --version)"; echo "npm: $(npm --version)"
  echo "os: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") $(uname -m)"
  echo "python_pins: $(wc -l < requirements.lock.txt)"
  echo "npm_locked_packages: $(node -e "console.log(Object.keys(require('./apps/web/package-lock.json').packages).length-1)")"
} | tee docs/runbooks/LOCK_ENVIRONMENT.txt
echo "Wrote requirements.lock.txt ($(wc -l < requirements.lock.txt) pins) and apps/web/package-lock.json. COMMIT THEM."
