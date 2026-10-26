#!/usr/bin/env bash
# Create production secrets once (Phase 15). Never overwrites an existing file; never prints a secret.
#   scripts/gen-secrets.sh [DIR]   (default ./secrets)
# Permissions: the DIRECTORY is 700, so no other host user can reach the files. The files are 644 because Compose
# bind-mounts file secrets with their host permissions and the containers run as a non-root user; a 600 file owned
# by the host user would be unreadable inside the containers.
set -euo pipefail
DIR="${1:-./secrets}"
umask 022
mkdir -p "$DIR"
chmod 700 "$DIR"
rand() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }
put() { [ -s "$DIR/$1" ] || { printf '%s' "$2" > "$DIR/$1"; echo "created $DIR/$1"; }; }
put postgres_password "$(rand 40)"
put redis_password "$(rand 40)"
put session_secret "$(rand 64)"
put metrics_token "$(rand 40)"
[ -e "$DIR/llm_api_key" ] || { : > "$DIR/llm_api_key"; echo "created $DIR/llm_api_key (empty: AI disabled)"; }
put database_url "postgresql+psycopg://srs:$(cat "$DIR/postgres_password")@postgres:5432/srs"
put redis_url "redis://:$(cat "$DIR/redis_password")@redis:6379/0"
chmod 644 "$DIR"/*
echo "secrets ready in $DIR (back them up in your password manager; losing session_secret signs everyone out)"
