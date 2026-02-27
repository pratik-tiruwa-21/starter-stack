package notify

import (
	"fmt"
	"log"
	"sync"
	"time"
)

// Severity levels for notification events.
type Severity string

const (
	SeverityCritical Severity = "critical"
	SeverityWarning  Severity = "warning"
	SeverityInfo     Severity = "info"
)

// Event represents a notification-worthy proxy event.
type Event struct {
	Decision      string    `json:"decision"`
	Skill         string    `json:"skill"`
	Tool          string    `json:"tool"`
	Reason        string    `json:"reason"`
	Risk          float64   `json:"risk"`
	Severity      Severity  `json:"severity"`
	Timestamp     time.Time `json:"timestamp"`
	NeedsApproval bool      `json:"needs_approval,omitempty"`
	ApprovalID    string    `json:"approval_id,omitempty"`
}

// Channel is the interface every notification backend implements.
type Channel interface {
	Name() string
	Enabled() bool
	Send(event Event) error
	SendHITL(event Event) (string, error)
}

// ChannelStatus reports the state of a notification channel.
type ChannelStatus struct {
	Name    string `json:"name"`
	Enabled bool   `json:"enabled"`
	Sent    int64  `json:"sent"`
	Errors  int64  `json:"errors"`
	Last    string `json:"last,omitempty"`
}

// HistoryEntry records a dispatched notification.
type HistoryEntry struct {
	Timestamp time.Time `json:"timestamp"`
	Channel   string    `json:"channel"`
	Decision  string    `json:"decision"`
	Skill     string    `json:"skill"`
	Tool      string    `json:"tool"`
	OK        bool      `json:"ok"`
	Error     string    `json:"error,omitempty"`
}

// Hub dispatches events to all enabled channels.
type Hub struct {
	mu       sync.RWMutex
	channels []Channel
	stats    map[string]*ChannelStatus
	history  []HistoryEntry
}

// NewHub creates a notification hub with the given channels.
func NewHub(channels []Channel) *Hub {
	stats := make(map[string]*ChannelStatus)
	for _, ch := range channels {
		stats[ch.Name()] = &ChannelStatus{Name: ch.Name(), Enabled: ch.Enabled()}
	}
	return &Hub{channels: channels, stats: stats}
}

// Notify dispatches an event to all enabled channels asynchronously.
func (h *Hub) Notify(event Event) {
	for _, ch := range h.channels {
		if !ch.Enabled() {
			continue
		}
		go func(c Channel) {
			var errStr string
			err := c.Send(event)
			ok := err == nil
			if err != nil {
				errStr = err.Error()
				log.Printf("[notify] %s error: %v", c.Name(), err)
			}
			h.mu.Lock()
			s := h.stats[c.Name()]
			if ok {
				s.Sent++
			} else {
				s.Errors++
			}
			s.Last = time.Now().Format(time.RFC3339)
			h.history = append(h.history, HistoryEntry{
				Timestamp: time.Now(),
				Channel:   c.Name(),
				Decision:  event.Decision,
				Skill:     event.Skill,
				Tool:      event.Tool,
				OK:        ok,
				Error:     errStr,
			})
			if len(h.history) > 500 {
				h.history = h.history[len(h.history)-500:]
			}
			h.mu.Unlock()
		}(ch)
	}
}

// Status returns the status of all channels.
func (h *Hub) Status() []ChannelStatus {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := make([]ChannelStatus, 0, len(h.channels))
	for _, ch := range h.channels {
		if s, ok := h.stats[ch.Name()]; ok {
			out = append(out, *s)
		}
	}
	return out
}

// History returns the last N notification history entries.
func (h *Hub) History(limit int) []HistoryEntry {
	h.mu.RLock()
	defer h.mu.RUnlock()
	n := len(h.history)
	if limit > n {
		limit = n
	}
	out := make([]HistoryEntry, limit)
	copy(out, h.history[n-limit:])
	return out
}

// EnabledChannels returns names of enabled channels.
func (h *Hub) EnabledChannels() []string {
	var out []string
	for _, ch := range h.channels {
		if ch.Enabled() {
			out = append(out, ch.Name())
		}
	}
	return out
}

// FormatEvent produces a human-readable title and body.
func FormatEvent(e Event) (string, string) {
	title := fmt.Sprintf("[%s] %s > %s", e.Decision, e.Skill, e.Tool)
	body := fmt.Sprintf("Reason: %s\nRisk: %.2f\nTime: %s",
		e.Reason, e.Risk, e.Timestamp.Format(time.RFC3339))
	return title, body
}
