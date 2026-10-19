package transport

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net/url"
	"time"

	"github.com/dtsys/agent/internal/version"
)

// BuildTLSConfig creates a TLS config. With skipTimeCheck=false it is plain,
// strict TLS verification. With skipTimeCheck=true (devices whose clock is
// unreliable, e.g. after losing their RTC battery) verification is still done —
// chain and hostname — but certificate *validity dates* are checked against a
// time chosen by verificationTime, never simply ignored.
func BuildTLSConfig(serverURL string, skipTimeCheck bool) *tls.Config {
	u, err := url.Parse(serverURL)
	if err != nil {
		return &tls.Config{}
	}
	serverName := u.Hostname()
	if serverName == "" {
		return &tls.Config{}
	}
	if !skipTimeCheck {
		return &tls.Config{ServerName: serverName}
	}

	buildTime := parseBuildDate(version.BuildDate)
	return &tls.Config{
		ServerName:         serverName,
		InsecureSkipVerify: true, // verification is done below in VerifyConnection
		VerifyConnection: func(cs tls.ConnectionState) error {
			return verifyPeer(cs.PeerCertificates, serverName, nil, time.Now(), buildTime)
		},
	}
}

func parseBuildDate(value string) time.Time {
	t, err := time.Parse("2006-01-02", value)
	if err != nil {
		return time.Time{}
	}
	return t
}

// verificationTime returns the instant at which a certificate's validity window
// is evaluated.
//
//   - Clock looks sane (not earlier than this agent's build date): use the real
//     time. An expired certificate is therefore rejected, so an attacker holding an
//     old expired certificate for the server's hostname cannot impersonate it.
//   - Clock is earlier than the build date, which is impossible for a correct clock:
//     it cannot be trusted. Evaluate at NotBefore+1m instead, but still refuse a
//     certificate that had already expired when this agent was built.
func verificationTime(now, notBefore, notAfter, build time.Time) (time.Time, error) {
	if build.IsZero() || !now.Before(build) {
		return now, nil
	}
	if notAfter.Before(build) {
		return time.Time{}, fmt.Errorf("server certificate expired before this agent was built (%s)", notAfter.Format(time.RFC3339))
	}
	return notBefore.Add(time.Minute), nil
}

// verifyPeer validates the chain and hostname. roots == nil means the system pool.
func verifyPeer(certs []*x509.Certificate, serverName string, roots *x509.CertPool, now, build time.Time) error {
	if len(certs) == 0 {
		return errors.New("no peer certificate")
	}
	leaf := certs[0]
	at, err := verificationTime(now, leaf.NotBefore, leaf.NotAfter, build)
	if err != nil {
		return err
	}
	opts := x509.VerifyOptions{
		DNSName:       serverName,
		Roots:         roots,
		Intermediates: x509.NewCertPool(),
		CurrentTime:   at,
	}
	for _, cert := range certs[1:] {
		opts.Intermediates.AddCert(cert)
	}
	_, err = leaf.Verify(opts)
	return err
}
