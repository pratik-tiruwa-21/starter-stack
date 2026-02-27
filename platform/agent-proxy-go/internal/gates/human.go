// Gate 2: Human-in-the-Loop — blocks destructive operations pending approval.
package gates

import (
	"fmt"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// HumanGateTools lists tool names that always require manual approval.
var HumanGateTools = map[string]bool{
	"file_delete":       true,
	"exec":              true,
	"credential_access": true,
	"network_external":  true,
	"container_exec":    true,
	"database_drop":     true,
	"key_rotation":      true,
}

// HumanGate checks if the requested tool requires human approval.
type HumanGate struct{}

// NewHumanGate creates a new human gate.
func NewHumanGate() *HumanGate {
	return &HumanGate{}
}

// Check evaluates whether the tool requires human-in-the-loop approval.
// Returns passed=false if the tool is in the gate list (decision becomes HUMAN_GATE, not DENY).
func (h *HumanGate) Check(req *models.ToolCallRequest) models.GateResult {
	start := time.Now()

	if HumanGateTools[req.Tool] {
		return models.GateResult{
			Name:    "human_gate",
			Passed:  false,
			Detail:  fmt.Sprintf("Tool '%s' requires human-in-the-loop approval", req.Tool),
			Latency: time.Since(start).Microseconds(),
		}
	}

	return models.GateResult{
		Name:    "human_gate",
		Passed:  true,
		Detail:  "No human approval required",
		Latency: time.Since(start).Microseconds(),
	}
}

// IsHumanGateTool returns true if the tool name is on the gate list.
func IsHumanGateTool(tool string) bool {
	return HumanGateTools[tool]
}
