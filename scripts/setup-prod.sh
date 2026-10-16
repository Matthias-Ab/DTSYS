#!/usr/bin/env bash
# First-time production setup. Press Enter at a prompt to auto-generate a strong
# random value. Secrets are written to .env (mode 600) and never echoed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(docker compose -f docker-compose.prod.yml)

if [ ! -f .env ]; then
  cp .env.example .env
fi
chmod 600 .env

rand() { openssl rand -hex 24; }

# Set KEY=value in .env without interpreting special characters in the value
# (sed would break on / & \ in passwords).
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  KEY="$key" VALUE="$value" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env > "$tmp"
  cat "$tmp" > .env
  rm -f "$tmp"
}

ask_secret() {
  local prompt="$1" value
  read -r -s -p "$prompt (Enter to auto-generate): " value
  echo >&2
  printf '%s' "${value:-$(rand)}"
}

read -r -p "Public URL of the dashboard (e.g. https://dtsys.example.com): " PUBLIC_URL
if [[ ! "$PUBLIC_URL" =~ ^https:// ]]; then
  echo "Error: the public URL must start with https://" >&2
  exit 1
fi

POSTGRES_PASSWORD="$(ask_secret 'Postgres password')"
REDIS_PASSWORD="$(ask_secret 'Redis password')"
FIRST_ADMIN_PASSWORD="$(ask_secret 'Initial admin password')"
SECRET_KEY="$(openssl rand -hex 32)"
METRICS_TOKEN="$(rand)"

if [ "${#FIRST_ADMIN_PASSWORD}" -lt 12 ]; then
  echo "Error: the admin password must be at least 12 characters." >&2
  exit 1
fi

set_env POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
set_env REDIS_PASSWORD "$REDIS_PASSWORD"
set_env SECRET_KEY "$SECRET_KEY"
set_env FIRST_ADMIN_PASSWORD "$FIRST_ADMIN_PASSWORD"
set_env ALLOWED_ORIGINS "${PUBLIC_URL%/}"
set_env BASE_URL "${PUBLIC_URL%/}"
set_env METRICS_TOKEN "$METRICS_TOKEN"

if [ ! -f nginx/ssl/cert.pem ] || [ ! -f nginx/ssl/key.pem ]; then
  echo "WARNING: no TLS certificate found in nginx/ssl/. Generating a SELF-SIGNED one." >&2
  echo "         Replace nginx/ssl/cert.pem and key.pem with a real certificate before go-live." >&2
  bash scripts/generate_certs.sh
fi

"${COMPOSE[@]}" up -d --build

echo "Waiting for the server to become healthy..."
for _ in $(seq 1 40); do
  if "${COMPOSE[@]}" exec -T server python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

"${COMPOSE[@]}" exec -T server alembic upgrade head

cat <<EOF

Production setup complete.

  Dashboard:        ${PUBLIC_URL%/}
  Admin user:       admin   (password as entered/generated above)
  Metrics token:    stored in .env as METRICS_TOKEN (Prometheus scrape: Authorization: Bearer <token>)

Next steps:
  - Change the admin password after first login and enable MFA (Settings).
  - Back up regularly:  BACKUP_COMPOSE_FILE=docker-compose.prod.yml BACKUP_ENCRYPTION_PASSPHRASE=... bash scripts/backup.sh
  - SECRET_KEY also encrypts MFA secrets: keep it stable and back it up with .env.
EOF
