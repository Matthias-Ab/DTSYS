package transport

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/url"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// CommandHandler is called when the server sends a command.
type CommandHandler func(cmd IncomingCommand)
type ConfigUpdateHandler func(update ConfigUpdateData)
type ScreenshotRequestData struct {
	CommandID string `json:"command_id"`
}

// Client manages the persistent WebSocket connection to the DTSYS server.
type Client struct {
	serverURL string
	deviceID  string
	apiKey    string
	onCommand CommandHandler
	onConfig  ConfigUpdateHandler
	tlsConfig *tls.Config

	conn    *websocket.Conn
	mu      sync.Mutex
	writeMu sync.Mutex
	closed  bool

	sendCh    chan Message
	writeStop chan struct{}
	writeDone chan struct{}
}

func NewClient(serverURL, deviceID, apiKey string, tlsConfig *tls.Config, onCommand CommandHandler, onConfig ConfigUpdateHandler) *Client {
	return &Client{
		serverURL: serverURL,
		deviceID:  deviceID,
		apiKey:    apiKey,
		onCommand: onCommand,
		onConfig:  onConfig,
		tlsConfig: tlsConfig,
		sendCh:    make(chan Message, 256),
	}
}

// Run connects and maintains the connection until ctx is cancelled.
func (c *Client) Run(ctx context.Context) {
	backoff := newBackoff(5*time.Second, 5*time.Minute)

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		if err := c.connect(ctx); err != nil {
			slog.Error("websocket connect failed", "error", err)
			wait := backoff.next()
			slog.Info("reconnecting", "in", wait)
			select {
			case <-ctx.Done():
				return
			case <-time.After(wait):
			}
			continue
		}

		backoff.reset()
		c.readLoop(ctx)
		c.drainSendCh()

		if ctx.Err() != nil {
			return
		}

		wait := backoff.next()
		slog.Info("reconnecting", "in", wait)
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
}

func (c *Client) connect(ctx context.Context) error {
	wsURL, err := buildWSURL(c.serverURL, c.deviceID, c.apiKey)
	if err != nil {
		return err
	}

	dialer := websocket.Dialer{
		HandshakeTimeout: 10 * time.Second,
		TLSClientConfig:  c.tlsConfig,
	}
	conn, _, err := dialer.DialContext(ctx, wsURL, nil)
	if err != nil {
		return fmt.Errorf("dial %s: %w", redactWSURL(wsURL), err)
	}

	c.mu.Lock()
	c.conn = conn
	c.writeStop = make(chan struct{})
	c.writeDone = make(chan struct{})
	c.mu.Unlock()

	slog.Info("connected to server", "url", redactWSURL(wsURL))

	// Start write pump
	go c.writePump()

	return nil
}

func (c *Client) readLoop(ctx context.Context) {
	var lastErr error
	defer func() {
		slog.Debug("readLoop exited", "reason", lastErr)
		c.mu.Lock()
		conn := c.conn
		stop := c.writeStop
		c.conn = nil
		c.writeStop = nil
		c.mu.Unlock()
		if stop != nil {
			close(stop)
		}
		if conn != nil {
			conn.Close()
		}
		if c.writeDone != nil {
			<-c.writeDone
		}
	}()

	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil {
		return
	}

	// Keepalive settings. gorilla/websocket does not support retrying a read
	// after any error (including a deadline timeout) on the same connection —
	// doing so panics with "repeated read on failed websocket connection".
	// So pongWait is the only read deadline, and ctx cancellation is handled
	// by closing the connection from another goroutine to unblock ReadJSON,
	// rather than polling with a short deadline and retrying on timeout.
	const pongWait = 60 * time.Second
	conn.SetPongHandler(func(string) error {
		conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})
	conn.SetReadDeadline(time.Now().Add(pongWait))

	stopWatcher := make(chan struct{})
	defer close(stopWatcher)
	go func() {
		select {
		case <-ctx.Done():
			conn.Close()
		case <-stopWatcher:
		}
	}()

	for {
		var raw map[string]json.RawMessage
		err := conn.ReadJSON(&raw)
		if err != nil {
			lastErr = err
			if !websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				slog.Warn("websocket read error", "error", err)
			}
			return
		}
		conn.SetReadDeadline(time.Now().Add(pongWait))

		var msgType string
		if t, ok := raw["type"]; ok {
			if err := json.Unmarshal(t, &msgType); err != nil {
				continue
			}
		}

		switch msgType {
		case MsgTypeCommand:
			var cmd IncomingCommand
			if data, ok := raw["data"]; ok {
				if err := json.Unmarshal(data, &cmd); err == nil && c.onCommand != nil {
					go c.onCommand(cmd)
				}
			}
		case MsgTypeScreenshotRequest:
			var req ScreenshotRequestData
			if data, ok := raw["data"]; ok {
				if err := json.Unmarshal(data, &req); err == nil && c.onCommand != nil {
					go c.onCommand(IncomingCommand{
						CommandID:   req.CommandID,
						CommandType: "screenshot",
						Payload:     map[string]interface{}{},
					})
				}
			}
		case MsgTypeConfigUpdate:
			var update ConfigUpdateData
			if data, ok := raw["data"]; ok {
				if err := json.Unmarshal(data, &update); err == nil && c.onConfig != nil {
					go c.onConfig(update)
				}
			}
		case MsgTypePing:
			c.Send(Message{Type: "pong"})
		case MsgTypeAck:
			// Server acknowledged our message
		}
	}
}

func (c *Client) writePump() {
	slog.Debug("writePump started")
	defer func() {
		slog.Debug("writePump exited")
		if c.writeDone != nil {
			close(c.writeDone)
		}
	}()

	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-c.writeStop:
			return
		case <-ticker.C:
			c.mu.Lock()
			conn := c.conn
			c.mu.Unlock()
			if conn == nil {
				return
			}
			c.writeMu.Lock()
			err := conn.WriteControl(websocket.PingMessage, []byte("ping"), time.Now().Add(10*time.Second))
			c.writeMu.Unlock()
			if err != nil {
				slog.Warn("ping error", "error", err)
				return
			}
		case msg := <-c.sendCh:
			c.mu.Lock()
			conn := c.conn
			c.mu.Unlock()
			if conn == nil {
				return
			}
			c.writeMu.Lock()
			err := conn.WriteJSON(msg)
			c.writeMu.Unlock()
			if err != nil {
				slog.Warn("write error", "error", err)
				return
			}
		}
	}
}

// Send queues a message for sending. Non-blocking; drops if buffer full.
func (c *Client) Send(msg Message) {
	buffered := len(c.sendCh)
	capacity := cap(c.sendCh)
	if capacity > 0 && buffered >= (capacity*3)/4 && shouldDropWhenBusy(msg.Type) {
		slog.Warn("send buffer busy, dropping message", "type", msg.Type)
		return
	}

	if isCriticalMessage(msg.Type) {
		c.sendCh <- msg
		return
	}

	select {
	case c.sendCh <- msg:
	default:
		slog.Warn("send buffer full, dropping message", "type", msg.Type)
	}
}

func (c *Client) SendTelemetry(data TelemetryData) {
	c.Send(Message{Type: MsgTypeTelemetry, Data: data})
}

func (c *Client) SendSoftwareInventory(packages []SoftwarePackage) {
	c.Send(Message{Type: MsgTypeSoftwareInventory, Data: SoftwareInventoryData{Packages: packages}})
}

func (c *Client) SendEvent(data EventData) {
	c.Send(Message{Type: MsgTypeEventReport, Data: data})
}

func (c *Client) SendNTPStatus(data NTPStatusData) {
	c.Send(Message{Type: MsgTypeNTPStatus, Data: data})
}

func (c *Client) SendCommandResult(data CommandResultData) {
	c.Send(Message{Type: MsgTypeCommandResult, Data: data})
}

func (c *Client) SendScreenshotResult(data ScreenshotResultData) {
	c.Send(Message{Type: MsgTypeScreenshotResult, Data: data})
}

func (c *Client) SendProcessList(processes []ProcessInfo) {
	c.Send(Message{Type: MsgTypeProcessList, Data: ProcessListData{Processes: processes}})
}

func (c *Client) SendAgentInfo(info AgentInfoData) {
	c.Send(Message{Type: MsgTypeAgentInfo, Data: info})
}

func (c *Client) SendSSHKeys(keys []SSHKeyInfo) {
	c.Send(Message{Type: MsgTypeSSHKeys, Data: SSHKeysData{Keys: keys}})
}

func (c *Client) IsConnected() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn != nil
}

func (c *Client) drainSendCh() {
	for {
		select {
		case <-c.sendCh:
		default:
			return
		}
	}
}

func isCriticalMessage(msgType string) bool {
	switch msgType {
	case MsgTypeEventReport, MsgTypeCommandResult, MsgTypeScreenshotResult:
		return true
	default:
		return false
	}
}

func shouldDropWhenBusy(msgType string) bool {
	switch msgType {
	case MsgTypeTelemetry, MsgTypeProcessList, MsgTypeNTPStatus, MsgTypeNetworkInfo:
		return true
	default:
		return false
	}
}

func buildWSURL(serverURL, deviceID, apiKey string) (string, error) {
	u, err := url.Parse(serverURL)
	if err != nil {
		return "", err
	}
	// Convert http(s) to ws(s)
	switch u.Scheme {
	case "http":
		u.Scheme = "ws"
	case "https":
		u.Scheme = "wss"
	}
	u.Path = fmt.Sprintf("/ws/device/%s", deviceID)
	q := u.Query()
	q.Set("token", apiKey)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// redactWSURL returns wsURL with the "token" query parameter (the device API
// key) masked, so it is safe to write to logs.
func redactWSURL(wsURL string) string {
	u, err := url.Parse(wsURL)
	if err != nil {
		return "[redacted: unparseable url]"
	}
	q := u.Query()
	if q.Get("token") != "" {
		q.Set("token", "[redacted]")
	}
	u.RawQuery = q.Encode()
	return u.String()
}

// backoff implements exponential backoff with cap
type backoff struct {
	current time.Duration
	initial time.Duration
	max     time.Duration
}

func newBackoff(initial, max time.Duration) *backoff {
	return &backoff{current: initial, initial: initial, max: max}
}

func (b *backoff) next() time.Duration {
	d := b.current
	b.current *= 2
	if b.current > b.max {
		b.current = b.max
	}
	return d
}

func (b *backoff) reset() {
	b.current = b.initial
}
