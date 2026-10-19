#!/usr/bin/env bash
# Safe upgrade of a production DTSYS install.
#
#   git pull && bash scripts/upgrade.sh
#
# 1. takes a database dump (pre-upgrade-*.sql.gz in the backups volume)
# 2. keeps the currently running image as dtsys-server:previous for rollback
# 3. rebuilds and restarts; the one-shot `migrate` service applies migrations before
#    the API/worker/beat start
# 4. waits for health and tells you how to roll back if it fails
#
# Rollback of the APPLICATION (no schema change involved):
#   DTSYS_TAG=previous docker compose -f docker-compose.prod.yml up -d
# Rollback of a bad MIGRATION: migrations only go forward. Restore the pre-upgrade
# dump with scripts/restore.sh (see docs/UPGRADE.md).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(docker compose -f docker-compose.prod.yml)
IMAGE="dtsys-server:${DTSYS_TAG:-local}"

[ -f .env ] || { echo "Error: .env not found; run from the install directory." >&2; exit 1; }

echo "==> Pre-upgrade database dump"
"${COMPOSE[@]}" exec -T backup sh -c '
  set -e
  out="/backups/pre-upgrade-$(date +%Y%m%d-%H%M%S).sql"
  pg_dump -h postgres -U "$POSTGRES_USER" -f "$out" "$POSTGRES_DB"
  gzip "$out"
  gzip -t "$out.gz"
  echo "saved $out.gz"
'

echo "==> Keeping the current image as dtsys-server:previous"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker tag "$IMAGE" dtsys-server:previous
fi

echo "==> Rebuilding and restarting (migrations run first)"
"${COMPOSE[@]}" up -d --build

echo "==> Waiting for health"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T server python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; then
    echo "Upgrade complete."
    exit 0
  fi
  sleep 3
done

cat >&2 <<MSG
The server did not become healthy after the upgrade.
  Inspect:    docker compose -f docker-compose.prod.yml logs migrate server
  Roll back the app (if migrations did not change the schema):
              DTSYS_TAG=previous docker compose -f docker-compose.prod.yml up -d
  Roll back after a bad migration: restore the pre-upgrade dump (scripts/restore.sh).
MSG
exit 1
