#!/usr/bin/env bash
# Generates a SELF-SIGNED certificate for development or an initial dry run.
# Usage: bash scripts/generate_certs.sh [hostname ...]    (default: localhost)
#
# The certificate carries subjectAltName entries. This matters: Go (the agent) and
# modern browsers ignore the CN and reject certificates without a matching SAN.
# For production use a real certificate (e.g. Let's Encrypt) in nginx/ssl/.
set -euo pipefail

HOSTS=("$@")
[ ${#HOSTS[@]} -eq 0 ] && HOSTS=(localhost)

SAN="IP:127.0.0.1"
for host in "${HOSTS[@]}"; do
  SAN="DNS:${host},${SAN}"
done

mkdir -p nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/key.pem \
  -out nginx/ssl/cert.pem \
  -subj "/O=DTSYS/CN=${HOSTS[0]}" \
  -addext "subjectAltName=${SAN}"

cat <<EOT
Generated self-signed certificates for: ${HOSTS[*]}
  nginx/ssl/cert.pem
  nginx/ssl/key.pem

Trusting the certificate:
  Linux: import cert.pem into your browser or system trust store.
  macOS: open Keychain Access, import cert.pem, and set it to Always Trust.
  Windows: open cert.pem and install it into Trusted Root Certification Authorities.
  Agents: set SSL_CERT_FILE=/path/to/cert.pem (Linux) or install it in the OS trust store.
EOT
