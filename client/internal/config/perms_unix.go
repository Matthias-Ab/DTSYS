//go:build !windows

package config

import "os"

func restrictToOwner(path string) error { return os.Chmod(path, 0o600) }
