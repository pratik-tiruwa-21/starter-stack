package notify

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Ntfy sends notifications via ntfy.sh.
type Ntfy struct {
	url   string
	topic string
	token string
}

// NewNtfy creates an Ntfy channel.
func NewNtfy(url, topic, token string) *Ntfy {
	if url == "" {
		url = "https://ntfy.sh"
	}
	return &Ntfy{url: url, topic: topic, token: token}
}

func (n *Ntfy) Name() string  { return "ntfy" }
func (n *Ntfy) Enabled() bool { return n.topic != "" }

func (n *Ntfy) Send(event Event) error {
	title, body := FormatEvent(event)
	return n.post(title, body, ntfyPriority(event), ntfyTags(event), "")
}

func (n *Ntfy) SendHITL(event Event) (string, error) {
	title, body := FormatEvent(event)
	body = "HUMAN APPROVAL REQUIRED\n" + body + "\nApproval ID: " + event.ApprovalID
	actions := fmt.Sprintf("http, Approve, %s/api/v1/hitl/approve/%s, method=POST; http, Deny, %s/api/v1/hitl/deny/%s, method=POST",
		n.url, event.ApprovalID, n.url, event.ApprovalID)
	err := n.post(title, body, "urgent", "lock,bust_in_silhouette", actions)
	return event.ApprovalID, err
}

func (n *Ntfy) post(title, body, priority, tags, actions string) error {
	url := fmt.Sprintf("%s/%s", n.url, n.topic)
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewBufferString(body))
	if err != nil {
		return fmt.Errorf("ntfy request: %w", err)
	}
	req.Header.Set("Title", title)
	req.Header.Set("Priority", priority)
	req.Header.Set("Tags", tags)
	if actions != "" {
		req.Header.Set("Actions", actions)
	}
	if n.token != "" {
		req.Header.Set("Authorization", "Bearer "+n.token)
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("ntfy send: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 400 {
		return fmt.Errorf("ntfy status %d", resp.StatusCode)
	}
	return nil
}

func ntfyPriority(e Event) string {
	switch e.Decision {
	case "DENY":
		return "urgent"
	case "HUMAN_GATE":
		return "high"
	default:
		return "default"
	}
}

func ntfyTags(e Event) string {
	switch e.Decision {
	case "DENY":
		return "skull,rotating_light"
	case "HUMAN_GATE":
		return "warning,bust_in_silhouette"
	default:
		return "white_check_mark"
	}
}
