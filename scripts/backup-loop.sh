#!/bin/sh
# Runs inside the `backup` service of docker-compose.prod.yml: dumps the database
# every BACKUP_INTERVAL_HOURS into /backups and prunes dumps older than
# BACKUP_RETENTION_DAYS. A failed dump never replaces or deletes good backups.
set -eu

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" "${PGPASSWORD:?}"
INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

while true; do
  stamp="$(date +%Y%m%d-%H%M%S)"
  out="/backups/dtsys-${stamp}.sql"
  if pg_dump -h postgres -U "$POSTGRES_USER" -f "$out" "$POSTGRES_DB" \
      && gzip "$out" && gzip -t "${out}.gz"; then
    echo "backup ok: ${out}.gz ($(du -h "${out}.gz" | cut -f1))"
    find /backups -name 'dtsys-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
  else
    echo "backup FAILED at ${stamp}" >&2
    rm -f "$out" "${out}.gz"
  fi
  sleep $((INTERVAL_HOURS * 3600))
done
