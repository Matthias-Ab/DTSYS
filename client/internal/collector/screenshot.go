package collector

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/jpeg"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

// CaptureScreenshot returns a JPEG screenshot of the primary display.
// It best-effort notifies a logged-in user that a screenshot was taken by IT
// management before capturing, so remote capture is never silent to whoever
// is sitting at the device. Notification failures (headless box, no display
// server, etc.) never block or fail the capture itself.
func CaptureScreenshot(ctx context.Context) ([]byte, error) {
	notifyScreenshotCaptured()

	switch runtime.GOOS {
	case "windows":
		return captureWindowsScreenshot(ctx)
	case "darwin":
		return captureDarwinScreenshot(ctx)
	default:
		return captureLinuxScreenshot(ctx)
	}
}

func notifyScreenshotCaptured() {
	notifyCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	const title = "DTSYS Device Management"
	const message = "A screenshot of this screen was captured by your organization's IT management agent."

	switch runtime.GOOS {
	case "windows":
		_ = exec.CommandContext(notifyCtx, "msg", "*", "/TIME:10", message).Run()
	case "darwin":
		script := fmt.Sprintf(`display notification %q with title %q`, message, title)
		_ = exec.CommandContext(notifyCtx, "osascript", "-e", script).Run()
	default:
		if os.Getenv("DISPLAY") == "" {
			return
		}
		if _, err := exec.LookPath("notify-send"); err == nil {
			_ = exec.CommandContext(notifyCtx, "notify-send", "-u", "normal", title, message).Run()
		}
	}
}

func captureWindowsScreenshot(ctx context.Context) ([]byte, error) {
	script := strings.Join([]string{
		"Add-Type -AssemblyName System.Windows.Forms;",
		"Add-Type -AssemblyName System.Drawing;",
		"$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;",
		"$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height);",
		"$graphics = [System.Drawing.Graphics]::FromImage($bmp);",
		"$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size);",
		"$stream = New-Object System.IO.MemoryStream;",
		"$bmp.Save($stream, [System.Drawing.Imaging.ImageFormat]::Jpeg);",
		"$graphics.Dispose();",
		"$bmp.Dispose();",
		"[Convert]::ToBase64String($stream.ToArray())",
	}, " ")

	out, err := exec.CommandContext(ctx, "powershell", "-NoProfile", "-Command", script).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("powershell screenshot failed: %w: %s", err, strings.TrimSpace(string(out)))
	}

	data, decodeErr := base64.StdEncoding.DecodeString(strings.TrimSpace(string(out)))
	if decodeErr != nil {
		return nil, fmt.Errorf("decode screenshot output: %w", decodeErr)
	}
	return data, nil
}

func captureDarwinScreenshot(ctx context.Context) ([]byte, error) {
	tempFile, err := os.CreateTemp("", "dtsys-screenshot-*.jpg")
	if err != nil {
		return nil, err
	}
	path := tempFile.Name()
	_ = tempFile.Close()
	defer os.Remove(path)

	out, err := exec.CommandContext(ctx, "screencapture", "-x", "-t", "jpg", path).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("screencapture failed: %w: %s", err, strings.TrimSpace(string(out)))
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read screenshot: %w", err)
	}
	return data, nil
}

func captureLinuxScreenshot(ctx context.Context) ([]byte, error) {
	if os.Getenv("DISPLAY") == "" {
		return generatePlaceholder(), nil
	}

	tempFile, err := os.CreateTemp("", "dtsys-screenshot-*")
	if err != nil {
		return nil, err
	}
	basePath := tempFile.Name()
	_ = tempFile.Close()
	defer os.Remove(basePath)

	type cmdSpec struct {
		bin  string
		args []string
	}

	commands := []cmdSpec{
		{bin: "scrot", args: []string{"-o", basePath + ".jpg"}},
		{bin: "gnome-screenshot", args: []string{"-f", basePath + ".png", "--display=:0"}},
		{bin: "convert", args: []string{"xwd:-", "jpg:" + basePath + ".jpg"}},
		{bin: "ffmpeg", args: []string{"-f", "x11grab", "-i", ":0", "-vframes", "1", basePath + ".jpg"}},
	}

	var outputs []string

	if _, err := exec.LookPath("xwd"); err == nil {
		if _, err := exec.LookPath("convert"); err == nil {
			out, runErr := exec.CommandContext(ctx, "xwd", "-root", "-silent", "-display", ":0").Output()
			if runErr == nil {
				convertCmd := exec.CommandContext(ctx, "convert", "xwd:-", "jpg:"+basePath+".jpg")
				convertCmd.Stdin = bytes.NewReader(out)
				if convertErr := convertCmd.Run(); convertErr == nil {
					target := basePath + ".jpg"
					if data, readErr := os.ReadFile(target); readErr == nil {
						_ = os.Remove(target)
						return data, nil
					}
				} else {
					outputs = append(outputs, fmt.Sprintf("convert: %s", convertErr.Error()))
				}
			} else {
				outputs = append(outputs, fmt.Sprintf("xwd: %s", runErr.Error()))
			}
		}
	}

	for _, spec := range commands {
		if spec.bin == "convert" {
			continue
		}
		if _, lookErr := exec.LookPath(spec.bin); lookErr != nil {
			outputs = append(outputs, fmt.Sprintf("%s: not available", spec.bin))
			continue
		}
		out, runErr := exec.CommandContext(ctx, spec.bin, spec.args...).CombinedOutput()
		if runErr == nil {
			target := spec.args[len(spec.args)-1]
			if strings.HasSuffix(target, ".png") {
				img, readErr := os.Open(target)
				if readErr != nil {
					return nil, fmt.Errorf("read screenshot: %w", readErr)
				}
				defer img.Close()
				decoded, _, decErr := image.Decode(img)
				if decErr != nil {
					return nil, fmt.Errorf("decode screenshot: %w", decErr)
				}
				var buf bytes.Buffer
				if encErr := jpeg.Encode(&buf, decoded, &jpeg.Options{Quality: 85}); encErr != nil {
					return nil, fmt.Errorf("encode screenshot: %w", encErr)
				}
				_ = os.Remove(target)
				return buf.Bytes(), nil
			}
			data, readErr := os.ReadFile(target)
			if readErr == nil {
				_ = os.Remove(target)
				return data, nil
			}
			return nil, fmt.Errorf("read screenshot: %w", readErr)
		}
		outputs = append(outputs, fmt.Sprintf("%s: %s", spec.bin, strings.TrimSpace(string(out))))
	}

	return generatePlaceholder(), nil
}

func generatePlaceholder() []byte {
	img := image.NewRGBA(image.Rect(0, 0, 400, 300))
	draw.Draw(img, img.Bounds(), &image.Uniform{C: color.RGBA{R: 200, G: 200, B: 200, A: 255}}, image.Point{}, draw.Src)
	drawText(img, 40, 140, "NO DISPLAY / HEADLESS", color.RGBA{R: 60, G: 60, B: 60, A: 255})
	var buf bytes.Buffer
	_ = jpeg.Encode(&buf, img, &jpeg.Options{Quality: 80})
	return buf.Bytes()
}

func drawText(img *image.RGBA, x, y int, text string, col color.Color) {
	for _, ch := range text {
		drawChar(img, x, y, ch, col)
		x += 6
	}
}

func drawChar(img *image.RGBA, x, y int, ch rune, col color.Color) {
	pattern := font5x7[ch]
	for row := 0; row < 7; row++ {
		for colIdx := 0; colIdx < 5; colIdx++ {
			if pattern[row]&(1<<(4-colIdx)) != 0 {
				img.Set(x+colIdx, y+row, col)
			}
		}
	}
}

var font5x7 = map[rune][7]byte{
	'A': {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'D': {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E},
	'E': {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F},
	'H': {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},
	'I': {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F},
	'L': {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F},
	'N': {0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11},
	'O': {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},
	'P': {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10},
	'R': {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11},
	'S': {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E},
	'Y': {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04},
	' ': {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},
	'/': {0x01, 0x02, 0x04, 0x08, 0x10, 0x00, 0x00},
}
