# Upgrading and rolling back

## Upgrade

```bash
git pull
bash scripts/upgrade.sh
```

The script dumps the database, keeps the running image as `dtsys-server:previous`, rebuilds,
and restarts. A one-shot `migrate` service runs `alembic upgrade head` before the API,
worker and beat start, so the app never runs against an old schema.

## Roll back the application

If the new version is unhealthy but the schema did not change:

```bash
DTSYS_TAG=previous docker compose -f docker-compose.prod.yml up -d
```

## Roll back a migration

Migrations only go forward. If a migration was the problem, restore the dump the upgrade
script took (it is in the `backups` volume as `pre-upgrade-*.sql.gz`):

```bash
docker compose -f docker-compose.prod.yml cp backup:/backups ./backups
BACKUP_COMPOSE_FILE=docker-compose.prod.yml bash scripts/restore.sh ./backups/backups/pre-upgrade-<timestamp>.sql.gz
DTSYS_TAG=previous docker compose -f docker-compose.prod.yml up -d
```

## Notes

- `SECRET_KEY` must not change across upgrades: it also encrypts stored MFA secrets.
- Back up `.env` together with the database.
- There is a short interruption while the API restarts (single instance, 2 workers); agents
  reconnect automatically.
