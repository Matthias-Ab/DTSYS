// Package releasesig defines how agent release binaries are signed and verified.
//
// A release is signed offline with an ed25519 key. The matching public key is
// compiled into the agent, so a compromised update server cannot push a binary
// the operator did not sign. The signed message binds the version, platform,
// architecture and binary digest, which prevents replaying an old (vulnerable)
// release or a binary built for another platform.
package releasesig

import (
	"crypto/ed25519"
	"encoding/base64"
	"fmt"
	"strings"
)

// Message returns the exact bytes that are signed for a release binary.
func Message(version, platform, arch, sha256Hex string) []byte {
	return []byte(fmt.Sprintf("dtsys-agent-release-v1|%s|%s|%s|%s",
		strings.TrimPrefix(strings.TrimSpace(version), "v"),
		platform, arch, strings.ToLower(sha256Hex)))
}

// Sign produces a base64 signature for a release.
func Sign(priv ed25519.PrivateKey, version, platform, arch, sha256Hex string) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(priv, Message(version, platform, arch, sha256Hex)))
}

// Verify checks a base64 signature against a base64-encoded ed25519 public key.
func Verify(pubKeyB64, version, platform, arch, sha256Hex, sigB64 string) error {
	pubBytes, err := base64.StdEncoding.DecodeString(strings.TrimSpace(pubKeyB64))
	if err != nil || len(pubBytes) != ed25519.PublicKeySize {
		return fmt.Errorf("invalid embedded release public key")
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(sigB64))
	if err != nil || len(sig) != ed25519.SignatureSize {
		return fmt.Errorf("missing or malformed release signature")
	}
	if !ed25519.Verify(ed25519.PublicKey(pubBytes), Message(version, platform, arch, sha256Hex), sig) {
		return fmt.Errorf("release signature verification failed")
	}
	return nil
}
