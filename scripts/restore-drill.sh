#!/usr/bin/env bash
# Backup/restore exercise (Phase 15): proves that a backup taken by scripts/backup.sh brings back BOTH database rows
# and uploaded files. Run against staging (or CI), never against production with real users.
#   COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml" scripts/restore-drill.sh https://host [--insecure]
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${1:?usage: restore-drill.sh BASE_URL [--insecure]}"; INSECURE="${2:-}"
MARK="$(mktemp -d)/marker.json"
python3 scripts/smoke.py --base "$BASE" $INSECURE --marker create --marker-file "$MARK"
OUT="$(mktemp -d)"
scripts/backup.sh "$OUT"
BACKUP="$(ls -d "$OUT"/srs-* | head -1)"
python3 scripts/smoke.py --base "$BASE" $INSECURE --marker delete --marker-file "$MARK"
T0=$(date +%s)
scripts/restore.sh "$BACKUP" --yes
for i in $(seq 1 60); do curl -fsk -o /dev/null "$BASE/healthz" && break; sleep 2; done
echo "restore + restart took $(( $(date +%s) - T0 )) s"
python3 scripts/smoke.py --base "$BASE" $INSECURE --marker verify --marker-file "$MARK"
