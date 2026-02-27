package notify

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Matrix sends notifications via Matrix client-server API.
type Matrix struct {
	homeserver  string
	roomID      string
	accessToken string
}

// NewMatrix creates a Matrix channel.
func NewMatrix(homeserver, roomID, accessToken string) *Matrix {
	return &Matrix{
		homeserver:  homeserver,
		roomID:      roomID,
		accessToken: accessToken,
	}
}

func (m *Matrix) Name() string  { return "matrix" }
func (m *Matrix) Enabled() bool { return m.homeserver != "" && m.roomID != "" && m.accessToken != "" }

func (m *Matrix) Send(event Event) error {
	title, body := FormatEvent(event)
	htmlBody := fmt.Sprintf("<h3>%s</h3><pre>%s</pre>", title, body)
	return m.sendMessage(title+"\n"+body, htmlBody)
}

func (m *Matrix) SendHITL(event Event) (string, error) {
	title, body := FormatEvent(event)
	plain := fmt.Sprintf("HUMAN APPROVAL REQUIRED\n%s\n%s\nApproval ID: %s\nReply APPROVE %s or DENY %s",
		title, body, event.ApprovalID, event.ApprovalID, event.ApprovalID)
	html := fmt.Sprintf(
		"<h3>HUMAN APPROVAL REQUIRED</h3><h4>%s</h4><pre>%s</pre>"+
			"<p>Approval ID: <code>%s</code></p>"+
			"<p>Reply <b>APPROVE %s</b> or <b>DENY %s</b></p>",
		title, body, event.ApprovalID, event.ApprovalID, event.ApprovalID)
	err := m.sendMessage(plain, html)
	return event.ApprovalID, err
}

func (m *Matrix) sendMessage(plain, html string) error {
	txnID := matrixTxnID()
	url := fmt.Sprintf("%s/_matrix/client/v3/rooms/%s/send/m.room.message/%s",
		m.homeserver, m.roomID, txnID)
	payload := map[string]any{
		"msgtype":        "m.text",
		"body":           plain,
		"format":         "org.matrix.custom.html",
		"formatted_body": html,
	}
	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("matrix marshal: %w", err)
	}
	req, err := http.NewRequest(http.MethodPut, url, bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("matrix request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+m.accessToken)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("matrix send: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return fmt.Errorf("matrix status %d", resp.StatusCode)
	}
	return nil
}

func matrixTxnID() string {
	b := make([]byte, 16)
	rand.Read(b)
	return hex.EncodeToString(b)
}
