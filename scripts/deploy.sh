#!/usr/bin/env bash
# Production deploy (Phase 15). Idempotent: run it for the first install and for every upgrade.
#   scripts/deploy.sh [--insecure] [--skip-backup]
# Steps: pre-flight checks -> build -> backup (upgrades) -> migrate -> start/roll services -> wait healthy -> smoke test.
# Full procedure, TLS and rollback: docs/runbooks/deployment.md
set -euo pipefail
cd "$(dirname "$0")/.."
INSECURE=""; SKIP_BACKUP=""
for a in "$@"; do
  case "$a" in --insecure) INSECURE="--insecure";; --skip-backup) SKIP_BACKUP=1;; *) echo "unknown option $a"; exit 2;; esac
done
export COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml"
fail() { echo "PRE-FLIGHT FAILED: $*" >&2; exit 1; }

echo "== pre-flight"
[ -f .env.production ] || fail ".env.production missing (cp .env.production.example .env.production)"
[ "$(stat -c %a secrets 2>/dev/null)" = "700" ] || fail "./secrets must exist with mode 700 (scripts/gen-secrets.sh)"
for s in postgres_password redis_password database_url redis_url session_secret metrics_token llm_api_key; do
  [ -e "secrets/$s" ] || fail "secrets/$s missing (scripts/gen-secrets.sh)"
done
CERTS="$(grep -E '^TLS_CERT_DIR=' .env.production | cut -d= -f2- || true)"; CERTS="${CERTS:-./infra/proxy/certs}"
[ -s "$CERTS/fullchain.pem" ] && [ -s "$CERTS/privkey.pem" ] || fail "TLS certificate missing in $CERTS (see runbook: TLS)"
BASE="$(grep -E '^APP_BASE_URL=' .env.production | cut -d= -f2-)"
case "$BASE" in https://*) ;; *) fail "APP_BASE_URL must be https:// in production";; esac
grep -qE '^LLM_PROVIDER=none' .env.production || [ -s secrets/llm_api_key ] || fail "LLM_PROVIDER set but secrets/llm_api_key is empty"

echo "== build"
$COMPOSE build

if [ -z "$SKIP_BACKUP" ] && [ -n "$($COMPOSE ps -q postgres 2>/dev/null)" ]; then
  echo "== backup before upgrade"
  scripts/backup.sh ./backups
fi

echo "== database + broker, then migrations"
$COMPOSE up -d postgres redis
$COMPOSE run --rm migrate

echo "== application services"
$COMPOSE up -d --remove-orphans api worker-interactive worker-heavy beat web proxy

echo "== wait for health"
for i in $(seq 1 60); do
  if $COMPOSE exec -T api python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz',timeout=3).status==200 else 1)" 2>/dev/null; then
    break
  fi
  [ "$i" = 60 ] && { $COMPOSE logs --tail=100 api; fail "API not ready after 120 s"; }
  sleep 2
done

echo "== smoke test against $BASE"
python3 scripts/smoke.py --base "$BASE" $INSECURE
echo "deploy complete: $($COMPOSE images --format json 2>/dev/null | head -c 0)$(grep -E '^SRS_VERSION=' .env.production | cut -d= -f2-)"
