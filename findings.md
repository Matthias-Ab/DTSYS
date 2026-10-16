# DTSYS — Deployment Readiness & Full Audit Findings

Date: 2026-10-16 · Commit audited: `6c87d8b` (main, includes unpushed reconnect fix)

## How this was produced

- **Hands-on testing (me):** booted the real production stack (Postgres, Redis, server ×2 workers, Celery worker + beat, nginx, built frontend), ran all 12 migrations on real Postgres, enrolled a real agent over WSS through nginx, ran on-demand and scheduled commands end to end, ran a backup + restore drill, and tested MFA, rate limiting and security headers.
- **Code audit:** five read-only reviewers (server, Go agent, frontend, mobile, infra/CI), each told to verify by reading code and to quote file:line.
- **Verification legend:** `✔` = I re-checked this myself against the code after the reviewer reported it. `○` = reported by a reviewer from code reading, not independently re-checked. `(inferred)` = reviewer did not execute it.

---

## 1. Readiness score (revised): **52 / 100**

The earlier score (68) was based on what I had *run*: the happy path works. The audit then looked at what I had *not* exercised, and found tenant-isolation holes, role-check holes, a broken Windows installer, a non-functional mobile app, and a CI pipeline that has never run the server tests. The score is lowered accordingly.

| Scenario | Score | Reasoning |
|---|---|---|
| Single organization, trusted admins only, Linux agents only | **~62** | Core flow is solid and verified. Blockers below still apply (RBAC, CI, upgrade path, TLS). |
| Multiple organizations / untrusted viewers | **~35** | Cross-tenant data leaks and viewer-triggered root commands (SRV-01…08). |
| Windows fleet via MSI | **~20** | MSI cannot write its config (AGT-01). |
| Mobile app as a deliverable | **~15** | Commands and metrics call routes that do not exist (MOB-01…04). |

| Area | Score | Notes |
|---|---|---|
| Core function (verified live) | 85 | Enroll, WSS via nginx, commands, scheduled commands, MFA API, backups all work. |
| Authorization / tenancy | 30 | Many endpoints unscoped; role checks mostly missing. |
| Deploy & upgrade process | 45 | No automated migrations, no upgrade/rollback doc, `setup-prod.sh` never run clean. |
| Quality gates (CI/tests) | 30 | CI red; server tests never run in CI; no cross-tenant tests anywhere. |
| Clients (Windows / mobile) | 25 | MSI broken, mobile non-functional. |
| Operations (monitoring, logs, TLS renewal) | 40 | `/metrics` exists; no alerting, log rotation, cert renewal. |

---

## 2. Corrections to earlier statements (accuracy log)

- I said CI "test steps look fine". **Wrong.** The server job fails at lint and the later test step is skipped; with the current install method it would fail again on a missing `.venv/bin/pytest`. Server tests have effectively never run in CI (OPS-02).
- I said the Celery fix was "confirmed live". **That was wrong at the time** (stale worker image, first-run-only success). It was properly verified afterwards: 3 consecutive ticks succeeded on the rebuilt images.
- MFA (my feature) was only tested at API level. The browser flow is likely broken by the axios 401 interceptor (WEB-01).
- Compliance tests I wrote covered only `_evaluate_rules`; the evaluation path crashes on a wrong column name (SRV-09).

---

## 3. P0 — fix before any production deployment

### Security / tenancy (server)
- [ ] **SRV-01 ✔ Scheduled commands with no device run on every org's devices.** `server/app/tasks/scheduler_tasks.py:44` `select(Device.id).where(~Device.is_revoked)` has no `org_id` filter; `device_id` is optional. *Fix:* filter by `scheduled.org_id`.
- [ ] **SRV-02 ○ Scheduled-command create/update accepts any device UUID** (`scheduled_commands.py:55,82-124`), no org check, no cron validation (invalid cron → 500), not-found returns 200, `command_type` unvalidated. *Fix:* validate device against `current_org_id`, validate cron + type, return 404.
- [ ] **SRV-03 ✔ Any viewer can run arbitrary shell as root/SYSTEM via software updates.** `software_updates.py:62-101` uses `get_current_user`, builds a command with `" ".join(packages)` from unvalidated names, sends it as `shell` (bypasses the admin gate on `shell`). No audit, no size cap. *Fix:* `require_admin`, validate names against `^[A-Za-z0-9._+:-]+$`, shell-quote, audit.
- [ ] **SRV-04 ✔ Alerts delivered to every org's webhooks/emails.** `notification_service.py:28` `select(NotificationRule).where(is_enabled)` — no `org_id` filter. *Fix:* `NotificationRule.org_id == device.org_id`.
- [ ] **SRV-05 ✔ Group endpoints have no org scoping (IDOR).** `groups.py:96,111,126…` use `db.get(DeviceGroup, id)`; `add_devices_to_group` accepts any device UUID. A viewer can read other orgs' device details; an admin can delete other orgs' groups.
- [ ] **SRV-06 ○ Removed org members keep access.** `dependencies.py:51-67` trusts the JWT `org_id` and never checks membership; `remove_member` leaves `users.active_org_id` set so refresh re-mints org access for 30+ days. *Fix:* verify membership per request; clear `active_org_id` on removal.
- [ ] **SRV-07 ○ Global admin is platform-wide.** `admin.py:56-182,269`: an admin of tenant A can list all users, reset tenant B's admin password (account takeover), read B's audit log and live connections. *Fix:* scope to org membership or add a separate superadmin role.
- [ ] **SRV-08 ○ Viewers can mutate and run disruptive commands.** Only `shell`/`script` are admin-gated (`commands.py:25,46`). Viewers can dispatch `reboot`/`update`, edit device metadata, toggle maintenance mode (silences alerts), change agent config, resolve alerts, delete SSH-key inventory, change compliance policies (`devices.py`, `alerts.py`, `compliance.py`). Mostly unaudited. *Fix:* `require_admin` on mutating routes; allowlist read-only types for viewers.
- [ ] **SRV-09 ✔ Compliance evaluation always crashes.** `compliance_service.py:124` orders by `DeviceMetric.timestamp`; the column is `time` → `AttributeError`, `POST /compliance/evaluate` returns 500. Also: no scheduled evaluation task exists, rules are unvalidated, unknown rule types silently pass.

### Web / auth
- [x] **WEB-01 ✔ FIXED — MFA login is likely broken in the browser (my bug).** `frontend/src/api/client.ts:24-48` treats every 401 as an expired session — including `/auth/login`'s "MFA code required" — tries `/auth/refresh`, fails, then `window.location.href='/login'` (hard reload) which wipes the OTP step. *Fix:* skip refresh/redirect for `/auth/*` URLs; use router navigation.

### Deploy / release
- [ ] **OPS-01 ○ No automated migrations; first boot and upgrades race alembic.** `main.py:55` `_seed_admin` runs in lifespan with no try/except against a possibly missing schema; `restart: always` loops; `setup-prod.sh` runs alembic against a restarting container. (Partly observed during my test; not reproduced as a hard crash.) *Fix:* one-shot `migrate` service with `service_completed_successfully` dependency.
- [x] **OPS-02 ○ FIXED (pending CI confirmation) — CI cannot go green and server tests never run.** `ci.yml:77` `uv pip install --system` ignores `uv.lock` (ruff 0.17.1 → 190 lint errors vs 10 with the locked 0.15.22); `ci.yml:80,88` call `.venv/bin/ruff|pytest` which don't exist. *Fix:* `uv sync --locked --extra dev`, `uv run ruff/pytest`, add `[tool.ruff.lint] select=["E4","E7","E9","F"]`, fix the 10 F-errors.
- [~] **OPS-03 ○ PARTIALLY FIXED — Go toolchain vulnerable; release pipeline unsigned and never run for v1.3.0.** 9 stdlib vulns, all "fixed in go1.26.9" (no 1.25 backport); `release.yml` pins 1.25.9. Release builds never get `RELEASE_SIGNING_KEY` (no secret set); `gh release list` shows only v1.2.0. *Fix:* `go 1.26.9` in `go.mod` + both workflows, pin govulncheck, add the signing secret, gate release on tests.
- [x] **OPS-04 ○ FIXED — Sample `.env.example` passes the production safety check.** `SECRET_KEY=change_me_secret_key_at_least_32_chars` is 39 chars so it is accepted; placeholder DB/Redis passwords are never checked. *Fix:* reject `change_me*` values and placeholder passwords.
- [x] **OPS-05 ○ FIXED — Unrecognized `ENVIRONMENT` silently disables the safety check.** `config.py` compares `== "production"`; `prod`/`Production` runs dev mode (`create_all` on the prod DB, `/docs`, dev secret). *Fix:* fail on unknown values.

### Agent / clients
- [ ] **AGT-01 ✔ Windows MSI never writes `agent.toml`.** `dtsys-agent.wxs:72-81`: `SetProperty Id="WriteConfigCmd"` does not match action `WriteConfigExec`; the action only runs `echo [DTSYS_SERVER]`; `Return="ignore"` hides errors; even if wired, it writes flat `server_url=` keys but the agent needs `[server] url=`. Installed service loops on "config not found". The MSI job has never run in CI.
- [ ] **AGT-02 ✔ `skip_time_check = true` is the shipped default** (`packaging/linux/agent.toml.tmpl:20`, `packaging/windows/install.ps1:49`). `transport/tls.go:28-42` then verifies the cert *as of its own NotBefore+1min*, so expired certificates are accepted forever (MITM → command execution + update channel). *Fix:* default false; bound the time window using NTP/server time.
- [ ] **AGT-03 ○ Linux installer runs the agent as unprivileged `dtsys-agent`**, breaking reboot, time sync, SSH-key inventory, journal/dmesg events and the command audit log (`install-agent.sh:234`); the packaged unit says `User=root` with a different binary path — the two contradict. *Decision needed:* run as root with hardening, or unprivileged + explicit sudo/polkit rules; unify the units.
- [ ] **MOB-01 ✔ (if mobile ships) All mobile command and metrics calls hit non-existent routes.** `mobile/src/api/commands.ts:11-17` calls `/commands`; server serves `/devices/{id}/commands`. `DeviceDetailScreen.tsx:316` calls `/metrics/{id}/latest`; server route is `/devices/{id}/metrics/latest`. Dispatch response shape also mismatched (`command_id` vs `id`) and most library command types (`check_updates`, `list_processes`, `network_info`, `shutdown`, …) are rejected by the server allowlist.
- [ ] **MOB-02 ○ Mobile never registers for push notifications** (no `expo-notifications`, no `push-tokens` call) although the server fully implements sending — alerts never reach on-call phones.

---

### Gate-work status (2026-10-16, uncommitted until pushed and CI-confirmed)
- **WEB-01 fixed:** login endpoint 401s no longer trigger refresh/logout/hard reload (`frontend/src/api/client.ts`); 4 new tests.
- **OPS-04/05 fixed:** `assert_production_safe` now rejects placeholder/low-entropy `SECRET_KEY`, short or placeholder admin password, default/empty DB and Redis passwords, weak `METRICS_TOKEN`, placeholder origins; unknown `ENVIRONMENT` values fail at startup. Tests cover each value shipped in `.env.example`.
- **OPS-02 fixed:** CI server job now `uv sync --locked --extra dev` + `uv run ruff/pytest`; ruff scoped to `E4,E7,E9,F` and its 10 real findings fixed; `pip-audit` runs on the locked set.
- **OPS-03 partly fixed:** Go 1.26.9 in `go.mod` and both workflows, `govulncheck` pinned to v1.9.0 and reports 0 affecting vulnerabilities; **found and fixed a side effect — `golang.org/x/sys` v0.21.0 broke `linux/arm64` builds on Go 1.26 (CI cross-compile and release would have failed); bumped to v0.49.0.** Still open: signing secret in CI, MSI ignoring signed binary, release gated on tests, v1.3.0 release never cut.
- **New dependency fixes:** replaced unmaintained `python-jose` (+`ecdsa`, `rsa`) with `PyJWT` (HS256 only; same token format, existing sessions stay valid) and bumped `cryptography` to ≥50 — `pip-audit` now clean (was 5 findings); 6 new token tests (tamper, wrong key, expiry, `alg=none`).
- **WEB-06 partly fixed:** `axios` high-severity resolved (1.20.0). Remaining: `react-router-dom` moderate (open redirect with user-controlled targets — none in this app; fix needs a v7 major upgrade). CI audit now uses `--omit=dev` and exits 0.
- **Note:** the earlier local test stack uses `METRICS_TOKEN=scrape-token` (12 chars), which the new production check rejects — use a ≥16-char token when redeploying it.

## 4. P1 — High

### Server
- **SRV-10 ○** Audit log, CSV export and SSE stream are not org-scoped (`AuditLog` has no org column) and include full shell command text.
- **SRV-11 ○** Webhook SSRF: `webhook_url` unvalidated; worker POSTs to it (`169.254.169.254`, `redis:6379`). Reject private/loopback/link-local, disable redirects.
- **SRV-12 ○** Scheduler poison pill + duplicate runs: offline-device scheduled commands stay `pending` forever; after 100, every dispatch returns 429 and the whole run aborts without advancing `next_run_at`; no row lock, so overlapping ticks can double-fire.
- **SRV-13 ○** Command result/output not bound to the reporting device and can regress a finished command to `running`; `running` is never timed out.
- **SRV-14 ○** Offline detection gaps: clean disconnects create no UptimeEvent/Alert; `check_offline_devices` inserts `Alert` directly (no webhook/email/push/SSE) and never auto-resolves offline alerts; devices in `alert` status are never examined.
- **SRV-15 ○** Revoking or rotating a device key does not disconnect its live WebSocket.
- **SRV-16 ○** Token lifecycle: password change/reset doesn't invalidate tokens; refresh tokens not rotated; users can reset their own password without the current one; no password policy on create; duplicate username → 500.
- **SRV-17 ○** No per-username lockout; login timing reveals valid usernames; LDAP filter injection (no escaping) and LDAP errors silently fall back to local auth; dev `nginx.conf` trusts client `X-Forwarded-For`.
- **SRV-18 ○** Enrollment token is not atomically single-use (GET → register → DELETE; use `GETDEL`); bad token JSON creates a device with no org; two duplicate registration endpoints with different rate-limit keys; fingerprint conflict leaks existing device id across tenants.
- **SRV-19 ○** Bulk commands unaudited, unbounded `device_ids`, no rate limit.
- **SRV-20 ○** Notification rules: update unscoped (cross-org), no DELETE endpoint, no per-channel validation, not-found returns 200.

### Agent
- **AGT-04 ○** A stalled write pump can hang the agent permanently with no reconnect (`transport/client.go:141,269,286`; blocking critical send into a full channel).
- **AGT-05 ○** Windows config readable by all local users and `enrollment_token` stays on disk after enrollment; API key lets any local user impersonate the device.
- **AGT-06 ○** Self-update has no rollback; a signed-but-crashing release bricks the fleet; Linux restart spawns a child then `os.Exit(0)` (races systemd).
- **AGT-07 ○** Windows self-update `.bat` can leave two agents running or silently fail; never deleted; unescaped paths.
- **AGT-08 ○** Installers not idempotent: re-run re-enrolls and overwrites credentials; `.ps1` replaces the exe before stopping the service; no uninstall path; `386` arch not built.
- **AGT-09 ○** Executor output buffered unbounded before the 1 MB cap (`cat /dev/zero` → OOM at `MemoryMax=256M`).
- **AGT-10 ○** Every command runs in a new unbounded goroutine; no command-ID dedupe on the agent (server-side redelivery is fixed, agent still re-executes if resent).
- **AGT-11 ○** Collectors run external commands with no timeout (`winget`, `journalctl`, `dmesg`) — one hang stalls software/event collection forever.
- **AGT-12 ○** Event collection re-reports old kernel/syslog events every ~5 min; `lastChecked` advances even if the send was dropped.
- **AGT-13 ○** Device API key travels in the WebSocket URL (`?token=`), landing in nginx access logs; accept `Authorization: Bearer` instead.
- **AGT-14 ○** Only `shell`/`script` honor `disable_shell`; `reboot`, `sync_time` (sets any clock as root), `update`, `config_update` (intervals down to 1s) are ungated. `script` type has no script semantics (just `command`).

### Frontend
- **WEB-02 ○** Reports always fails silently: requests `limit=1000`, server caps at 500 → 422 (`Reports.tsx:26`).
- **WEB-03 ○** SSE/activity streams capture the access token once and never refresh it; after expiry they reconnect with a stale token every 3–5 s forever.
- **WEB-04 ○ (inferred)** Dashboard may re-render in a loop while a query has no data (`data = []` default + `setState` effect).
- **WEB-05 ○** Everything is silently capped at 100 rows (devices, alerts, stats, search, network map).
- **WEB-06 ○** `axios` (high) and `react-router-dom` (moderate) runtime vulnerabilities; fixed by `npm audit fix` (lockfile is stale).
- **WEB-07 ○** UI role is read from localStorage and never re-derived; several mutating pages are visible to viewers with no error shown on 403.

### Mobile
- **MOB-03 ○** A transient refresh failure wipes the session and the app never learns it (stays "authenticated" with every screen erroring); no single-flight refresh.
- **MOB-04 ○** Logout doesn't call `/auth/logout` or delete the push token. Device list uses `status` (stale) instead of `is_online`; list gauges always empty. No role awareness (viewer can one-tap reboot).
- **MOB-05 ○** 50 npm vulnerabilities (2 critical, 34 high; mostly Expo toolchain); no `eas.json`, bundle id/package, version drift (1.0.0 hard-coded); no CI/build path for mobile.

### Ops
- **OPS-06 ○** Passwords with special characters break the stack (`@ / # $ space "` in URLs; scripts `source .env`).
- **OPS-07 ○** `backup.sh`/`restore.sh` default to the dev compose file; restore drops the DB before checking the extension; doesn't stop writers.
- **OPS-08 ○** No upgrade/rollback procedure or script anywhere; `up -d --build` loses the old image.
- **OPS-09 ○** Root `docker-compose.yml` is labelled production but publishes Postgres/Redis/API on all interfaces, bind-mounts source, no proxy headers or healthchecks. `make docker-up` and the Readme use it.
- **OPS-10 ○** Readme/packaging disagree: manual systemd step breaks a working install; macOS paths inconsistent; Readme claims `/docs` in production (disabled); Go 1.22 claim vs `go.mod` 1.25.9; nonexistent `AGENT_VERSION` setting.
- **OPS-11 ○** `packaging/windows/install.ps1` fetches a file production doesn't serve, no checksum.
- **OPS-12 ○** No Docker log rotation (disk fills), mixed log formats, no retention/PII policy.
- **OPS-13 ○** No container hardening or resource limits; floating image tags; frontend runs nginx as root; Node 20 EOL.

---

## 5. P2 — Medium

**Server:** saved commands have no tenant isolation and a global unique name (SRV-21); retention gaps — `audit_log`, `uptime_events`, `compliance_results` never pruned, `maintenance_until` never enforced (SRV-22); audit coverage gaps for device edit, config, maintenance, group CRUD, org/role changes, notification/saved/compliance changes, software dispatch, bulk, enrollment tokens (SRV-23); org `admin` can promote to `owner`, any user can create orgs, invite leaks username existence, orgs delete leaves dangling refs (SRV-24); SSE/event/activity streams are per-process (2 workers) and `org_id=None` payloads leak to all tenants (SRV-25); malformed agent message drops the WebSocket (no per-message try/except or validation) (SRV-26); alert auto-resolve names inconsistent with creation (`disk_full`/`high_temp`), hardcoded thresholds, racy dedupe (SRV-27); push notifications run inline in the ingest path, dead tokens never pruned (`DeviceNotRegistered`), re-registering a token reassigns it (SRV-28); unbounded queries/N+1 on inventory, software, groups, org devices, compliance evaluate (SRV-29); HTML injection in alert emails (no escaping) (SRV-30); unauthenticated `/status` and `/health` expose fleet counts/versions (SRV-31).

**Agent:** screenshots can't work when running as a service (no DISPLAY / session 0 / TCC) and have no size cap (AGT-15); reconnect backoff has no jitter and never stops for revoked devices (AGT-16); no store-and-forward — stale/lost data while offline (AGT-17); no `SetReadLimit`, no proxy support (AGT-18); SSH-key fingerprint computed wrongly (AGT-19); MSI build: `-X main.Version` has no effect, no `PublicKey`, service not stopped on upgrade, uninstall deletes `agent.toml` (AGT-20).

**Frontend:** no cross-tab logout or idle timeout (WEB-08); org switch ignores errors, relies on hard reload (WEB-09); most pages have no error state (WEB-10); bulk bar ignores single selection, vague errors, no confirm (WEB-11); destructive actions mostly lack confirmation (WEB-12); Alerts summary/export reflect only the current page of 20 (WEB-13); inventory CSV import brittle (split on commas, per-row PATCH) (WEB-14); scheduled commands page has no delete/edit and hides 403 (WEB-15); Reports "uptime %" is max continuous uptime (WEB-16); compliance evaluate refetches before async result (WEB-17); polling volume (2 queries per card, ~12 per DeviceDetail, no visibility pause) (WEB-18); 896-line `DeviceDetail`, no route code-splitting (WEB-19).

**Mobile:** server-URL handling (http allowed with a prompt, saved before login succeeds, no normalization) (MOB-06); no biometric lock or startup session validation (MOB-07); polling continues in background, no offline banner (MOB-08); misc UX/bugs — unconfirmed Resolve, empty-string render crash risk (MOB-09).

**Ops:** Redis has no persistence/memory policy, password in process list (OPS-14); healthchecks give false confidence (`nginx -t`, `/health` returns 200 when degraded) (OPS-15); `healthcheck.sh` wrong against prod (OPS-16); self-signed certs by default, no ACME/renewal (OPS-17); CI hygiene — unpinned actions, no `permissions:`, no Dependabot, duplicate unsigned release job (OPS-18); weak hostname-only device fingerprint (OPS-19); backups unencrypted by default, default umask (OPS-20); stale/tracked artefacts (`deploy_client.sh`, `.claude/settings.local.json`, `.codex`), no LICENSE/SECURITY.md (OPS-21).

## 6. P3 — Low (batch later)

Non-atomic config save; hostname-only fingerprint; inaccurate network/disk telemetry; semver parser rejects pre-release tags; `update` command sends no result; weak single-server UDP NTP check; installer `curl|bash` hygiene and WSL misclassification; legacy `install.ps1` weaker than current; collector gaps (SSH keys, macOS dedupe, Windows HKCU as SYSTEM); packaged macOS plist path mismatch; mobile no deep links, dark mode, tablet layout; frontend accessibility (3 `aria-` attributes, labels without `htmlFor`, no focus traps); theme inconsistency/flash; branding and dashboard layout stored per browser only; `dev-start.sh` prints `admin123`, dev compose binds all interfaces; `Makefile dev-reset` deletes `.env` without confirm; `.gitignore` gaps (`backups/`, `.ruff_cache`, `.claude/`); no host time-sync/firewall guidance.

---

## 7. Missing capabilities (feature gaps)

**Platform / server:** per-org RBAC (org roles unused); org-scoped audit; password change + policy + forgot-password; session/token management and refresh rotation; notification-rule delete, test-send and webhook signing; scheduled compliance + compliance→alert/remediation; command targeting by group/tag; command cancel/approval; alert acknowledge/assign/snooze; maintenance-window expiry job; metrics rollups (hypertable); OIDC/SAML; Redis bridge for SSE across workers; GDPR delete; agent version enforcement; patch install workflow; webhooks beyond notification rules; PSA/ticketing; Wake-on-LAN, lock/wipe; remote desktop/terminal; MDM.

**Agent:** disk/SMART health, battery, GPU; patch/hotfix inventory, pending-reboot state; AV/EDR, firewall, disk-encryption status; logged-in users and login history, local admin accounts, USB devices; proxy support; cert pinning; store-and-forward queue; self-uninstall on revoke; local diagnostics endpoint.

**Web:** devices table view (sort/filter/saved views); device lifecycle UI (revoke, rotate key, delete, bulk tag/group); scheduled command edit/run-now/history; server-side saved dashboards and branding; notification centre; audit date-range filter; profile/password page; session-timeout UX.

**Mobile:** push registration + tap-to-open, alert acknowledge/detail/filters, device search, maintenance toggle, org switching, MFA enrolment, biometric lock, offline cache.

**Ops:** automated migrations + upgrade/rollback scripts; alerting (backup failure, disk, cert expiry, service down); backup monitoring and scheduled restore tests; DR runbook (RPO/RTO); ACME/renewal; log shipping; host hardening guidance; image scanning/SBOM/Dependabot; signed releases enforced in CI + MSI/Authenticode signing; admin/user docs and API reference; staging/smoke test stage; end-to-end agent→server→dashboard test.

---

## 8. Already fixed in this session (for reference)

MFA/TOTP (server, web, mobile login step) · `/metrics` · CSV formula injection · production boot (`.env`, `ALLOWED_ORIGINS`) · `setup-prod.sh` rewrite · cross-worker WebSocket routing via Redis · offline-queued command delivery · command row committed before dispatch · Celery loop-bound connection bug · reconnect no longer re-sends already-`sent` commands (committed, **not yet pushed**) · nginx: real client IP, security headers/HSTS/CSP, DNS re-resolve, `/health` `/metrics` installer routing · scheduled backups + verified restore · executor timeout process-group kill · updater host restriction · ed25519 release signing (built, **not enforced in CI**) · SAN in generated certs · status/installer endpoints in container.

---

## 9. Suggested order of attack

1. **Gate (½ day):** WEB-01 (MFA login), OPS-02 + OPS-03 (CI green: uv lock, ruff scope, Go 1.26.9, `npm audit fix`), OPS-04/05 (safety checks). A green CI is the prerequisite for everything else being trustworthy.
2. **Tenancy & RBAC (2–3 days):** SRV-01…09 together, with a shared `org_scoped_get()` helper and **cross-tenant isolation tests** (org A vs org B for every router). Add `require_admin` to mutating routes (SRV-08, SRV-03).
3. **Deploy safety (1 day):** OPS-01 (migrate service), OPS-06 (password handling), OPS-07/08 (upgrade + backup scripts), OPS-09 (retire root compose), OPS-17 (certbot or documented real cert), OPS-12/13 (log rotation, limits).
4. **Agent hardening (2 days):** AGT-02, AGT-03 decision, AGT-04, AGT-09…12, AGT-13, AGT-05/06; MSI rebuild (AGT-01, AGT-20) and signed release in CI.
5. **Server correctness (1–2 days):** SRV-10…20 (audit scoping, SSRF, scheduler poison pill, offline alerts, token lifecycle, enrollment `GETDEL`).
6. **Frontend (1–2 days):** WEB-02…07, error states, confirmations, pagination.
7. **Mobile (decide scope first):** either ship (MOB-01…05, ~3 days) or explicitly defer and remove from release notes.
8. **Backlog:** P2/P3 and the feature-gap list, by customer need.

**Go/no-go suggestion:** a pilot with one trusted organization on Linux agents becomes reasonable after step 1, the SRV-03/SRV-08 role fixes, OPS-01, and a real TLS certificate. Multi-tenant or Windows rollout should wait for steps 2 and 4.
