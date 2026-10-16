//go:build !windows

package executor

import (
	"os/exec"
	"syscall"
	"time"
)

// confine runs cmd in its own process group and, on cancellation or timeout,
// kills the whole group. Without this only the shell is killed and any child
// it spawned keeps running and holds the output pipe open, so the timeout
// never takes effect.
func confine(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	cmd.WaitDelay = 2 * time.Second
}
