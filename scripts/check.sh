#!/usr/bin/env bash
# Static checks + unit tests. Mirrors CI. Requires: pip install -e ".[dev]" and (cd apps/web && npm ci)
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== ruff lint";   ruff check services/api
echo "== ruff format"; ruff format --check services/api
echo "== mypy";        mypy
echo "== pytest (unit; integration skipped unless TEST_DATABASE_URL set)"
pytest services/api/tests/unit -q
if [ -d apps/web/node_modules ]; then
  echo "== web lint/typecheck/unit"
  (cd apps/web && npm run lint && npm run typecheck && npm run test:unit)
else
  echo "!! apps/web/node_modules missing: web checks NOT RUN" >&2
fi
