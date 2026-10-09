# DTSYS TODO — Missing Core Pieces

Findings from a quick repo scan (not a line-by-line audit). Items marked "verify" need confirming in code.

## Testing
- [x] Frontend test runner: vitest + jsdom set up and wired into CI (initial tests for cron, tags, authStore)
- [ ] Frontend: component tests (Testing Library) and Playwright e2e
- [ ] Mobile app tests
- [x] Server tests: compliance rule engine, audit (service, admin gating, stream ticket, CSV export)
- [ ] Server tests still missing: notifications, scheduled commands, organizations, software updates, WebSocket layer
- [ ] Go agent tests: executor, updater, software collectors (only NTP, hardware, messages covered)
- [ ] End-to-end test: agent -> server -> dashboard

## Security & Identity
- [x] MFA / TOTP: server (setup/enable/disable, replay protection, encrypted secret, migration `a3b4c5d6e7f8`), web login + Settings card
- [x] MFA on the mobile app login screen
- [ ] MFA recovery codes and optional admin-enforced MFA
- [ ] OIDC / SAML SSO (only LDAP exists today)
- [ ] Fine-grained RBAC (verify; basic roles and org scoping appear to exist)
- [ ] Signed agent releases / updater verification (verify)
- [ ] Remote command signing or allowlisting (verify)

## Observability & Operations
- [x] Prometheus `/metrics` endpoint (bearer `METRICS_TOKEN`; disabled when unset)
- [ ] OpenTelemetry tracing
- [ ] Sentry (or equivalent) error reporting
- [ ] HA / horizontal scaling: multiple server replicas, WebSocket fan-out via Redis
- [ ] Scheduled backups and restore testing (currently shell scripts only)
- [x] Repo hygiene: `go1.22.4.linux-amd64.tar.gz`, `dist/`, `.codex` were already gitignored and untracked

## Device Management Features
- [ ] Patch install / approval workflow (update detection exists, installation does not)
- [ ] Remote desktop / interactive terminal (only one-shot commands and screenshots today)
- [ ] Wake-on-LAN, reboot, lock/wipe actions
- [ ] MDM for iOS / Android devices
- [ ] Webhooks
- [ ] PSA / ticketing integration
- [ ] Slack / Teams notification channels (only email and push today)
- [ ] API tokens for automation and public API docs

## Data & Architecture
- [ ] Metrics retention and downsampling policy (verify what `cleanup_tasks.py` does)
- [ ] Multi-tenant isolation review (organizations exist; verify enforcement)

## Docs & Process
- [ ] User and admin documentation
- [ ] API reference
- [ ] Contribution guide
