#!/usr/bin/env bash
# Usage: bash scripts/restore.sh <backup-file> [--yes]
# Restores a DTSYS database backup produced by scripts/backup.sh.
# Accepts either a plain .sql.gz or a GPG-encrypted .sql.gz.gpg (will prompt
# for BACKUP_ENCRYPTION_PASSPHRASE or the gpg passphrase interactively).
#
# This DROPS AND RECREATES the target database — it is destructive by design.
# Pass --yes to skip the confirmation prompt (e.g. for scripted DR drills).

set -euo pipefail

BACKUP_FILE="${1:-}"
CONFIRM="${2:-}"

if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
  echo "Usage: bash scripts/restore.sh <backup-file> [--yes]" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "Error: .env not found. Run from the repo root." >&2
  exit 1
fi

set -a
source .env
set +a

COMPOSE_FILE="${BACKUP_COMPOSE_FILE:-docker-compose.dev.yml}"

if [ "$CONFIRM" != "--yes" ]; then
  read -r -p "This will DROP and recreate database '$POSTGRES_DB'. Type 'yes' to continue: " reply
  if [ "$reply" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
SQL_GZ="$WORKDIR/restore.sql.gz"

case "$BACKUP_FILE" in
  *.gpg)
    if ! command -v gpg >/dev/null 2>&1; then
      echo "Error: this backup is GPG-encrypted but gpg is not installed." >&2
      exit 1
    fi
    if [ -n "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
      gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_ENCRYPTION_PASSPHRASE" \
          --decrypt --output "$SQL_GZ" "$BACKUP_FILE"
    else
      gpg --output "$SQL_GZ" --decrypt "$BACKUP_FILE"
    fi
    ;;
  *)
    cp "$BACKUP_FILE" "$SQL_GZ"
    ;;
esac

if ! gzip -t "$SQL_GZ"; then
  echo "Error: backup file failed gzip integrity check — refusing to restore a corrupt dump." >&2
  exit 1
fi

echo "Recreating database '$POSTGRES_DB'..."
docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";" \
    -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"

echo "Restoring from $BACKUP_FILE..."
gunzip -c "$SQL_GZ" | docker compose -f "$COMPOSE_FILE" exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo "Restore complete."
