#!/usr/bin/env bash
# Usage: bash scripts/backup.sh
# Creates a timestamped, integrity-checked backup of the DTSYS database.
# Set BACKUP_ENCRYPTION_PASSPHRASE to encrypt the backup at rest with GPG
# (AES256) — strongly recommended since dumps contain user records and
# device/API-key hashes. Restore with scripts/restore.sh.

set -euo pipefail

if [ ! -f .env ]; then
  echo "Error: .env not found. Run make dev first." >&2
  exit 1
fi

set -a
source .env
set +a

COMPOSE_FILE="${BACKUP_COMPOSE_FILE:-docker-compose.dev.yml}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
FILENAME="$BACKUP_DIR/dtsys-$TIMESTAMP.sql.gz"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$FILENAME"

if ! gzip -t "$FILENAME"; then
  echo "Error: backup file failed gzip integrity check: $FILENAME" >&2
  rm -f "$FILENAME"
  exit 1
fi

if [ -z "${BACKUP_ENCRYPTION_PASSPHRASE:-}" ]; then
  echo "WARNING: BACKUP_ENCRYPTION_PASSPHRASE not set — backup is stored unencrypted." >&2
  echo "         Set it (a strong passphrase) to encrypt backups at rest with GPG." >&2
  echo "Backup saved to: $FILENAME"
  ls -lh "$FILENAME"
  exit 0
fi

if ! command -v gpg >/dev/null 2>&1; then
  echo "Error: BACKUP_ENCRYPTION_PASSPHRASE is set but gpg is not installed." >&2
  exit 1
fi

ENCRYPTED_FILENAME="${FILENAME}.gpg"
gpg --batch --yes --pinentry-mode loopback --passphrase "$BACKUP_ENCRYPTION_PASSPHRASE" \
    --symmetric --cipher-algo AES256 --output "$ENCRYPTED_FILENAME" "$FILENAME"
rm -f "$FILENAME"

echo "Backup saved (encrypted) to: $ENCRYPTED_FILENAME"
ls -lh "$ENCRYPTED_FILENAME"
