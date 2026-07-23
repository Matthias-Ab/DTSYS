package updater

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

type versionResponse struct {
	Version     string `json:"version"`
	DownloadURL string `json:"download_url"`
	SHA256      string `json:"sha256"`
}

// CheckAndUpdate downloads and stages a newer agent binary when one is available.
func CheckAndUpdate(ctx context.Context, serverURL, currentVersion, deviceID, apiKey string, tlsConfig *tls.Config) (bool, error) {
	endpoint, err := buildVersionURL(serverURL)
	if err != nil {
		return false, err
	}

	client := http.DefaultClient
	if tlsConfig != nil {
		client = &http.Client{
			Transport: &http.Transport{TLSClientConfig: tlsConfig},
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return false, err
	}
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	if deviceID != "" {
		req.Header.Set("X-Device-ID", deviceID)
	}

	resp, err := client.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return false, fmt.Errorf("version check failed with status %d", resp.StatusCode)
	}

	var payload versionResponse
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return false, err
	}
	if payload.Version == "" || payload.DownloadURL == "" {
		return false, nil
	}

	newer, err := isNewerVersion(payload.Version, currentVersion)
	if err != nil {
		return false, err
	}
	if !newer {
		return false, nil
	}

	expectedSHA256 := strings.ToLower(strings.TrimSpace(payload.SHA256))
	if len(expectedSHA256) != 64 {
		return false, fmt.Errorf("server did not provide a valid sha256 checksum for the update; refusing to install an unverified binary")
	}

	downloadURL, err := resolveDownloadURL(endpoint, payload.DownloadURL)
	if err != nil {
		return false, err
	}

	exePath, err := os.Executable()
	if err != nil {
		return false, err
	}
	exePath, err = filepath.EvalSymlinks(exePath)
	if err != nil {
		exePath = filepath.Clean(exePath)
	}

	dir := filepath.Dir(exePath)
	tempFile, err := os.CreateTemp(dir, "dtsys-agent-update-*")
	if err != nil {
		return false, err
	}
	tempPath := tempFile.Name()

	actualSHA256, err := downloadBinary(ctx, downloadURL, apiKey, tempFile, client)
	if err != nil {
		tempFile.Close()
		_ = os.Remove(tempPath)
		return false, err
	}
	if err := tempFile.Close(); err != nil {
		_ = os.Remove(tempPath)
		return false, err
	}
	if subtle.ConstantTimeCompare([]byte(actualSHA256), []byte(expectedSHA256)) != 1 {
		_ = os.Remove(tempPath)
		return false, fmt.Errorf("downloaded update failed checksum verification: expected %s, got %s", expectedSHA256, actualSHA256)
	}
	if err := os.Chmod(tempPath, 0o755); err != nil && runtime.GOOS != "windows" {
		_ = os.Remove(tempPath)
		return false, err
	}

	if runtime.GOOS == "windows" {
		if err := stageWindowsUpdate(exePath, tempPath); err != nil {
			return false, err
		}
		return true, nil
	}

	if err := os.Rename(tempPath, exePath); err != nil {
		_ = os.Remove(tempPath)
		return false, err
	}

	return true, nil
}

func buildVersionURL(serverURL string) (string, error) {
	base, err := url.Parse(strings.TrimRight(serverURL, "/"))
	if err != nil {
		return "", err
	}
	switch base.Scheme {
	case "ws":
		base.Scheme = "http"
	case "wss":
		base.Scheme = "https"
	}
	base.Path = strings.TrimRight(base.Path, "/") + "/api/v1/agent/version"
	query := base.Query()
	query.Set("platform", runtime.GOOS)
	query.Set("arch", runtime.GOARCH)
	base.RawQuery = query.Encode()
	return base.String(), nil
}

func resolveDownloadURL(baseURL, raw string) (string, error) {
	parsedBase, err := url.Parse(baseURL)
	if err != nil {
		return "", err
	}
	parsedURL, err := url.Parse(raw)
	if err != nil {
		return "", err
	}
	return parsedBase.ResolveReference(parsedURL).String(), nil
}

// downloadBinary streams the response body into file while hashing it, and
// returns the hex-encoded sha256 digest of what was written so the caller can
// verify it against the server-advertised checksum before trusting the binary.
func downloadBinary(ctx context.Context, downloadURL, apiKey string, file *os.File, client *http.Client) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, downloadURL, nil)
	if err != nil {
		return "", err
	}
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}

	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("download failed with status %d", resp.StatusCode)
	}

	hasher := sha256.New()
	if _, err := io.Copy(io.MultiWriter(file, hasher), resp.Body); err != nil {
		return "", err
	}
	return hex.EncodeToString(hasher.Sum(nil)), nil
}

func isNewerVersion(remoteVersion, currentVersion string) (bool, error) {
	remote, err := parseSemver(remoteVersion)
	if err != nil {
		return false, fmt.Errorf("parse remote version: %w", err)
	}
	current, err := parseSemver(currentVersion)
	if err != nil {
		return false, fmt.Errorf("parse current version: %w", err)
	}

	for i := 0; i < 3; i++ {
		if remote[i] > current[i] {
			return true, nil
		}
		if remote[i] < current[i] {
			return false, nil
		}
	}
	return false, nil
}

func parseSemver(version string) ([3]int, error) {
	var parsed [3]int
	trimmed := strings.TrimPrefix(strings.TrimSpace(version), "v")
	parts := strings.Split(trimmed, ".")
	if len(parts) == 0 {
		return parsed, fmt.Errorf("empty version")
	}

	for i := 0; i < len(parts) && i < 3; i++ {
		number, err := strconv.Atoi(parts[i])
		if err != nil {
			return parsed, err
		}
		parsed[i] = number
	}
	return parsed, nil
}

func stageWindowsUpdate(exePath, downloadedPath string) error {
	dir := filepath.Dir(exePath)
	newPath := filepath.Join(dir, strings.TrimSuffix(filepath.Base(exePath), filepath.Ext(exePath))+".new"+filepath.Ext(exePath))
	_ = os.Remove(newPath)
	if err := os.Rename(downloadedPath, newPath); err != nil {
		return err
	}

	updateScript := filepath.Join(dir, "dtsys-update.bat")
	script := fmt.Sprintf(`@echo off
timeout /t 2 /nobreak >nul
move /y "%s" "%s" >nul
start "" "%s"%s
`, newPath, exePath, exePath, windowsArgsSuffix(os.Args[1:]))

	return os.WriteFile(updateScript, []byte(script), 0o755)
}

func windowsArgsSuffix(args []string) string {
	if len(args) == 0 {
		return ""
	}

	quoted := make([]string, 0, len(args))
	for _, arg := range args {
		quoted = append(quoted, ` "`+strings.ReplaceAll(arg, `"`, `""`)+`"`)
	}
	return strings.Join(quoted, "")
}
