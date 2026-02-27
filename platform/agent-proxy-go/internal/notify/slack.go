package notify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Slack sends notifications via Slack Incoming Webhook.
type Slack struct {
	webhookURL string
}

// NewSlack creates a Slack channel.
func NewSlack(webhookURL string) *Slack {
	return &Slack{webhookURL: webhookURL}
}

func (s *Slack) Name() string  { return "slack" }
func (s *Slack) Enabled() bool { return s.webhookURL != "" }

func (s *Slack) Send(event Event) error {
	title, body := FormatEvent(event)
	color := slackColor(event)
	payload := map[string]any{
		"username":   "ClawdContext OS",
		"icon_emoji": ":shield:",
		"attachments": []map[string]any{
			{
				"color":     color,
				"title":     title,
				"text":      body,
				"footer":    "AgentProxy Layer 4",
				"ts":        event.Timestamp.Unix(),
				"mrkdwn_in": []string{"text"},
				"fields": []map[string]any{
					{"title": "Skill", "value": event.Skill, "short": true},
					{"title": "Tool", "value": event.Tool, "short": true},
				},
			},
		},
	}
	return s.post(payload)
}

func (s *Slack) SendHITL(event Event) (string, error) {
	title, body := FormatEvent(event)
	payload := map[string]any{
		"username":   "ClawdContext OS",
		"icon_emoji": ":lock:",
		"text":       "*HUMAN APPROVAL REQUIRED*",
		"attachments": []map[string]any{
			{
				"color":     "#FFB300",
				"title":     title,
				"text":      body,
				"footer":    "Approval ID: " + event.ApprovalID,
				"ts":        event.Timestamp.Unix(),
				"mrkdwn_in": []string{"text"},
				"actions": []map[string]any{
					{"type": "button", "text": "Approve", "style": "primary", "value": "approve:" + event.ApprovalID},
					{"type": "button", "text": "Deny", "style": "danger", "value": "deny:" + event.ApprovalID},
				},
			},
		},
	}
	err := s.post(payload)
	return event.ApprovalID, err
}

func (s *Slack) post(payload any) error {
	b, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("slack marshal: %w", err)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Post(s.webhookURL, "application/json", bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("slack send: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return fmt.Errorf("slack status %d", resp.StatusCode)
	}
	return nil
}

func slackColor(e Event) string {
	switch e.Decision {
	case "DENY":
		return "#FF3D71"
	case "HUMAN_GATE":
		return "#FFB300"
	default:
		return "#00E676"
	}
}
