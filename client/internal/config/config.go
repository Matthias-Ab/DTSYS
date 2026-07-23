package config

import (
	"fmt"
	"os"

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

func Load(path string) (*Config, error) {
	cfg := &Config{
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

func Save(path string, cfg *Config) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0600)
	if err != nil {
		return fmt.Errorf("cannot write config: %w", err)
	}
	defer f.Close()

	enc := toml.NewEncoder(f)
	return enc.Encode(cfg)
}
