# DTSYS

Device telemetry, remote command execution, alerting, and fleet management for Windows, Linux, and macOS endpoints.

## Quick Start (Production)

1. Build agent release binaries:
```bash
bash scripts/build-release.sh
```

2. Run the production setup:
```bash
bash scripts/setup-prod.sh
```

3. Access the UI and API:
- Dashboard: `https://YOUR_DOMAIN`
- API docs: `https://YOUR_DOMAIN/docs`

## Quick Start (Development)

```bash
make dev
make dev-agent
```

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Docker | 24+ | Needed for local PostgreSQL, Redis, and the full stack |
| Go | 1.26.9 | Required for the agent build and tests |
| Node.js | 20 | Required for the frontend build and dev server |
| Python | 3.12 | Required for the FastAPI server and Alembic |

## Architecture

```text
                    +-----------------------+
                    |      React UI         |
                    |  Dashboard / Alerts   |
                    +-----------+-----------+
                                |
                                | HTTPS / REST
                                v
+-------------+        +--------+---------+        +------------------+
| Go Agent    | <----> | FastAPI API      | <----> | PostgreSQL /     |
| telemetry   |  WS    | WebSocket broker |        | TimescaleDB      |
| commands    |        | auth / alerts    |        +------------------+
+-------------+        +--------+---------+                   ^
                                |                             |
                                v                             |
                         +------+-------+                     |
                         | Redis cache  | <-------------------+
                         | rate limits  |
                         | enrollment   |
                         +--------------+
```

## Development Setup

1. Install prerequisites:
```bash
docker --version
node --version
go version
python3 --version
```

2. Create and review environment settings:
```bash
cp .env.example .env
```

3. Start infrastructure:
```bash
docker compose up -d postgres redis
```

4. Install server dependencies:
```bash
cd server
python3 -m pip install --user --break-system-packages uv
~/.local/bin/uv venv .venv
~/.local/bin/uv pip install --python .venv/bin/python -e ".[dev]"
```

5. Apply database migrations:
```bash
cd server
.venv/bin/alembic upgrade head
```

6. Install frontend dependencies:
```bash
cd frontend
npm install
```

7. Build or run the client agent:
```bash
cd client
go mod tidy
go build ./...
```

8. Start the dev services:
```bash
make dev-server
make dev-frontend
make dev-agent
```

## Agent Deployment

### Linux

One-line install example:
```bash
curl -fsSL https://your-server.example.com/install-agent.sh | sudo bash -s -- \
  --server https://your-server.example.com \
  --token REPLACE_ME
```

Then install the service:
```bash
sudo cp client/packaging/linux/dtsys-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dtsys-agent
```

### Windows

Run the packaged installer from an elevated PowerShell session:
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\client\packaging\windows\install.ps1 `
  -ServerURL "https://your-server.example.com" `
  -EnrollmentToken "REPLACE_ME"
```

The installer:
- Downloads `dtsys-agent-windows.exe`
- Writes `agent.toml`
- Installs the service with NSSM
- Falls back to Chocolatey or a direct NSSM zip download when NSSM is not already present

### macOS

Copy the binary and config:
```bash
mkdir -p /usr/local/lib/dtsys /etc/dtsys
cp dist/agents/dtsys-agent-darwin-arm64 /usr/local/lib/dtsys/dtsys-agent
chmod +x /usr/local/lib/dtsys/dtsys-agent
cp client/packaging/macos/com.dtsys.agent.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dtsys.agent.plist
```

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://dtsys:dtsys@localhost:5432/dtsys` | Primary SQL database |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for enrollment tokens and WS rate limits |
| `SECRET_KEY` | `dev-secret-key-change-in-production` | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime |
| `ENROLLMENT_TOKEN_EXPIRE_MINUTES` | `60` | Enrollment token TTL |
| `ENVIRONMENT` | `development` | Controls startup behavior and docs exposure |
| `APP_NAME` | `DTSYS` | Service name used in responses |
| `DEVICE_OFFLINE_THRESHOLD_SECONDS` | `120` | Offline detection threshold |
| `ALERT_CPU_PERCENT` | `90.0` | CPU usage alert threshold |
| `ALERT_RAM_PERCENT` | `90.0` | RAM usage alert threshold |
| `ALERT_DISK_PERCENT` | `90.0` | Disk usage alert threshold |
| `ALERT_CPU_TEMP_CELSIUS` | `85.0` | CPU temperature alert threshold |
| `ALERT_NTP_OFFSET_MS` | `500.0` | NTP drift alert threshold |
| `FIRST_ADMIN_PASSWORD` | `changeme` | Seeded admin password |
| `AGENT_VERSION` | `0.1.0` | Version served to auto-updating agents |
| `AGENT_DIST_DIR` | `./dist/agents/` | Directory exposed by `/api/v1/downloads/*` |

## API Quick Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/auth/login` | `POST` | Login and receive access / refresh tokens |
| `/api/v1/auth/refresh` | `POST` | Refresh access token |
| `/api/v1/devices/register` | `POST` | Agent registration using enrollment token |
| `/api/v1/devices` | `GET` | List managed devices |
| `/api/v1/devices/{id}` | `GET` | Device details |
| `/api/v1/devices/{id}/network` | `GET` | Latest network interface snapshot |
| `/api/v1/devices/{id}/commands` | `POST` | Dispatch a command to one device |
| `/api/v1/commands/bulk` | `POST` | Dispatch a command to many devices |
| `/api/v1/alerts` | `GET` | Query alerts |
| `/api/v1/alerts/{id}/resolve` | `POST` | Resolve an alert |
| `/api/v1/agent/version` | `GET` | Agent update metadata |
| `/api/v1/downloads/{filename}` | `GET` | Download agent binaries |
| `/api/v1/agent/download` | `GET` | Download agent binary by arch/platform |
| `/health` | `GET` | App, DB, Redis, and uptime health status |

## Build and Release

Build the frontend:
```bash
make build-frontend
```

Build all agent binaries:
```bash
make build-agents
```

Start the full local stack:
```bash
make docker-up
```

Stop it:
```bash
make docker-down
```

## Testing

Server tests:
```bash
make test-server
```

Client tests:
```bash
make test-client
```

Run migrations:
```bash
make migrate
```

## Troubleshooting

### Agent will not connect

- Verify `server.url` in `agent.toml`
- Confirm the device can reach `/ws/device/{id}?token=...`
- Check Redis rate limiting if repeated connection attempts are being rejected with close code `4029`

### Device cannot register

- Ensure the enrollment token exists in Redis and has not been used already
- Confirm the server can write to PostgreSQL during registration
- Verify `server.url` points to the API host, not a local-only address

### Alerts are not firing

- Check `/health` for database and Redis status
- Confirm telemetry messages are arriving over WebSocket
- Review thresholds in `server/app/config.py`

### Frontend shows stale state

- Rebuild with `make build-frontend`
- Confirm the browser has a fresh access token
- Inspect `/api/v1/alerts` and `/api/v1/devices` responses directly

## Contributing

1. Create a feature branch.
2. Keep changes scoped and run the relevant build/test targets.
3. Add or update tests for any behavior change.
4. Run `make lint-server`, `make test-server`, and `make test-client` before opening a PR.
5. Document any operator-facing change in this README.
