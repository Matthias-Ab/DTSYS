#!/usr/bin/env bash
# DTSYS Agent one-liner installer for Linux
# Usage: curl -sSL https://your-server/install.sh | bash
# Or with custom params:
#   SERVER_URL=https://dtsys.example.com ENROLLMENT_TOKEN=xxx bash install.sh

set -euo pipefail

SERVER_URL="${SERVER_URL:-}"
ENROLLMENT_TOKEN="${ENROLLMENT_TOKEN:-}"
AGENT_VERSION="${AGENT_VERSION:-latest}"
INSTALL_DIR="/usr/bin"
CONFIG_DIR="/etc/dtsys"

if [ -z "$SERVER_URL" ] || [ -z "$ENROLLMENT_TOKEN" ]; then
    echo "ERROR: SERVER_URL and ENROLLMENT_TOKEN must be set"
    exit 1
fi

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ARCH_TAG="amd64" ;;
    aarch64) ARCH_TAG="arm64" ;;
    *)       echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

echo ">>> Installing DTSYS agent ($ARCH_TAG)"

# Download binary
DOWNLOAD_URL="${SERVER_URL}/api/v1/downloads/dtsys-agent-linux-${ARCH_TAG}"
curl -sSL -o /tmp/dtsys-agent "$DOWNLOAD_URL"
chmod +x /tmp/dtsys-agent
mv /tmp/dtsys-agent "$INSTALL_DIR/dtsys-agent"

# Write config
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

cat > "$CONFIG_DIR/agent.toml" <<EOF
[server]
url = "${SERVER_URL}"
enrollment_token = "${ENROLLMENT_TOKEN}"

[agent]

[collect]
telemetry_interval_secs = 60
software_scan_interval_m = 60
event_poll_interval_secs = 120
EOF

chmod 600 "$CONFIG_DIR/agent.toml"

# Install systemd service
cat > /etc/systemd/system/dtsys-agent.service <<EOF
[Unit]
Description=DTSYS Device Management Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/dtsys-agent --config ${CONFIG_DIR}/agent.toml
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dtsys-agent
systemctl restart dtsys-agent

echo ">>> DTSYS agent installed and started"
echo ">>> Check status: systemctl status dtsys-agent"
