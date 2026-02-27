package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Discord sends notifications via Discord webhook.
type Discord struct {
	webhookURL string
}

// NewDiscord creates a Discord channel.
func NewDiscord(webhookURL string) *Discord {
	return &Discord{webhookURL: webhookURL}
}

func (d *Discord) Name() string  { return "discord" }
func (d *Discord) Enabled() bool { return d.webhookURL != "" }

func (d *Discord) Send(event Event) error {
	title, body := FormatEvent(event)
	embed := map[string]any{
		"title":       title,
		"description": body,
		"color":       discordColor(event),
		"timestamp":   event.Timestamp.Format(time.RFC3339),
		"fields": []map[string]any{
			{"name": "Skill", "value": event.Skill, "inline": true},
			{"name": "Tool", "value": event.Tool, "inline": true},
			{"name": "Risk", "value": fmt.Sprintf("%.2f", event.Risk), "inline": true},
		},
	}
	payload := map[string]any{
		"username": "ClawdContext OS",
		"embeds":   []any{embed},
	}
	return d.post(payload)
}

func (d *Discord) SendHITL(event Event) (string, error) {
	title, body := FormatEvent(event)
	embed := map[string]any{
		"title":       "HUMAN APPROVAL REQUIRED",
		"description": title + "\n" + body + "\n\nApproval ID: " + event.ApprovalID + "\nReact with a checkmark to approve or X to deny.",
		"color":       0xFFB300,
		"timestamp":   event.Timestamp.Format(time.RFC3339),
	}
	payload := map[string]any{
		"username": "ClawdContext OS",
		"embeds":   []any{embed},
	}
	err := d.post(payload)
	return event.ApprovalID, err
}

func (d *Discord) post(payload any) error {
	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("discord marshal: %w", err)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Post(d.webhookURL, "application/json", bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("discord send: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return fmt.Errorf("discord status %d", resp.StatusCode)
	}
	return nil
}

func discordColor(e Event) int {
	switch e.Decision {
	case "DENY":
		return 0xFF3D71
	case "HUMAN_GATE":
		return 0xFFB300
	default:
		return 0x00E676
	}
}
