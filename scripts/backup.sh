#!/usr/bin/env bash
# Consistent-enough backup of the Docker Compose deployment (Phase 14). See docs/runbooks/data-retention-and-recovery.md.
#   scripts/backup.sh [OUT_DIR]      -> OUT_DIR/srs-<UTC timestamp>/{db.dump,storage.tar.gz,manifest.txt}
# Database first, then files: a file uploaded between the two steps has no row yet and is removed by the orphan
# sweep after restore; a row whose file is missing is reported by the document status (never silently "processed").
set -euo pipefail
OUT_ROOT="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT_ROOT}/srs-${STAMP}"
COMPOSE="${COMPOSE:-docker compose}"
mkdir -p "$OUT"
umask 077   # backups contain private research data

echo "1/3 database (pg_dump custom format)"
$COMPOSE exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner' > "$OUT/db.dump"

echo "2/3 uploaded files (storage volume)"
$COMPOSE run --rm --no-deps -T --entrypoint sh api -c 'tar -C /srv/storage -czf - .' > "$OUT/storage.tar.gz"

echo "3/3 manifest"
{
  echo "created_utc=${STAMP}"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "alembic_head=$($COMPOSE run --rm --no-deps -T migrate alembic current 2>/dev/null | tail -1 || echo unknown)"
  (cd "$OUT" && sha256sum db.dump storage.tar.gz)
} > "$OUT/manifest.txt"
echo "backup written to $OUT (store it encrypted and off-host)"
