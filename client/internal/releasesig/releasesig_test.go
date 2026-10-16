package releasesig

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"strings"
	"testing"
)

func keypair(t *testing.T) (string, ed25519.PrivateKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(pub), priv
}

const digest = "ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12ab12"

func TestSignVerifyRoundTrip(t *testing.T) {
	pub, priv := keypair(t)
	sig := Sign(priv, "1.4.0", "linux", "amd64", digest)
	if err := Verify(pub, "v1.4.0", "linux", "amd64", strings.ToUpper(digest), sig); err != nil {
		t.Fatalf("valid signature rejected: %v", err)
	}
}

func TestVerifyRejectsTampering(t *testing.T) {
	pub, priv := keypair(t)
	sig := Sign(priv, "1.4.0", "linux", "amd64", digest)
	otherPub, _ := keypair(t)

	cases := map[string]func() error{
		"different digest":  func() error { return Verify(pub, "1.4.0", "linux", "amd64", strings.Repeat("0", 64), sig) },
		"downgrade/replay":  func() error { return Verify(pub, "1.3.0", "linux", "amd64", digest, sig) },
		"other platform":    func() error { return Verify(pub, "1.4.0", "windows", "amd64", digest, sig) },
		"other arch":        func() error { return Verify(pub, "1.4.0", "linux", "arm64", digest, sig) },
		"wrong key":         func() error { return Verify(otherPub, "1.4.0", "linux", "amd64", digest, sig) },
		"empty signature":   func() error { return Verify(pub, "1.4.0", "linux", "amd64", digest, "") },
		"garbage signature": func() error { return Verify(pub, "1.4.0", "linux", "amd64", digest, "!!notbase64!!") },
		"short signature":   func() error { return Verify(pub, "1.4.0", "linux", "amd64", digest, "AAAA") },
		"bad embedded key":  func() error { return Verify("nope", "1.4.0", "linux", "amd64", digest, sig) },
	}
	for name, fn := range cases {
		if err := fn(); err == nil {
			t.Errorf("%s: expected verification failure", name)
		}
	}
}
