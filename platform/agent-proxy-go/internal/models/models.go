// Package models defines shared types for the AgentProxy.
package models

import "time"

// ─── Request / Response ──────────────────────────────────────────

// Decision represents the proxy verdict.
type Decision string

const (
	Allow     Decision = "ALLOW"
	Deny      Decision = "DENY"
	HumanGate Decision = "HUMAN_GATE"
)

// ToolCallRequest is the incoming payload from the LLM agent runtime.
type ToolCallRequest struct {
	Skill       string         `json:"skill"`
	Tool        string         `json:"tool"`
	Arguments   map[string]any `json:"arguments,omitempty"`
	Context     string         `json:"context,omitempty"`
	TokenCount  int            `json:"token_count,omitempty"`
	TokenBudget int            `json:"token_budget,omitempty"`
}

// GateResult captures a single gate's evaluation.
type GateResult struct {
	Name    string `json:"name"`
	Passed  bool   `json:"passed"`
	Detail  string `json:"detail"`
	Latency int64  `json:"latency_us"` // microseconds
}

// ScanFinding is a single TTP pattern match.
type ScanFinding struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Severity string `json:"severity"`
	Matches  int    `json:"matches"`
}

// ProxyDecision is the full evaluation result sent back to the caller.
type ProxyDecision struct {
	Decision   Decision       `json:"decision"`
	Reason     string         `json:"reason"`
	Checks     map[string]any `json:"checks"`
	Gates      []GateResult   `json:"gates"`
	LatencyUs  int64          `json:"latency_us"`
	AuditHash  string         `json:"audit_hash,omitempty"`
	ApprovalID string         `json:"approval_id,omitempty"`
}

// ─── Audit ───────────────────────────────────────────────────────

// AuditEntry is an immutable hash-chained log record.
type AuditEntry struct {
	Timestamp string         `json:"timestamp"`
	Skill     string         `json:"skill"`
	Tool      string         `json:"tool"`
	Decision  string         `json:"decision"`
	Reason    string         `json:"reason"`
	Checks    map[string]any `json:"checks"`
	PrevHash  string         `json:"prev_hash"`
	EntryHash string         `json:"entry_hash"`
}

// ChainStatus reports hash-chain verification results.
type ChainStatus struct {
	Valid      bool   `json:"valid"`
	Total      int    `json:"total_entries"`
	Verified   int    `json:"verified"`
	FirstEntry string `json:"first_entry,omitempty"`
	LastEntry  string `json:"last_entry,omitempty"`
}

// ─── System Status ───────────────────────────────────────────────

// SystemStatus is the /api/v1/status payload.
type SystemStatus struct {
	TotalEvaluations int64           `json:"total_evaluations"`
	Allowed          int64           `json:"allowed"`
	Denied           int64           `json:"denied"`
	HumanGated       int64           `json:"human_gated"`
	CERCurrent       float64         `json:"cer_current"`
	Layers           map[string]bool `json:"layers"`
	RPS              float64         `json:"rps"`
	AvgLatencyUs     int64           `json:"avg_latency_us"`
	P99LatencyUs     int64           `json:"p99_latency_us"`
	UptimeSeconds    float64         `json:"uptime_seconds"`
}

// ─── Metrics (real-time) ─────────────────────────────────────────

// Metrics holds rolling performance counters for the dashboard.
type Metrics struct {
	// Ring buffer of last 300 evaluation latencies (5 min @ 1/s)
	LatencyRing []int64 `json:"-"`
	LatencyIdx  int     `json:"-"`
	LatencyFull bool    `json:"-"`

	// Per-second counters for sparkline
	RPS           []float64 `json:"rps_history"`       // last 60s
	DenyRate      []float64 `json:"deny_rate_history"` // last 60s
	CERHistory    []float64 `json:"cer_history"`       // last 60s
	GateLatencies []GateAvg `json:"gate_latencies"`

	// Timestamps for rate calc
	SecondStart  time.Time `json:"-"`
	SecondCount  int64     `json:"-"`
	SecondDenied int64     `json:"-"`
}

// GateAvg is average latency for a specific gate.
type GateAvg struct {
	Name  string `json:"name"`
	AvgUs int64  `json:"avg_us"`
	MaxUs int64  `json:"max_us"`
	P99Us int64  `json:"p99_us"`
}

// ─── WebSocket Events ────────────────────────────────────────────

// WSEvent is broadcast to dashboard clients.
type WSEvent struct {
	Type      string  `json:"type"`
	Timestamp string  `json:"timestamp"`
	Skill     string  `json:"skill,omitempty"`
	Tool      string  `json:"tool,omitempty"`
	Decision  string  `json:"decision,omitempty"`
	Reason    string  `json:"reason,omitempty"`
	LatencyUs int64   `json:"latency_us,omitempty"`
	Gate      string  `json:"gate,omitempty"`
	GateMs    float64 `json:"gate_ms,omitempty"`
}

// ─── Patterns (scanner) ─────────────────────────────────────────

// TTPPattern defines a single threat pattern.
type TTPPattern struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Pattern  string `json:"pattern"`
	Severity string `json:"severity"`
}
