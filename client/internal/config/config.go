package config

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

type Config struct {
	Server   ServerConfig   `toml:"server"`
	Agent    AgentConfig    `toml:"agent"`
	Collect  CollectConfig  `toml:"collect"`
	Events   EventsConfig   `toml:"events"`
	TLS      TLSConfig      `toml:"tls"`
	Update   UpdateConfig   `toml:"update"`
	Executor ExecutorConfig `toml:"executor"`
}

type ServerConfig struct {
	URL             string `toml:"url"`              // e.g. "wss://dtsys.example.com"
	EnrollmentToken string `toml:"enrollment_token"` // used only on first registration
}

type AgentConfig struct {
	DeviceID string `toml:"device_id"` // set after registration
	APIKey   string `toml:"api_key"`   // set after registration
}

type CollectConfig struct {
	TelemetryIntervalSecs int `toml:"telemetry_interval_secs"`  // default 60
	SoftwareScanIntervalM int `toml:"software_scan_interval_m"` // default 60 (minutes)
	EventPollIntervalSecs int `toml:"event_poll_interval_secs"` // default 120
}

type EventsConfig struct {
	DedupMaxEntries  int      `toml:"dedup_max_entries"`   // default 50
	ExcludePatterns  []string `toml:"exclude_patterns"`    // default includes docker EOF noise
	RateLimitMax     int      `toml:"rate_limit_max"`      // default 20
	RateLimitWindowS int      `toml:"rate_limit_window_s"` // default 30
}

type TLSConfig struct {
	SkipTimeCheck bool `toml:"skip_time_check"` // default false; enable only for devices with unreliable clocks
}

type UpdateConfig struct {
	AutoUpdate         bool `toml:"auto_update"`          // default true
	CheckIntervalHours int  `toml:"check_interval_hours"` // default 6
}

type ExecutorConfig struct {
	DisableShell bool `toml:"disable_shell"` // default false; set true to refuse "shell"/"script" commands locally
}

func defaults() *Config {
	return &Config{
		Collect: CollectConfig{
			TelemetryIntervalSecs: 60,
			SoftwareScanIntervalM: 60,
			EventPollIntervalSecs: 120,
		},
		Events: EventsConfig{
			DedupMaxEntries:  50,
			ExcludePatterns:  []string{"event handler.*EOF", "event streamer.*EOF"},
			RateLimitMax:     20,
			RateLimitWindowS: 30,
		},
		TLS: TLSConfig{
			SkipTimeCheck: false,
		},
		Update: UpdateConfig{
			AutoUpdate:         true,
			CheckIntervalHours: 6,
		},
	}
}

func Load(path string) (*Config, error) {
	cfg := defaults()

	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil, fmt.Errorf("config file not found: %s", path)
	}

	if _, err := toml.DecodeFile(path, cfg); err != nil {
		return nil, fmt.Errorf("failed to parse config: %w", err)
	}

	if cfg.Server.URL == "" {
		return nil, fmt.Errorf("server.url is required in config")
	}

	return cfg, nil
}

// Save writes the config atomically (temp file + rename) with owner-only access, so a
// crash or power loss mid-write can no longer leave an empty config that stops the
// agent from starting, and the API key is never readable by other local users.
func Save(path string, cfg *Config) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".agent-config-*")
	if err != nil {
		return fmt.Errorf("cannot write config: %w", err)
	}
	tmpPath := tmp.Name()
	cleanup := func() { _ = os.Remove(tmpPath) }

	if err := toml.NewEncoder(tmp).Encode(cfg); err != nil {
		tmp.Close()
		cleanup()
		return fmt.Errorf("cannot encode config: %w", err)
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return fmt.Errorf("cannot write config: %w", err)
	}
	if err := restrictToOwner(tmpPath); err != nil {
		cleanup()
		return fmt.Errorf("cannot restrict config permissions: %w", err)
	}
	if err := os.Rename(tmpPath, path); err != nil {
		cleanup()
		return fmt.Errorf("cannot replace config: %w", err)
	}
	return nil
}

// WriteInitial creates the first config file for an installer (MSI/scripts). The
// TOML is produced by the encoder, so server URLs and tokens containing quotes,
// newlines or other special characters cannot inject extra keys the way string
// concatenation in a shell/batch command could. An existing config that already
// holds device credentials is left untouched, so re-running the installer (MSI
// repair/upgrade) does not wipe enrollment.
func WriteInitial(path, serverURL, enrollmentToken string) error {
	u, err := url.Parse(strings.TrimSpace(serverURL))
	if err != nil || u.Host == "" {
		return fmt.Errorf("invalid server URL %q", serverURL)
	}
	switch u.Scheme {
	case "http", "https", "ws", "wss":
	default:
		return fmt.Errorf("server URL must use http, https, ws or wss (got %q)", u.Scheme)
	}
	if strings.TrimSpace(enrollmentToken) == "" {
		return fmt.Errorf("an enrollment token is required")
	}

	if existing, err := Load(path); err == nil && existing.Agent.DeviceID != "" && existing.Agent.APIKey != "" {
		return nil
	}

	cfg := defaults()
	cfg.Server.URL = strings.TrimSpace(serverURL)
	cfg.Server.EnrollmentToken = strings.TrimSpace(enrollmentToken)
	return Save(path, cfg)
}
