#!/usr/bin/env bash
# Integration tests against a REAL Postgres (never SQLite). Starts the compose postgres/redis if needed.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TEST_DATABASE_URL:=postgresql+psycopg://srs:${POSTGRES_PASSWORD:-srs}@localhost:5432/srs_test}"
export TEST_DATABASE_URL
echo "Using $TEST_DATABASE_URL (schema 'public' is DROPPED and recreated: use a disposable database)"
pytest services/api/tests/integration -q --cov=app --cov-report=term-missing
