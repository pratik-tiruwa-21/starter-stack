// Gate 5: CER Threshold — Context Efficiency Ratio monitoring.
// Denies requests when CER drops below critical threshold.
package gates

import (
	"fmt"
	"sync"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// CERGate checks that the context window isn't overloaded.
type CERGate struct {
	mu      sync.RWMutex
	warn    float64
	crit    float64
	current float64
}

// NewCERGate creates a CER gate with the given thresholds.
func NewCERGate(warn, crit float64) *CERGate {
	return &CERGate{
		warn:    warn,
		crit:    crit,
		current: 1.0,
	}
}

// Check evaluates the CER gate.
func (g *CERGate) Check(req *models.ToolCallRequest) models.GateResult {
	start := time.Now()

	budget := req.TokenBudget
	if budget <= 0 {
		budget = 200000
	}

	cer := 1.0
	if budget > 0 {
		cer = float64(budget-req.TokenCount) / float64(budget)
	}
	if cer < 0 {
		cer = 0
	}

	g.mu.Lock()
	g.current = cer
	g.mu.Unlock()

	if cer <= g.crit {
		return models.GateResult{
			Name:    "cer",
			Passed:  false,
			Detail:  fmt.Sprintf("CER critical: %.3f (threshold=%.1f)", cer, g.crit),
			Latency: time.Since(start).Microseconds(),
		}
	}

	return models.GateResult{
		Name:    "cer",
		Passed:  true,
		Detail:  fmt.Sprintf("CER=%.3f (warn=%.1f, crit=%.1f)", cer, g.warn, g.crit),
		Latency: time.Since(start).Microseconds(),
	}
}

// Current returns the latest CER value.
func (g *CERGate) Current() float64 {
	g.mu.RLock()
	defer g.mu.RUnlock()
	return g.current
}
