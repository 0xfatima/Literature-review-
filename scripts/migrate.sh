#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../services/api"
if [ $# -eq 0 ]; then alembic upgrade head; else alembic "$@"; fi
