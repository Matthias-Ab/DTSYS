#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/client"

mkdir -p ../dist
VERSION="$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')"
if [ -z "$VERSION" ]; then
  VERSION="0.0.0"
fi

LDFLAGS="-s -w -X github.com/dtsys/agent/internal/version.Version=${VERSION} -X github.com/dtsys/agent/internal/version.BuildDate=$(date -u +%Y-%m-%d)"

# Signed releases: set RELEASE_SIGNING_KEY to the secret key file from
# `go run ./cmd/release-sign keygen -out keys/`. The matching public key is
# compiled into every agent so it only accepts updates signed with that key.
if [ -n "${RELEASE_SIGNING_KEY:-}" ]; then
  RELEASE_PUBLIC_KEY="$(cat "${RELEASE_PUBLIC_KEY_FILE:-${RELEASE_SIGNING_KEY%.key}.pub}")"
  LDFLAGS="${LDFLAGS} -X github.com/dtsys/agent/internal/updater.PublicKey=${RELEASE_PUBLIC_KEY}"
else
  echo "WARNING: RELEASE_SIGNING_KEY not set; building UNSIGNED agents (self-update falls back to checksum only)." >&2
fi

GOOS=linux   GOARCH=amd64 go build -ldflags="${LDFLAGS}" -o ../dist/dtsys-agent-linux-amd64         ./cmd/agent/
GOOS=linux   GOARCH=arm64 go build -ldflags="${LDFLAGS}" -o ../dist/dtsys-agent-linux-arm64         ./cmd/agent/
GOOS=darwin  GOARCH=amd64 go build -ldflags="${LDFLAGS}" -o ../dist/dtsys-agent-darwin-amd64        ./cmd/agent/
GOOS=darwin  GOARCH=arm64 go build -ldflags="${LDFLAGS}" -o ../dist/dtsys-agent-darwin-arm64        ./cmd/agent/
GOOS=windows GOARCH=amd64 go build -ldflags="${LDFLAGS}" -o ../dist/dtsys-agent-windows-amd64.exe   ./cmd/agent/

echo "$VERSION" > ../dist/version.txt

# Checksums let the agent's self-updater and install scripts verify a downloaded
# binary before executing/installing it, instead of trusting the transport alone.
cd ../dist
for bin in dtsys-agent-linux-amd64 dtsys-agent-linux-arm64 dtsys-agent-darwin-amd64 dtsys-agent-darwin-arm64 dtsys-agent-windows-amd64.exe; do
  sha256sum "$bin" | awk '{print $1}' > "${bin}.sha256"
done
cd - >/dev/null

if [ -n "${RELEASE_SIGNING_KEY:-}" ]; then
  cd "$ROOT_DIR/client"
  for target in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64; do
    platform="${target%-*}"; arch="${target#*-}"
    bin="dtsys-agent-${target}"; [ "$platform" = "windows" ] && bin="${bin}.exe"
    go run ./cmd/release-sign sign -key "$RELEASE_SIGNING_KEY" -version "$VERSION" -platform "$platform" -arch "$arch" -file "../dist/${bin}" > "../dist/${bin}.sig"
  done
  cd - >/dev/null
  echo "Signed all agent binaries."
fi

echo "Built all agent binaries in dist/"
