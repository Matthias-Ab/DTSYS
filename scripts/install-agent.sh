#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: curl -fsSL https://YOUR_SERVER/install-agent.sh | sudo bash -s -- --server https://YOUR_SERVER --token ENROLLMENT_TOKEN" >&2
  echo "       add --allow-insecure-http to permit a plain http:// server (dev/test only)" >&2
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

SERVER_URL=""
ENROLLMENT_TOKEN=""
ALLOW_INSECURE_HTTP="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --server)
      SERVER_URL="${2:-}"
      shift 2
      ;;
    --token)
      ENROLLMENT_TOKEN="${2:-}"
      shift 2
      ;;
    --allow-insecure-http)
      ALLOW_INSECURE_HTTP="true"
      shift 1
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$SERVER_URL" ] || [ -z "$ENROLLMENT_TOKEN" ]; then
  echo "Error: --server and --token are required." >&2
  usage
  exit 1
fi

case "$SERVER_URL" in
  https://*)
    ;;
  http://*)
    if [ "$ALLOW_INSECURE_HTTP" != "true" ]; then
      echo "Error: --server must use https:// (the agent will otherwise send its enrollment token and API key in plaintext)." >&2
      echo "       Pass --allow-insecure-http to override for local/dev testing only." >&2
      exit 1
    fi
    echo "WARNING: installing over plaintext http:// — credentials will be sent unencrypted." >&2
    ;;
  *)
    echo "Error: --server must start with https:// or http://" >&2
    exit 1
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: run as root (use sudo)." >&2
  exit 1
fi

OS="$(uname -s)"
ARCH_RAW="$(uname -m)"

PLATFORM=""
if [ "$OS" = "Linux" ]; then
  if [ -f /proc/version ] && grep -qi microsoft /proc/version; then
    PLATFORM="windows"
  else
    PLATFORM="linux"
  fi
elif [ "$OS" = "Darwin" ]; then
  PLATFORM="darwin"
else
  echo "Unsupported OS: $OS" >&2
  exit 1
fi

case "$ARCH_RAW" in
  x86_64|amd64)
    ARCH="amd64"
    ;;
  aarch64|arm64)
    ARCH="arm64"
    ;;
  *)
    echo "Unsupported architecture: $ARCH_RAW" >&2
    exit 1
    ;;
esac

if [ "$PLATFORM" = "windows" ]; then
  echo "WSL detected; downloading Windows agent binary." >&2
fi

BIN_URL="${SERVER_URL}/api/v1/agent/download?arch=${ARCH}&platform=${PLATFORM}"
INSTALL_DIR="/usr/local/bin"
if [ "$PLATFORM" = "linux" ]; then
  # A dedicated, dtsys-owned directory (rather than the root-owned
  # /usr/local/bin) so the unprivileged dtsys-agent service user can
  # replace its own binary in place when auto_update runs.
  INSTALL_DIR="/opt/dtsys"
fi
BIN_PATH="${INSTALL_DIR}/dtsys-agent"
if [ "$PLATFORM" = "windows" ]; then
  BIN_PATH="${INSTALL_DIR}/dtsys-agent.exe"
fi

if [ "$PLATFORM" = "linux" ]; then
  if ! id -u dtsys-agent >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin dtsys-agent
  fi
fi

mkdir -p "$INSTALL_DIR"
if [ "$PLATFORM" = "linux" ]; then
  chown dtsys-agent:dtsys-agent "$INSTALL_DIR"
fi

VERSION_RESPONSE="$(curl -fsS "${SERVER_URL}/api/v1/agent/version?arch=${ARCH}&platform=${PLATFORM}")"
EXPECTED_SHA256="$(printf '%s' "$VERSION_RESPONSE" | python3 -c 'import json,sys; print((json.loads(sys.stdin.read()).get("sha256") or "").lower())')"

if [ -z "$EXPECTED_SHA256" ] || [ "${#EXPECTED_SHA256}" -ne 64 ]; then
  echo "Error: server did not provide a sha256 checksum for this build; refusing to install an unverified binary." >&2
  exit 1
fi

echo "Downloading agent from ${BIN_URL}..."
TMP_BIN="$(mktemp)"
curl -fsSL "$BIN_URL" -o "$TMP_BIN"

ACTUAL_SHA256="$(sha256sum "$TMP_BIN" | awk '{print $1}')"
if [ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]; then
  rm -f "$TMP_BIN"
  echo "Error: checksum mismatch — expected ${EXPECTED_SHA256}, got ${ACTUAL_SHA256}. Refusing to install." >&2
  exit 1
fi

mv "$TMP_BIN" "$BIN_PATH"
chmod +x "$BIN_PATH"

HOSTNAME="$(hostname)"
OS_VERSION="$(uname -r)"
if [ "$PLATFORM" = "darwin" ]; then
  OS_VERSION="$(sw_vers -productVersion)"
fi

FINGERPRINT="$(printf '%s' "$HOSTNAME" | openssl dgst -sha256 | awk '{print $2}')"

HOSTNAME_VALUE="$HOSTNAME" PLATFORM_VALUE="$PLATFORM" OS_VERSION_VALUE="$OS_VERSION" ARCH_VALUE="$ARCH" FINGERPRINT_VALUE="$FINGERPRINT" TOKEN_VALUE="$ENROLLMENT_TOKEN" \
ENROLL_PAYLOAD="$(python3 - <<'PY'
import json, os
payload = {
  "hostname": os.environ.get("HOSTNAME_VALUE"),
  "os_type": os.environ.get("PLATFORM_VALUE"),
  "os_version": os.environ.get("OS_VERSION_VALUE"),
  "arch": os.environ.get("ARCH_VALUE"),
  "fingerprint": os.environ.get("FINGERPRINT_VALUE"),
  "enrollment_token": os.environ.get("TOKEN_VALUE"),
}
print(json.dumps(payload))
PY
)"

ENROLL_RESPONSE="$(curl -fsS -X POST "${SERVER_URL}/api/v1/enroll" \
  -H 'Content-Type: application/json' \
  -d "${ENROLL_PAYLOAD}")"

DEVICE_ID="$(printf '%s' "$ENROLL_RESPONSE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("device_id",""))')"
API_KEY="$(printf '%s' "$ENROLL_RESPONSE" | python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("api_key",""))')"

if [ -z "$DEVICE_ID" ] || [ -z "$API_KEY" ]; then
  echo "Enrollment failed: ${ENROLL_RESPONSE}" >&2
  exit 1
fi

CONFIG_DIR="/etc/dtsys"
CONFIG_PATH="${CONFIG_DIR}/agent.toml"
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_PATH" <<EOF
[server]
url = "${SERVER_URL}"

[agent]
device_id = "${DEVICE_ID}"
api_key = "${API_KEY}"

[collect]
telemetry_interval_secs = 60
software_scan_interval_m = 60
event_poll_interval_secs = 120

[events]
dedup_max_entries = 50
exclude_patterns = ["event handler.*EOF", "event streamer.*EOF"]
rate_limit_max = 20
rate_limit_window_s = 30

[tls]
skip_time_check = false

[update]
auto_update = true
check_interval_hours = 6
EOF
chmod 600 "$CONFIG_PATH"

if [ "$PLATFORM" = "linux" ]; then
  chown dtsys-agent:dtsys-agent "$CONFIG_PATH"
  SERVICE_PATH="/etc/systemd/system/dtsys-agent.service"
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=DTSYS Device Management Agent
Documentation=https://github.com/dejazmach28/DTSYS
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=${BIN_PATH} --config /etc/dtsys/agent.toml
Restart=always
RestartSec=10
RestartPreventExitStatus=
TimeoutStartSec=30
TimeoutStopSec=30

User=dtsys-agent
Group=dtsys-agent
NoNewPrivileges=yes

MemoryMax=256M
CPUQuota=20%

StandardOutput=journal
StandardError=journal
SyslogIdentifier=dtsys-agent

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now dtsys-agent
elif [ "$PLATFORM" = "darwin" ]; then
  PLIST_PATH="/Library/LaunchDaemons/com.dtsys.agent.plist"
  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.dtsys.agent</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/local/bin/dtsys-agent</string>
      <string>--config</string>
      <string>/etc/dtsys/agent.toml</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/var/log/dtsys-agent.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/dtsys-agent.log</string>
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
EOF
  launchctl load -w "$PLIST_PATH"
fi

echo "DTSYS agent installed successfully. Device ID: ${DEVICE_ID}"
