//go:build windows

package executor

import (
	"os/exec"
	"time"
)

func confine(cmd *exec.Cmd) {
	cmd.WaitDelay = 2 * time.Second
}
