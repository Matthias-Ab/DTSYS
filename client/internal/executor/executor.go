package executor

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	_ "image/jpeg"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/dtsys/agent/internal/collector"
	agentconfig "github.com/dtsys/agent/internal/config"
	"github.com/dtsys/agent/internal/transport"
	"github.com/dtsys/agent/internal/version"
	"github.com/shirou/gopsutil/v3/disk"
)

const maxOutputBytes = 1 * 1024 * 1024 // 1MB output cap

type DiagnosticsConfigProvider func() *agentconfig.Config

var diagnosticsConfigFn DiagnosticsConfigProvider

func ConfigureDiagnostics(version string, configFn DiagnosticsConfigProvider) {
	_ = version
	diagnosticsConfigFn = configFn
}

// Execute runs a command and returns the result.
func Execute(ctx context.Context, cmd transport.IncomingCommand, send func(transport.Message)) transport.CommandResultData {
	slog.Info("executing command", "type", cmd.CommandType, "id", cmd.CommandID)
	timeout := 60 * time.Second
	if t, ok := cmd.Payload["timeout_secs"].(float64); ok && t > 0 && t <= 300 {
		timeout = time.Duration(t) * time.Second
	}

	execCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var out []byte
	var exitCode int
	var err error

	switch cmd.CommandType {
	case "shell", "script":
		if diagnosticsConfigFn != nil {
			if cfg := diagnosticsConfigFn(); cfg != nil && cfg.Executor.DisableShell {
				return transport.CommandResultData{
					CommandID: cmd.CommandID,
					ExitCode:  1,
					Output:    "shell/script command execution is disabled on this device (executor.disable_shell = true)",
				}
			}
		}
		out, exitCode, err = runShell(execCtx, cmd.Payload)
	case "reboot":
		out, exitCode, err = runReboot(execCtx)
	case "update_check":
		out, exitCode, err = runUpdateCheck(execCtx)
	case "sync_time":
		out, exitCode, err = runSyncTime(execCtx, cmd.Payload)
	case "screenshot":
		out, exitCode, err = runScreenshot(execCtx, cmd, send)
	case "request_process_list":
		out, exitCode, err = runProcessList(execCtx, cmd, send)
	case "diagnostics":
		out, exitCode, err = runDiagnostics(execCtx)
	default:
		return transport.CommandResultData{
			CommandID: cmd.CommandID,
			ExitCode:  1,
			Output:    fmt.Sprintf("unknown command type: %s", cmd.CommandType),
		}
	}

	if err != nil && exitCode == 0 {
		exitCode = 1
	}

	_ = appendCommandAudit(cmd.CommandID, cmd.CommandType, exitCode)

	output := string(out)
	if len(output) > maxOutputBytes {
		output = output[:maxOutputBytes] + "\n[output truncated]"
	}

	result := transport.CommandResultData{
		CommandID: cmd.CommandID,
		ExitCode:  exitCode,
		Output:    output,
	}
	slog.Info("command_result", "id", result.CommandID, "exit_code", result.ExitCode)
	return result
}

func runShell(ctx context.Context, payload map[string]interface{}) ([]byte, int, error) {
	command, _ := payload["command"].(string)
	if command == "" {
		return nil, 1, fmt.Errorf("command payload missing 'command' field")
	}
	if len(command) > 10000 {
		return nil, 1, fmt.Errorf("command exceeds 10000 character limit")
	}

	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.CommandContext(ctx, "cmd", "/C", command)
	default:
		cmd = exec.CommandContext(ctx, "/bin/sh", "-c", command)
	}

	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf

	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}

	return buf.Bytes(), exitCode, err
}

func runReboot(ctx context.Context) ([]byte, int, error) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.CommandContext(ctx, "shutdown", "/r", "/t", "30", "/c", "DTSYS scheduled reboot")
	case "darwin":
		cmd = exec.CommandContext(ctx, "shutdown", "-r", "+1")
	default:
		cmd = exec.CommandContext(ctx, "shutdown", "-r", "+1", "DTSYS scheduled reboot")
	}

	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}
	return buf.Bytes(), exitCode, err
}

func runUpdateCheck(ctx context.Context) ([]byte, int, error) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.CommandContext(ctx, "powershell", "-Command",
			`(New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search("IsInstalled=0").Updates | Select Title | Format-List`)
	case "darwin":
		cmd = exec.CommandContext(ctx, "softwareupdate", "-l")
	default:
		// Try apt or dnf
		if _, err := exec.LookPath("apt-get"); err == nil {
			cmd = exec.CommandContext(ctx, "apt-get", "-s", "upgrade")
		} else {
			cmd = exec.CommandContext(ctx, "dnf", "check-update")
		}
	}

	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			// dnf check-update exits 100 when updates are available
			if exitErr.ExitCode() == 100 {
				exitCode = 0
			} else {
				exitCode = exitErr.ExitCode()
			}
		} else {
			exitCode = 1
		}
	}

	output := strings.TrimSpace(buf.String())
	if output == "" {
		output = "No updates available"
	}
	return []byte(output), exitCode, nil
}

func runSyncTime(ctx context.Context, payload map[string]interface{}) ([]byte, int, error) {
	targetTime, _ := payload["target_time"].(string)
	switch runtime.GOOS {
	case "windows":
		return runSingleCommand(ctx, exec.CommandContext(ctx, "w32tm", "/resync", "/force"))
	case "darwin":
		return runSingleCommand(ctx, exec.CommandContext(ctx, "sntp", "-sS", "time.apple.com"))
	default:
		commands := []*exec.Cmd{
			exec.CommandContext(ctx, "chronyc", "makestep"),
			exec.CommandContext(ctx, "timedatectl", "set-ntp", "true"),
			exec.CommandContext(ctx, "ntpdate", "-u", "pool.ntp.org"),
		}
		var outputs []string
		for _, cmd := range commands {
			if _, err := exec.LookPath(cmd.Path); err != nil {
				outputs = append(outputs, fmt.Sprintf("%s: not available", cmd.Path))
				continue
			}
			out, exitCode, err := runSingleCommand(ctx, cmd)
			outputs = append(outputs, strings.TrimSpace(string(out)))
			if err == nil && exitCode == 0 {
				return []byte(strings.Join(compactStrings(outputs), "\n")), 0, nil
			}
		}
		if targetTime != "" {
			out, exitCode, err := runSingleCommand(ctx, exec.CommandContext(ctx, "date", "-s", targetTime))
			outputs = append(outputs, strings.TrimSpace(string(out)))
			if err == nil && exitCode == 0 {
				return []byte(strings.Join(compactStrings(outputs), "\n")), 0, nil
			}
		}
		output := strings.Join(compactStrings(outputs), "\n")
		if output == "" {
			output = "no supported time sync command available"
		}
		return []byte(output), 1, fmt.Errorf("time sync failed")
	}
}

func runSingleCommand(ctx context.Context, cmd *exec.Cmd) ([]byte, int, error) {
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf

	err := cmd.Run()
	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = 1
		}
	}

	if ctx.Err() != nil && exitCode == 0 {
		exitCode = 1
	}
	return buf.Bytes(), exitCode, err
}

func compactStrings(values []string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			result = append(result, value)
		}
	}
	return result
}

func runScreenshot(ctx context.Context, cmd transport.IncomingCommand, send func(transport.Message)) ([]byte, int, error) {
	imageBytes, err := collector.CaptureScreenshot(ctx)
	if err != nil {
		if send != nil {
			send(transport.Message{
				Type: transport.MsgTypeScreenshotResult,
				Data: transport.ScreenshotResultData{
					CommandID: cmd.CommandID,
					Error:     err.Error(),
				},
			})
		}
		return []byte(err.Error()), 1, err
	}

	width, height := 0, 0
	if cfg, _, cfgErr := image.DecodeConfig(bytes.NewReader(imageBytes)); cfgErr == nil {
		width = cfg.Width
		height = cfg.Height
	}

	if send != nil {
		send(transport.Message{
			Type: transport.MsgTypeScreenshotResult,
			Data: transport.ScreenshotResultData{
				CommandID: cmd.CommandID,
				ImageB64:  base64.StdEncoding.EncodeToString(imageBytes),
				Width:     width,
				Height:    height,
			},
		})
	}

	return []byte("screenshot captured"), 0, nil
}

func runProcessList(ctx context.Context, cmd transport.IncomingCommand, send func(transport.Message)) ([]byte, int, error) {
	processes, err := collector.CollectTopProcesses(15)
	if err != nil {
		return []byte(err.Error()), 1, err
	}

	if send != nil {
		send(transport.Message{
			Type: transport.MsgTypeProcessList,
			Data: transport.ProcessListData{Processes: processes},
		})
	}

	return []byte(fmt.Sprintf("reported %d processes", len(processes))), 0, nil
}

func runDiagnostics(ctx context.Context) ([]byte, int, error) {
	_ = ctx
	report := map[string]interface{}{
		"agent_version": version.Version,
		"build_date":    version.BuildDate,
	}

	if osInfo, err := collector.CollectOSInfo(); err == nil {
		report["os_info"] = map[string]interface{}{
			"hostname": osInfo.Hostname,
			"os":       osInfo.OSType,
			"version":  osInfo.OSVersion,
			"arch":     osInfo.Arch,
		}
	}

	if telemetry, err := collector.CollectTelemetry(); err == nil {
		report["telemetry"] = telemetry
	}

	if ifaces, err := collector.CollectNetworkInfo(); err == nil {
		report["network_interfaces"] = ifaces
	}

	report["ntp_status"] = collector.CollectNTPStatus()

	if diagnosticsConfigFn != nil {
		if cfg := diagnosticsConfigFn(); cfg != nil {
			report["agent_config"] = map[string]interface{}{
				"telemetry_interval_secs":  cfg.Collect.TelemetryIntervalSecs,
				"software_scan_interval_m": cfg.Collect.SoftwareScanIntervalM,
				"event_poll_interval_secs": cfg.Collect.EventPollIntervalSecs,
				"server_url":               cfg.Server.URL,
			}
		}
	}

	if processes, err := collector.CollectTopProcesses(10); err == nil {
		report["top_processes"] = processes
	}

	if events, err := collector.CollectEvents(time.Now().Add(-6 * time.Hour)); err == nil {
		if len(events) > 5 {
			events = events[:5]
		}
		report["recent_events"] = events
	}

	if partitions, err := collectPartitions(); err == nil {
		report["disk_partitions"] = partitions
	}

	output, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return []byte(err.Error()), 1, err
	}
	return output, 0, nil
}

func appendCommandAudit(commandID, commandType string, exitCode int) error {
	path := "/var/log/dtsys-agent-commands.log"
	if runtime.GOOS == "windows" {
		path = filepath.Join(os.TempDir(), "dtsys-agent-commands.log")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer f.Close()

	_, err = fmt.Fprintf(
		f,
		"%s | %s | %s | %d\n",
		time.Now().Format(time.RFC3339),
		commandID,
		commandType,
		exitCode,
	)
	return err
}

func collectPartitions() ([]map[string]interface{}, error) {
	partitions, err := disk.Partitions(false)
	if err != nil {
		return nil, err
	}

	results := make([]map[string]interface{}, 0, len(partitions))
	for _, partition := range partitions {
		entry := map[string]interface{}{
			"device":     partition.Device,
			"mountpoint": partition.Mountpoint,
			"fstype":     partition.Fstype,
		}
		if usage, err := disk.Usage(partition.Mountpoint); err == nil {
			entry["total_gb"] = float64(usage.Total) / 1024 / 1024 / 1024
			entry["used_gb"] = float64(usage.Used) / 1024 / 1024 / 1024
			entry["used_percent"] = usage.UsedPercent
		}
		results = append(results, entry)
	}
	return results, nil
}
