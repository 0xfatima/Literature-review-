#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh into the Docker Compose deployment (Phase 14).
#   scripts/restore.sh BACKUP_DIR --yes
# DESTRUCTIVE: replaces the database and the storage volume. Stop api/worker first so nothing writes meanwhile.
set -euo pipefail
DIR="${1:?usage: scripts/restore.sh BACKUP_DIR --yes}"
[ "${2:-}" = "--yes" ] || { echo "refusing without --yes (this replaces all data)"; exit 2; }
COMPOSE="${COMPOSE:-docker compose}"
(cd "$DIR" && sha256sum -c <(grep -E '  (db\.dump|storage\.tar\.gz)$' manifest.txt))

$COMPOSE stop api worker web proxy || true
$COMPOSE up -d postgres
echo "1/3 database"
# --clean drops objects first. audit_events' append-only trigger blocks row DELETE/TRUNCATE, not DROP TABLE.
$COMPOSE exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < "$DIR/db.dump"
echo "2/3 files"
$COMPOSE run --rm --no-deps -T --entrypoint sh api -c 'find /srv/storage -mindepth 1 -delete && tar -C /srv/storage -xzf -' \
  < "$DIR/storage.tar.gz"
echo "3/3 migrations (a backup from an older release is upgraded in place)"
$COMPOSE run --rm migrate alembic upgrade head
$COMPOSE up -d
echo "restore complete; verify with: curl -fsS http://localhost:8000/readyz and the checklist in the runbook"
