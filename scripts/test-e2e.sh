#!/usr/bin/env bash
# Browser tests against the full compose stack.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --build --wait postgres redis migrate api worker web proxy 2>&1 | tail -5 || docker compose up -d --build
(cd apps/web && npx playwright install --with-deps chromium && E2E_BASE_URL=http://localhost:3000 npx playwright test)
