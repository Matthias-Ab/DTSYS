package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/dtsys/agent/internal/collector"
	"github.com/dtsys/agent/internal/config"
	"github.com/dtsys/agent/internal/executor"
	"github.com/dtsys/agent/internal/transport"
	"github.com/dtsys/agent/internal/updater"
	"github.com/dtsys/agent/internal/version"
)

const defaultConfigPath = "/etc/dtsys/agent.toml"
const registrationFailureStatusPath = "/etc/dtsys/agent.error"

var AgentVersion = version.Version

func main() {
	configPath := flag.String("config", defaultConfigPath, "Path to agent config file")
	flag.Parse()

	var wsClient *transport.Client
	slog.SetDefault(slog.New(&forwardingHandler{
		base: slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}),
		client: func() *transport.Client {
			return wsClient
		},
	}))

	cfg, err := config.Load(*configPath)
	if err != nil {
		slog.Error("failed to load config", "error", err)
		os.Exit(1)
	}
	if err := collector.ConfigureEventFiltering(cfg.Events.DedupMaxEntries, cfg.Events.ExcludePatterns); err != nil {
		slog.Error("failed to configure event filters", "error", err)
		os.Exit(1)
	}
	collector.ConfigureEventRateLimit(cfg.Events.RateLimitMax, cfg.Events.RateLimitWindowS)
	runtimeCfg := config.NewRuntimeConfig(cfg, *configPath)
	executor.ConfigureDiagnostics(AgentVersion, func() *config.Config {
		return runtimeCfg.Config()
	})

	tlsConfig := transport.BuildTLSConfig(cfg.Server.URL, cfg.TLS.SkipTimeCheck)

	// Register if not yet registered
	if cfg.Agent.DeviceID == "" || cfg.Agent.APIKey == "" {
		slog.Info("no device ID found, registering with server")
		if err := register(cfg, *configPath, tlsConfig); err != nil {
			slog.Error("registration failed", "error", err)
			os.Exit(1)
		}
	}
	runStartupChecks(cfg, tlsConfig)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	wsClient = transport.NewClient(
		cfg.Server.URL,
		cfg.Agent.DeviceID,
		cfg.Agent.APIKey,
		tlsConfig,
		func(cmd transport.IncomingCommand) {
			if cmd.CommandType == transport.MsgTypeUpdate {
				if cfg.Update.AutoUpdate {
					if updated := runUpdateCommand(ctx, cfg, tlsConfig); updated {
						restartAgent()
						return
					}
				}
				wsClient.SendCommandResult(transport.CommandResultData{
					CommandID: cmd.CommandID,
					ExitCode:  0,
					Output:    "Update check completed",
				})
				return
			}
			result := executor.Execute(ctx, cmd, wsClient.Send)
			if cmd.CommandType != "screenshot" {
				slog.Info("sending command_result", "id", result.CommandID)
				wsClient.SendCommandResult(result)
			}
		},
		func(update transport.ConfigUpdateData) {
			if err := runtimeCfg.Apply(
				update.TelemetryIntervalSecs,
				update.SoftwareScanIntervalM,
				update.EventPollIntervalSecs,
			); err != nil {
				slog.Warn("failed to apply config update", "error", err)
				return
			}
			slog.Info(
				"config updated",
				"telemetry_interval_secs", update.TelemetryIntervalSecs,
				"software_scan_interval_m", update.SoftwareScanIntervalM,
				"event_poll_interval_secs", update.EventPollIntervalSecs,
			)
		},
	)

	// Start data collection loops
	go telemetryLoop(ctx, wsClient, runtimeCfg)
	go softwareLoop(ctx, wsClient, runtimeCfg)
	go ntpLoop(ctx, wsClient, cfg)
	go eventLoop(ctx, wsClient, runtimeCfg)
	go networkLoop(ctx, wsClient)
	go sshKeyLoop(ctx, wsClient)
	go processLoop(ctx, wsClient)
	go agentInfoLoop(ctx, wsClient)
	go updateLoop(ctx, cfg, tlsConfig)

	// Run WebSocket connection (blocks until ctx cancelled)
	wsClient.Run(ctx)
	slog.Info("agent stopped")
}

type forwardingHandler struct {
	base   slog.Handler
	client func() *transport.Client
}

func (h *forwardingHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.base.Enabled(ctx, level)
}

func (h *forwardingHandler) Handle(ctx context.Context, record slog.Record) error {
	if err := h.base.Handle(ctx, record); err != nil {
		return err
	}

	if record.Level < slog.LevelWarn || h.client == nil {
		return nil
	}

	client := h.client()
	if client == nil || !client.IsConnected() {
		return nil
	}

	client.SendEvent(transport.EventData{
		EventType: "agent_log",
		Source:    fmt.Sprintf("agent/%s", strings.ToLower(record.Level.String())),
		Message:   fmt.Sprintf("[%s] %s", strings.ToUpper(record.Level.String()), record.Message),
		RawData: map[string]interface{}{
			"level": record.Level.String(),
		},
	})
	return nil
}

func (h *forwardingHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return &forwardingHandler{
		base:   h.base.WithAttrs(attrs),
		client: h.client,
	}
}

func (h *forwardingHandler) WithGroup(name string) slog.Handler {
	return &forwardingHandler{
		base:   h.base.WithGroup(name),
		client: h.client,
	}
}

func register(cfg *config.Config, cfgPath string, tlsConfig *tls.Config) error {
	osInfo, err := collector.CollectOSInfo()
	if err != nil {
		return fmt.Errorf("collect os info: %w", err)
	}

	fingerprint := buildFingerprint(osInfo.Hostname)

	apiURL := strings.TrimRight(cfg.Server.URL, "/")
	apiURL = strings.Replace(apiURL, "wss://", "https://", 1)
	apiURL = strings.Replace(apiURL, "ws://", "http://", 1)
	client := &http.Client{
		Timeout: 30 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: tlsConfig,
		},
	}
	attempt := 0
	backoff := 5 * time.Second

	for {
		attempt++
		result, statusCode, responseBody, err := attemptRegistration(client, apiURL, map[string]interface{}{
			"hostname":         osInfo.Hostname,
			"os_type":          osInfo.OSType,
			"os_version":       osInfo.OSVersion,
			"arch":             osInfo.Arch,
			"fingerprint":      fingerprint,
			"enrollment_token": cfg.Server.EnrollmentToken,
		})
		if err == nil && statusCode == http.StatusOK {
			cfg.Agent.DeviceID = result.DeviceID
			cfg.Agent.APIKey = result.APIKey
			if saveErr := config.Save(cfgPath, cfg); saveErr != nil {
				return fmt.Errorf("save config: %w", saveErr)
			}
			slog.Info("device registered", "device_id", result.DeviceID)
			return nil
		}

		if statusCode == http.StatusBadRequest {
			return fmt.Errorf("registration failed: invalid or expired enrollment token")
		}

		if statusCode == http.StatusConflict {
			slog.Warn("device fingerprint already registered; attempting reuse flow")
			if reuseExistingCredentials(cfg, cfgPath) == nil {
				return nil
			}
			slog.Warn("existing device reuse not completed", "detail", responseBody)
		}

		wait := backoff
		if statusCode >= 400 && statusCode < 500 && statusCode != http.StatusConflict {
			wait = 5 * time.Minute
		}
		if attempt >= 10 {
			_ = writeRegistrationStatus(fmt.Sprintf("registration still failing after %d attempts: %v %s", attempt, err, responseBody))
			wait = 5 * time.Minute
		}
		slog.Warn("registration attempt failed", "attempt", attempt, "status_code", statusCode, "error", err, "retry_in", wait)
		time.Sleep(wait)
		if backoff < 5*time.Minute {
			backoff *= 2
			if backoff > 5*time.Minute {
				backoff = 5 * time.Minute
			}
		}
	}
}

func runStartupChecks(cfg *config.Config, tlsConfig *tls.Config) {
	slog.Info("running startup checks")
	if cfg.Agent.DeviceID == "" || cfg.Agent.APIKey == "" {
		slog.Error("config missing device_id or api_key — re-enroll this device")
		os.Exit(1)
	}

	apiURL := strings.TrimRight(cfg.Server.URL, "/")
	apiURL = strings.Replace(apiURL, "wss://", "https://", 1)
	apiURL = strings.Replace(apiURL, "ws://", "http://", 1)

	client := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			TLSClientConfig: tlsConfig,
		},
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL+"/health", nil)
	if err != nil {
		slog.Warn("startup check failed", "error", err)
	} else if resp, err := client.Do(req); err != nil {
		slog.Warn("server not reachable at startup — will retry in background", "error", err)
	} else {
		resp.Body.Close()
		slog.Info("server reachable", "status", resp.StatusCode)
	}
	slog.Info("system time", "time", time.Now().UTC().Format(time.RFC3339))
}

type registerResponse struct {
	DeviceID string `json:"device_id"`
	APIKey   string `json:"api_key"`
}

func attemptRegistration(client *http.Client, apiURL string, body map[string]interface{}) (registerResponse, int, string, error) {
	data, _ := json.Marshal(body)
	resp, err := client.Post(apiURL+"/api/v1/devices/register", "application/json", strings.NewReader(string(data)))
	if err != nil {
		return registerResponse{}, 0, "", fmt.Errorf("register request: %w", err)
	}
	defer resp.Body.Close()

	bodyBytes, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return registerResponse{}, resp.StatusCode, strings.TrimSpace(string(bodyBytes)), fmt.Errorf("server returned %d", resp.StatusCode)
	}

	var result registerResponse
	if err := json.Unmarshal(bodyBytes, &result); err != nil {
		return registerResponse{}, resp.StatusCode, string(bodyBytes), fmt.Errorf("parse response: %w", err)
	}
	return result, resp.StatusCode, string(bodyBytes), nil
}

func reuseExistingCredentials(cfg *config.Config, cfgPath string) error {
	fmt.Println("This device fingerprint is already registered.")
	fmt.Print("Enter existing device ID (leave blank to abort): ")
	var deviceID string
	if _, err := fmt.Scanln(&deviceID); err != nil {
		return err
	}
	if strings.TrimSpace(deviceID) == "" {
		return fmt.Errorf("no device id provided")
	}
	fmt.Print("Enter existing API key: ")
	var apiKey string
	if _, err := fmt.Scanln(&apiKey); err != nil {
		return err
	}
	if strings.TrimSpace(apiKey) == "" {
		return fmt.Errorf("no api key provided")
	}

	cfg.Agent.DeviceID = strings.TrimSpace(deviceID)
	cfg.Agent.APIKey = strings.TrimSpace(apiKey)
	if err := config.Save(cfgPath, cfg); err != nil {
		return err
	}
	slog.Info("reused existing device credentials", "device_id", cfg.Agent.DeviceID)
	return nil
}

func writeRegistrationStatus(message string) error {
	path := registrationFailureStatusPath
	if runtime.GOOS == "windows" {
		path = filepath.Join(os.TempDir(), "dtsys-agent.error")
	}
	return os.WriteFile(path, []byte(message+"\n"), 0600)
}

func buildFingerprint(hostname string) string {
	h := sha256.New()
	h.Write([]byte(hostname))
	return fmt.Sprintf("%x", h.Sum(nil))
}

func telemetryLoop(ctx context.Context, client *transport.Client, runtimeCfg *config.RuntimeConfig) {
	ticker := time.NewTicker(runtimeCfg.TelemetryInterval())
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case next := <-runtimeCfg.TelemetryUpdates():
			ticker.Reset(next)
		case <-ticker.C:
			if !client.IsConnected() {
				continue
			}
			data, err := collector.CollectTelemetry()
			if err != nil {
				slog.Warn("telemetry collection failed", "error", err)
				continue
			}
			client.SendTelemetry(data)
		}
	}
}

func softwareLoop(ctx context.Context, client *transport.Client, runtimeCfg *config.RuntimeConfig) {
	ticker := time.NewTicker(runtimeCfg.SoftwareInterval())
	defer ticker.Stop()

	// Wait for connection before initial send
	waitConnected(ctx, client)
	sendSoftware(client)

	for {
		select {
		case <-ctx.Done():
			return
		case next := <-runtimeCfg.SoftwareUpdates():
			ticker.Reset(next)
		case <-ticker.C:
			sendSoftware(client)
		}
	}
}

func waitConnected(ctx context.Context, client *transport.Client) {
	for {
		if client.IsConnected() {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(500 * time.Millisecond):
		}
	}
}

func sendSoftware(client *transport.Client) {
	if !client.IsConnected() {
		return
	}
	pkgs, err := collector.CollectSoftware()
	if err != nil {
		slog.Warn("software collection failed", "error", err)
		return
	}
	client.SendSoftwareInventory(pkgs)
	slog.Info("software inventory sent", "packages", len(pkgs))
}

func ntpLoop(ctx context.Context, client *transport.Client, cfg *config.Config) {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !client.IsConnected() {
				continue
			}
			status := collector.CollectNTPStatus()
			client.SendNTPStatus(status)
		}
	}
}

func eventLoop(ctx context.Context, client *transport.Client, runtimeCfg *config.RuntimeConfig) {
	ticker := time.NewTicker(runtimeCfg.EventInterval())
	defer ticker.Stop()

	lastChecked := time.Now().Add(-5 * time.Minute)
	for {
		select {
		case <-ctx.Done():
			return
		case next := <-runtimeCfg.EventUpdates():
			ticker.Reset(next)
		case <-ticker.C:
			events, err := collector.CollectEvents(lastChecked)
			if err != nil {
				slog.Warn("event collection failed", "error", err)
				continue
			}
			lastChecked = time.Now()
			events = collector.ApplyEventRateLimit(events)
			for _, ev := range events {
				client.SendEvent(ev)
			}
		}
	}
}

func networkLoop(ctx context.Context, client *transport.Client) {
	ticker := time.NewTicker(10 * time.Minute)
	defer ticker.Stop()

	waitConnected(ctx, client)
	sendNetwork(client)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			sendNetwork(client)
		}
	}
}

func sendNetwork(client *transport.Client) {
	if !client.IsConnected() {
		return
	}
	ifaces, err := collector.CollectNetworkInfo()
	if err != nil {
		slog.Warn("network collection failed", "error", err)
		return
	}

	client.Send(transport.Message{
		Type: transport.MsgTypeNetworkInfo,
		Data: transport.NetworkInfoData{Interfaces: ifaces},
	})
}

func sshKeyLoop(ctx context.Context, client *transport.Client) {
	ticker := time.NewTicker(4 * time.Hour)
	defer ticker.Stop()

	waitConnected(ctx, client)
	sendSSHKeys(client)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			sendSSHKeys(client)
		}
	}
}

func sendSSHKeys(client *transport.Client) {
	keys, err := collector.CollectSSHKeys()
	if err != nil {
		slog.Warn("ssh key collection failed", "error", err)
		return
	}
	client.SendSSHKeys(keys)
}

func processLoop(ctx context.Context, client *transport.Client) {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()

	waitConnected(ctx, client)
	sendProcesses(client)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			sendProcesses(client)
		}
	}
}

func agentInfoLoop(ctx context.Context, client *transport.Client) {
	ticker := time.NewTicker(1 * time.Hour)
	defer ticker.Stop()

	send := func() {
		if !client.IsConnected() {
			return
		}
		client.SendAgentInfo(transport.AgentInfoData{
			Version:   AgentVersion,
			BuildDate: version.BuildDate,
		})
	}

	send()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			send()
		}
	}
}

func sendProcesses(client *transport.Client) {
	if !client.IsConnected() {
		return
	}
	processes, err := collector.CollectTopProcesses(15)
	if err != nil {
		slog.Warn("process collection failed", "error", err)
		return
	}

	client.SendProcessList(processes)
}

func updateLoop(ctx context.Context, cfg *config.Config, tlsConfig *tls.Config) {
	if !cfg.Update.AutoUpdate {
		return
	}
	interval := time.Duration(maxInt(cfg.Update.CheckIntervalHours, 1)) * time.Hour
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	check := func() bool {
		updateCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()

		updated, err := updater.CheckAndUpdate(
			updateCtx,
			cfg.Server.URL,
			AgentVersion,
			cfg.Agent.DeviceID,
			cfg.Agent.APIKey,
			tlsConfig,
		)
		if err != nil {
			slog.Warn("agent update check failed", "error", err)
			return false
		}
		return updated
	}

	if check() {
		restartAgent()
		return
	}

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if check() {
				restartAgent()
				return
			}
		}
	}
}

func runUpdateCommand(ctx context.Context, cfg *config.Config, tlsConfig *tls.Config) bool {
	updateCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()

	updated, err := updater.CheckAndUpdate(
		updateCtx,
		cfg.Server.URL,
		AgentVersion,
		cfg.Agent.DeviceID,
		cfg.Agent.APIKey,
		tlsConfig,
	)
	if err != nil {
		slog.Warn("agent update command failed", "error", err)
		return false
	}
	return updated
}

func restartAgent() {
	slog.Info("Agent updated, restarting...")

	exePath, err := os.Executable()
	if err != nil {
		slog.Error("failed to determine executable path", "error", err)
		return
	}

	if runtime.GOOS == "windows" {
		scriptPath := filepath.Join(filepath.Dir(exePath), "dtsys-update.bat")
		if err := exec.Command(scriptPath).Start(); err != nil {
			slog.Error("failed to launch update script", "error", err)
			return
		}
		os.Exit(0)
	}

	cmd := exec.Command(exePath, os.Args[1:]...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Start(); err != nil {
		slog.Error("failed to restart agent", "error", err)
		return
	}
	os.Exit(0)
}

func maxInt(value, minimum int) int {
	if value < minimum {
		return minimum
	}
	return value
}
