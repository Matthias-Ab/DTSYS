# Changelog

## Unreleased

### Fixed
- **Production boot**: containers now load `.env`; `ALLOWED_ORIGINS` accepts a plain value, comma-separated list or JSON array (previously the server could not start in production)
- **Multi-worker WebSockets**: device connections are now tracked cluster-wide via Redis (presence keys + pub/sub), so API requests and Celery scheduled commands reach agents connected to any worker
- **Offline command queue**: commands queued while a device was offline are delivered when it reconnects (only `sent` ones were redelivered before)
- **Agent**: command timeouts now kill the whole process group (a timed-out shell no longer hangs on its child); updater refuses downloads from a different host/scheme than the server
- **nginx (production)**: real client IP forwarded (rate limits and audit log previously saw only nginx's address, and a client-supplied `X-Forwarded-For` was trusted); security headers + HSTS + CSP on the SPA; `/health`, `/metrics` and agent install scripts routed to the API; upstreams re-resolved so redeploys no longer cause 502s
- **Status page** and **agent installer** URLs now work behind nginx (`/api/v1/status`; scripts mounted into the server container)
- **CSV exports** neutralise spreadsheet formula injection (audit export and frontend exports)

### Added
- Scheduled database backups (`backup` service in `docker-compose.prod.yml`); `restore.sh` fixed for TimescaleDB and verified with a restore drill
- TOTP multi-factor authentication (web + mobile), `/metrics` Prometheus endpoint (`METRICS_TOKEN`), ed25519-signed agent releases (`release-sign`), vitest frontend tests
- `scripts/setup-prod.sh` rewritten: hidden prompts, auto-generated secrets, safe `.env` writes, https enforcement

## v1.3.0 — 2026-04-14

### Security
- **Fixed** push token endpoint: added Expo/raw token format validation, per-user limit (max 10), platform field validation, and DELETE input validation
- **Fixed** Go toolchain upgraded from 1.25.0 → 1.25.9 to resolve 17 stdlib vulnerabilities (govulncheck)
- Full security audit documented in `docs/security-audit-v1.3.md`

### Features
- **Mobile — Full command execution**: Commands tab in DeviceDetail with history, status polling, output modal, command library bottom sheet, and one-tap dispatch of all supported command types
- **Windows MSI installer**: WiX v4 `.wxs` source, `build-msi.ps1` build script, multi-job CI workflow (`release.yml`) building MSI on Windows runner and publishing to GitHub Release, full installation docs in `docs/windows-msi-installer.md`
- **Device compliance policies**: `CompliancePolicy` + `ComplianceResult` models, evaluation engine with 6 rule types (offline hours, disk free/percent, required/forbidden software, OS type), full REST API (`GET/POST/PATCH/DELETE /compliance/policies`, `GET /compliance/results`, `POST /compliance/evaluate/{id}`), Alembic migration, and frontend Compliance page with expandable policy cards
- **Audit log UI**: Paginated audit log table with action/user/resource/date filters, CSV export, and real-time SSE live mode; admin-only API at `/api/v1/audit` with query, CSV export, and SSE stream endpoints

### Infrastructure
- Release CI workflow updated to 3-job pipeline: binaries (Ubuntu), MSI (Windows), release (Ubuntu)
- Go toolchain pin updated in `go.mod`

## v1.2.0 — 2026-04-14

### Features
- Real-time device monitoring (CPU, RAM, disk, network, temperature)
- Software inventory with update detection
- Remote command execution with live output
- Event log collection (syslog, journalctl)
- Alert engine with auto-resolution for CPU/RAM/disk thresholds
- Device screenshot capture (Linux/macOS/Windows)
- NTP sync status monitoring
- Process list collection
- Device grouping and tagging
- Scheduled commands
- Notification rules with email delivery
- Audit logging
- Dark/light theme
- Global search
- Network topology map
- Device comparison
- Software update management

### Agent
- Single binary, cross-platform (Linux amd64/arm64, macOS, Windows)
- Exponential backoff reconnection
- Priority-aware send buffer (critical messages never dropped)
- One-liner install script

### Infrastructure
- Docker Compose deployment
- PostgreSQL + TimescaleDB for metrics
- Redis for caching and screenshots
- Nginx reverse proxy with TLS
