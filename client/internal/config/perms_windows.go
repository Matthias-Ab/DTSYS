//go:build windows

package config

import (
	"fmt"
	"os/exec"
)

// restrictToOwner removes inherited access (the install directory under Program Files
// grants Users read access, which would expose the device API key to every local
// user) and grants full control to SYSTEM and Administrators only. SIDs are used so
// this works on non-English Windows.
func restrictToOwner(path string) error {
	out, err := exec.Command("icacls", path, "/inheritance:r", "/grant:r", "*S-1-5-18:F", "*S-1-5-32-544:F").CombinedOutput()
	if err != nil {
		return fmt.Errorf("icacls failed: %v: %s", err, out)
	}
	return nil
}
