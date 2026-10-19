package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestWriteInitialProducesLoadableConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.toml")
	if err := WriteInitial(path, "https://dtsys.example.com", "tok-123"); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("written config does not load (this is the bug the MSI had): %v", err)
	}
	if cfg.Server.URL != "https://dtsys.example.com" || cfg.Server.EnrollmentToken != "tok-123" {
		t.Errorf("unexpected server section: %+v", cfg.Server)
	}
	if cfg.Collect.TelemetryIntervalSecs != 60 || !cfg.Update.AutoUpdate || cfg.TLS.SkipTimeCheck {
		t.Errorf("defaults not applied (skip_time_check must default to false): %+v", cfg)
	}
}

func TestWriteInitialIsOwnerOnly(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permissions")
	}
	path := filepath.Join(t.TempDir(), "agent.toml")
	if err := WriteInitial(path, "https://dtsys.example.com", "tok"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("config mode = %v, want 0600", info.Mode().Perm())
	}
}

func TestWriteInitialCannotBeInjectedThroughValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.toml")
	evil := "tok\"\nskip_time_check = true\n[update]\nauto_update = false\n# \\"
	if err := WriteInitial(path, "https://dtsys.example.com", evil); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.TLS.SkipTimeCheck || !cfg.Update.AutoUpdate {
		t.Errorf("token injected extra config: %+v", cfg)
	}
	if cfg.Server.EnrollmentToken != strings.TrimSpace(evil) {
		t.Errorf("token did not round-trip: %q", cfg.Server.EnrollmentToken)
	}
}

func TestWriteInitialValidatesInput(t *testing.T) {
	dir := t.TempDir()
	cases := []struct{ name, url, token string }{
		{"no scheme", "dtsys.example.com", "tok"},
		{"bad scheme", "ftp://dtsys.example.com", "tok"},
		{"file scheme", "file:///etc/passwd", "tok"},
		{"empty url", "", "tok"},
		{"no host", "https://", "tok"},
		{"empty token", "https://dtsys.example.com", ""},
		{"blank token", "https://dtsys.example.com", "   "},
	}
	for _, c := range cases {
		path := filepath.Join(dir, strings.ReplaceAll(c.name, " ", "_")+".toml")
		if err := WriteInitial(path, c.url, c.token); err == nil {
			t.Errorf("%s: expected an error", c.name)
		}
		if _, err := os.Stat(path); err == nil {
			t.Errorf("%s: a config file was written despite the error", c.name)
		}
	}
}

func TestWriteInitialKeepsExistingCredentials(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.toml")
	cfg := defaults()
	cfg.Server.URL = "https://old.example.com"
	cfg.Agent.DeviceID = "dev-1"
	cfg.Agent.APIKey = "key-1"
	if err := Save(path, cfg); err != nil {
		t.Fatal(err)
	}
	// MSI repair/upgrade runs the installer again: it must not wipe enrollment.
	if err := WriteInitial(path, "https://new.example.com", "another-token"); err != nil {
		t.Fatal(err)
	}
	got, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Agent.DeviceID != "dev-1" || got.Agent.APIKey != "key-1" || got.Server.URL != "https://old.example.com" {
		t.Errorf("existing enrolled config was overwritten: %+v", got)
	}
}

func TestSaveIsAtomicAndLeavesNoTempFiles(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.toml")
	cfg := defaults()
	cfg.Server.URL = "https://dtsys.example.com"
	for i := 0; i < 5; i++ {
		cfg.Agent.DeviceID = strings.Repeat("a", i+1)
		if err := Save(path, cfg); err != nil {
			t.Fatal(err)
		}
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 || entries[0].Name() != "agent.toml" {
		t.Errorf("temp files left behind: %v", entries)
	}
	got, err := Load(path)
	if err != nil || got.Agent.DeviceID != "aaaaa" {
		t.Errorf("last save not visible: %v %+v", err, got)
	}
}
