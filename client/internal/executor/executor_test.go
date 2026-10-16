package executor

import (
	"context"
	"runtime"
	"strings"
	"testing"
	"time"

	agentconfig "github.com/dtsys/agent/internal/config"
	"github.com/dtsys/agent/internal/transport"
)

func skipOnWindows(t *testing.T) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("uses /bin/sh")
	}
}

func shellCmd(command string, extra map[string]interface{}) transport.IncomingCommand {
	payload := map[string]interface{}{"command": command}
	for k, v := range extra {
		payload[k] = v
	}
	return transport.IncomingCommand{CommandID: "c1", CommandType: "shell", Payload: payload}
}

func TestExecuteShellOutputAndExitCode(t *testing.T) {
	skipOnWindows(t)
	res := Execute(context.Background(), shellCmd("echo hello", nil), nil)
	if res.CommandID != "c1" || res.ExitCode != 0 || strings.TrimSpace(res.Output) != "hello" {
		t.Errorf("unexpected result %+v", res)
	}

	res = Execute(context.Background(), shellCmd("echo oops >&2; exit 7", nil), nil)
	if res.ExitCode != 7 || !strings.Contains(res.Output, "oops") {
		t.Errorf("expected exit 7 with stderr captured, got %+v", res)
	}
}

func TestExecuteScriptTypeUsesShell(t *testing.T) {
	skipOnWindows(t)
	cmd := shellCmd("echo from-script", nil)
	cmd.CommandType = "script"
	if res := Execute(context.Background(), cmd, nil); strings.TrimSpace(res.Output) != "from-script" {
		t.Errorf("got %+v", res)
	}
}

func TestExecuteValidatesCommandPayload(t *testing.T) {
	skipOnWindows(t)
	res := Execute(context.Background(), transport.IncomingCommand{CommandID: "c", CommandType: "shell", Payload: map[string]interface{}{}}, nil)
	if res.ExitCode == 0 {
		t.Errorf("missing command should fail, got %+v", res)
	}

	res = Execute(context.Background(), shellCmd(strings.Repeat("a", 10001), nil), nil)
	if res.ExitCode == 0 {
		t.Errorf("oversized command should fail, got %+v", res)
	}
}

func TestExecuteUnknownCommandType(t *testing.T) {
	res := Execute(context.Background(), transport.IncomingCommand{CommandID: "x", CommandType: "format_disk"}, nil)
	if res.ExitCode != 1 || !strings.Contains(res.Output, "unknown command type: format_disk") {
		t.Errorf("got %+v", res)
	}
}

func TestExecuteHonoursDisableShell(t *testing.T) {
	skipOnWindows(t)
	cfg := &agentconfig.Config{}
	cfg.Executor.DisableShell = true
	ConfigureDiagnostics("test", func() *agentconfig.Config { return cfg })
	t.Cleanup(func() { ConfigureDiagnostics("test", nil) })

	for _, typ := range []string{"shell", "script"} {
		cmd := shellCmd("touch /tmp/dtsys-should-not-exist-"+typ, nil)
		cmd.CommandType = typ
		res := Execute(context.Background(), cmd, nil)
		if res.ExitCode != 1 || !strings.Contains(res.Output, "disabled") {
			t.Errorf("%s: expected disabled refusal, got %+v", typ, res)
		}
	}
}

func TestExecuteTimeout(t *testing.T) {
	skipOnWindows(t)
	start := time.Now()
	res := Execute(context.Background(), shellCmd("sleep 5", map[string]interface{}{"timeout_secs": float64(1)}), nil)
	if res.ExitCode == 0 {
		t.Errorf("timed-out command must not report success: %+v", res)
	}
	if elapsed := time.Since(start); elapsed > 4*time.Second {
		t.Errorf("timeout not enforced, took %v", elapsed)
	}
}

func TestExecuteTimeoutIsClamped(t *testing.T) {
	skipOnWindows(t)
	// Out-of-range values fall back to the default instead of being honoured.
	res := Execute(context.Background(), shellCmd("echo ok", map[string]interface{}{"timeout_secs": float64(100000)}), nil)
	if res.ExitCode != 0 {
		t.Errorf("got %+v", res)
	}
}

func TestExecuteTruncatesLargeOutput(t *testing.T) {
	skipOnWindows(t)
	res := Execute(context.Background(), shellCmd("head -c 2000000 /dev/zero | tr '\\0' a", nil), nil)
	if !strings.HasSuffix(res.Output, "[output truncated]") {
		t.Errorf("expected truncation marker, len=%d", len(res.Output))
	}
	if len(res.Output) > maxOutputBytes+64 {
		t.Errorf("output not capped: %d", len(res.Output))
	}
}

func TestExecuteCancelledContext(t *testing.T) {
	skipOnWindows(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if res := Execute(ctx, shellCmd("echo hi", nil), nil); res.ExitCode == 0 {
		t.Errorf("cancelled context should fail, got %+v", res)
	}
}

func TestCompactStrings(t *testing.T) {
	got := compactStrings([]string{" a ", "", "  ", "b"})
	if len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Errorf("got %q", got)
	}
}

func TestDiagnosticsProducesJSON(t *testing.T) {
	out, code, err := runDiagnostics(context.Background())
	if err != nil || code != 0 || !strings.Contains(string(out), "agent_version") {
		t.Errorf("code=%d err=%v out=%.200s", code, err, out)
	}
}
