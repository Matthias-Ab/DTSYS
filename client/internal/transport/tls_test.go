package transport

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"testing"
	"time"
)

type testPKI struct {
	roots *x509.CertPool
	ca    *x509.Certificate
	caKey *ecdsa.PrivateKey
}

func newPKI(t *testing.T) *testPKI {
	t.Helper()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "test-ca"},
		NotBefore: time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC), NotAfter: time.Date(2040, 1, 1, 0, 0, 0, 0, time.UTC),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	ca, _ := x509.ParseCertificate(der)
	pool := x509.NewCertPool()
	pool.AddCert(ca)
	return &testPKI{roots: pool, ca: ca, caKey: key}
}

func (p *testPKI) leaf(t *testing.T, host string, notBefore, notAfter time.Time) *x509.Certificate {
	t.Helper()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()), Subject: pkix.Name{CommonName: host}, DNSNames: []string{host},
		NotBefore: notBefore, NotAfter: notAfter, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, p.ca, &key.PublicKey, p.caKey)
	if err != nil {
		t.Fatal(err)
	}
	cert, _ := x509.ParseCertificate(der)
	return cert
}

var (
	buildTime = time.Date(2026, 4, 15, 0, 0, 0, 0, time.UTC)
	saneNow   = time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	brokenNow = time.Date(1970, 1, 1, 0, 5, 0, 0, time.UTC) // device that lost its RTC
)

func TestSaneClockRejectsExpiredCertificate(t *testing.T) {
	pki := newPKI(t)
	expired := pki.leaf(t, "dtsys.example.com", time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2025, 4, 1, 0, 0, 0, 0, time.UTC))
	// This is the attack the old code allowed: skip_time_check validated at NotBefore+1m.
	if err := verifyPeer([]*x509.Certificate{expired}, "dtsys.example.com", pki.roots, saneNow, buildTime); err == nil {
		t.Fatal("expired certificate accepted on a device with a sane clock")
	}
}

func TestSaneClockAcceptsValidCertificate(t *testing.T) {
	pki := newPKI(t)
	valid := pki.leaf(t, "dtsys.example.com", saneNow.Add(-24*time.Hour), saneNow.Add(60*24*time.Hour))
	if err := verifyPeer([]*x509.Certificate{valid}, "dtsys.example.com", pki.roots, saneNow, buildTime); err != nil {
		t.Fatalf("valid certificate rejected: %v", err)
	}
}

func TestBrokenClockStillWorksWithCurrentCertificate(t *testing.T) {
	pki := newPKI(t)
	// Issued after this agent was built, device clock says 1970: the case skip_time_check exists for.
	valid := pki.leaf(t, "dtsys.example.com", saneNow.Add(-24*time.Hour), saneNow.Add(60*24*time.Hour))
	if err := verifyPeer([]*x509.Certificate{valid}, "dtsys.example.com", pki.roots, brokenNow, buildTime); err != nil {
		t.Fatalf("device with a broken clock could not connect: %v", err)
	}
}

func TestBrokenClockStillRejectsCertificateExpiredBeforeBuild(t *testing.T) {
	pki := newPKI(t)
	old := pki.leaf(t, "dtsys.example.com", time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC))
	if err := verifyPeer([]*x509.Certificate{old}, "dtsys.example.com", pki.roots, brokenNow, buildTime); err == nil {
		t.Fatal("certificate that expired before the agent was built was accepted")
	}
}

func TestHostnameAndChainAreStillEnforced(t *testing.T) {
	pki := newPKI(t)
	valid := pki.leaf(t, "dtsys.example.com", saneNow.Add(-time.Hour), saneNow.Add(time.Hour*24))
	for name, now := range map[string]time.Time{"sane clock": saneNow, "broken clock": brokenNow} {
		if err := verifyPeer([]*x509.Certificate{valid}, "evil.example.net", pki.roots, now, buildTime); err == nil {
			t.Errorf("%s: wrong hostname accepted", name)
		}
		other := newPKI(t)
		if err := verifyPeer([]*x509.Certificate{valid}, "dtsys.example.com", other.roots, now, buildTime); err == nil {
			t.Errorf("%s: certificate from an untrusted CA accepted", name)
		}
	}
	if err := verifyPeer(nil, "dtsys.example.com", pki.roots, saneNow, buildTime); err == nil {
		t.Error("empty chain accepted")
	}
}

func TestUnparseableBuildDateFailsClosed(t *testing.T) {
	pki := newPKI(t)
	expired := pki.leaf(t, "dtsys.example.com", time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2025, 4, 1, 0, 0, 0, 0, time.UTC))
	// An unknown build date must mean strict verification, not relaxed.
	if err := verifyPeer([]*x509.Certificate{expired}, "dtsys.example.com", pki.roots, brokenNow, parseBuildDate("garbage")); err == nil {
		t.Fatal("expired certificate accepted when build date is unknown")
	}
}
