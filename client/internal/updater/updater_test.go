package updater

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/dtsys/agent/internal/releasesig"
)

func TestParseSemver(t *testing.T) {
	good := map[string][3]int{
		"1.2.3":   {1, 2, 3},
		"v1.3.0":  {1, 3, 0},
		" 2.0 ":   {2, 0, 0},
		"10.20.3": {10, 20, 3},
	}
	for in, want := range good {
		got, err := parseSemver(in)
		if err != nil || got != want {
			t.Errorf("parseSemver(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	for _, in := range []string{"", "abc", "1.x.3"} {
		if _, err := parseSemver(in); err == nil {
			t.Errorf("parseSemver(%q) expected error", in)
		}
	}
}

func TestIsNewerVersion(t *testing.T) {
	cases := []struct {
		remote, current string
		want            bool
	}{
		{"1.3.1", "1.3.0", true},
		{"1.4.0", "1.3.9", true},
		{"2.0.0", "1.9.9", true},
		{"1.3.0", "1.3.0", false},
		{"1.2.9", "1.3.0", false},
		{"v1.10.0", "1.9.0", true},
	}
	for _, c := range cases {
		got, err := isNewerVersion(c.remote, c.current)
		if err != nil || got != c.want {
			t.Errorf("isNewerVersion(%q,%q) = %v, %v; want %v", c.remote, c.current, got, err, c.want)
		}
	}
	if _, err := isNewerVersion("bad", "1.0.0"); err == nil {
		t.Error("expected error for bad remote version")
	}
	if _, err := isNewerVersion("1.0.0", "bad"); err == nil {
		t.Error("expected error for bad current version")
	}
}

func TestBuildVersionURL(t *testing.T) {
	got, err := buildVersionURL("wss://dtsys.example.com/")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"https://dtsys.example.com/api/v1/agent/version?", "platform=" + runtime.GOOS, "arch=" + runtime.GOARCH} {
		if !strings.Contains(got, want) {
			t.Errorf("buildVersionURL = %q, missing %q", got, want)
		}
	}
	got, _ = buildVersionURL("ws://localhost:8000")
	if !strings.HasPrefix(got, "http://localhost:8000/api/v1/agent/version") {
		t.Errorf("unexpected url %q", got)
	}
}

func TestResolveDownloadURL(t *testing.T) {
	base := "https://dtsys.example.com/api/v1/agent/version?platform=linux"

	ok := map[string]string{
		"/downloads/agent":                   "https://dtsys.example.com/downloads/agent",
		"downloads/agent":                    "https://dtsys.example.com/api/v1/agent/downloads/agent",
		"https://DTSYS.example.com/dl/agent": "https://DTSYS.example.com/dl/agent",
	}
	for raw, want := range ok {
		got, err := resolveDownloadURL(base, raw)
		if err != nil || got != want {
			t.Errorf("resolveDownloadURL(%q) = %q, %v; want %q", raw, got, err, want)
		}
	}

	// The API key rides along on the download request, so off-host and
	// scheme-downgrade URLs must be refused.
	for _, raw := range []string{
		"https://evil.example.net/agent",
		"//evil.example.net/agent",
		"http://dtsys.example.com/agent",
		"https://dtsys.example.com:8443/agent",
	} {
		if got, err := resolveDownloadURL(base, raw); err == nil {
			t.Errorf("resolveDownloadURL(%q) = %q; expected refusal", raw, got)
		}
	}
}

func TestDownloadBinaryHashesAndSendsAuth(t *testing.T) {
	payload := []byte("binary-bytes")
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		_, _ = w.Write(payload)
	}))
	defer srv.Close()

	f, err := os.CreateTemp(t.TempDir(), "dl")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()

	sum, err := downloadBinary(context.Background(), srv.URL, "key123", f, srv.Client())
	if err != nil {
		t.Fatal(err)
	}
	want := sha256.Sum256(payload)
	if sum != hex.EncodeToString(want[:]) {
		t.Errorf("sha mismatch: %s", sum)
	}
	if gotAuth != "Bearer key123" {
		t.Errorf("auth header = %q", gotAuth)
	}
}

func TestDownloadBinaryNon200(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "nope", http.StatusNotFound)
	}))
	defer srv.Close()
	f, _ := os.CreateTemp(t.TempDir(), "dl")
	defer f.Close()
	if _, err := downloadBinary(context.Background(), srv.URL, "", f, srv.Client()); err == nil {
		t.Error("expected error on 404")
	}
}

// CheckAndUpdate replaces os.Executable() on success, which in a test would
// overwrite the test binary itself. These cases all fail before that step.
func TestCheckAndUpdateRefusals(t *testing.T) {
	newer := "99.0.0"
	cases := []struct {
		name    string
		body    string
		status  int
		wantErr string
	}{
		{"server error", `{}`, 500, "status 500"},
		{"no checksum", `{"version":"` + newer + `","download_url":"/dl"}`, 200, "sha256"},
		{"short checksum", `{"version":"` + newer + `","download_url":"/dl","sha256":"abc"}`, 200, "sha256"},
		{"off-host download", `{"version":"` + newer + `","download_url":"https://evil.example.net/a","sha256":"` + strings.Repeat("a", 64) + `"}`, 200, "different host"},
		{"bad version", `{"version":"zzz","download_url":"/dl","sha256":"` + strings.Repeat("a", 64) + `"}`, 200, "parse remote version"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(c.status)
				_, _ = w.Write([]byte(c.body))
			}))
			defer srv.Close()
			updated, err := CheckAndUpdate(context.Background(), srv.URL, "1.0.0", "dev", "key", nil)
			if updated || err == nil || !strings.Contains(err.Error(), c.wantErr) {
				t.Errorf("updated=%v err=%v; want error containing %q", updated, err, c.wantErr)
			}
		})
	}
}

func TestCheckAndUpdateNoopCases(t *testing.T) {
	for name, body := range map[string]string{
		"empty response": `{}`,
		"same version":   `{"version":"1.0.0","download_url":"/dl","sha256":"` + strings.Repeat("a", 64) + `"}`,
		"older version":  `{"version":"0.9.0","download_url":"/dl","sha256":"` + strings.Repeat("a", 64) + `"}`,
	} {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
			defer srv.Close()
			updated, err := CheckAndUpdate(context.Background(), srv.URL, "1.0.0", "", "", nil)
			if updated || err != nil {
				t.Errorf("updated=%v err=%v; want no-op", updated, err)
			}
		})
	}
}

func TestWindowsArgsSuffix(t *testing.T) {
	if got := windowsArgsSuffix(nil); got != "" {
		t.Errorf("got %q", got)
	}
	if got := windowsArgsSuffix([]string{"--config", `a "b"`}); got != ` "--config" "a ""b"""` {
		t.Errorf("got %q", got)
	}
}

func TestStageWindowsUpdateWritesScriptAndRenames(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "agent.exe")
	dl := filepath.Join(dir, "dl.tmp")
	if err := os.WriteFile(dl, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := stageWindowsUpdate(exe, dl); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "agent.new.exe")); err != nil {
		t.Errorf("staged binary missing: %v", err)
	}
	script, err := os.ReadFile(filepath.Join(dir, "dtsys-update.bat"))
	if err != nil || !strings.Contains(string(script), "agent.new.exe") {
		t.Errorf("bad script: %v %q", err, script)
	}
}

func TestCheckAndUpdateEnforcesSignatureWhenKeyEmbedded(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	oldKey := PublicKey
	PublicKey = base64.StdEncoding.EncodeToString(pub)
	t.Cleanup(func() { PublicKey = oldKey })

	binary := []byte("new-agent-binary")
	sum := sha256.Sum256(binary)
	digest := hex.EncodeToString(sum[:])
	const version = "99.0.0"

	_, otherPriv, _ := ed25519.GenerateKey(rand.Reader)
	cases := map[string]struct{ sig, wantErr string }{
		"missing signature":   {"", "unsigned binary"},
		"signed by other key": {releasesig.Sign(otherPriv, version, runtime.GOOS, runtime.GOARCH, digest), "signature verification failed"},
		"signed for other os": {releasesig.Sign(priv, version, "plan9", runtime.GOARCH, digest), "signature verification failed"},
		"signed older ver":    {releasesig.Sign(priv, "98.0.0", runtime.GOOS, runtime.GOARCH, digest), "signature verification failed"},
	}
	// A valid signature would proceed to replace os.Executable(), which a test must not do.

	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "/agent/version") {
					fmt.Fprintf(w, `{"version":%q,"download_url":"/dl","sha256":%q,"signature":%q}`, version, digest, c.sig)
					return
				}
				_, _ = w.Write(binary)
			}))
			defer srv.Close()
			updated, err := CheckAndUpdate(context.Background(), srv.URL, "1.0.0", "", "", nil)
			if updated || err == nil || !strings.Contains(err.Error(), c.wantErr) {
				t.Errorf("updated=%v err=%v; want error containing %q", updated, err, c.wantErr)
			}
		})
	}
}
