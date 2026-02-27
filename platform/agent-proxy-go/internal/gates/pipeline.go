// Package gates — Pipeline orchestrates the 5-gate evaluation sequence.
// Each gate runs serially (fail-fast), but the gates themselves are goroutine-safe.
package gates

import (
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// Pipeline orchestrates the 5-gate evaluation.
type Pipeline struct {
	rateLimiter *RateLimiter
	humanGate   *HumanGate
	capability  *CapabilityChecker
	scanner     *SemanticScanner
	cer         *CERGate
}

// NewPipeline creates a fully wired 5-gate pipeline.
func NewPipeline(rl *RateLimiter, hg *HumanGate, cap *CapabilityChecker, sc *SemanticScanner, cer *CERGate) *Pipeline {
	return &Pipeline{
		rateLimiter: rl,
		humanGate:   hg,
		capability:  cap,
		scanner:     sc,
		cer:         cer,
	}
}

// EvalResult captures the full pipeline evaluation.
type EvalResult struct {
	Decision models.Decision
	Reason   string
	Gates    []models.GateResult
	TotalUs  int64
}

// Evaluate runs all 5 gates in sequence (fail-fast).
func (p *Pipeline) Evaluate(req *models.ToolCallRequest) EvalResult {
	start := time.Now()
	gates := make([]models.GateResult, 0, 5)

	// Gate 1: Rate limit
	g1 := p.rateLimiter.Check(req)
	gates = append(gates, g1)
	if !g1.Passed {
		return EvalResult{
			Decision: models.Deny,
			Reason:   g1.Detail,
			Gates:    gates,
			TotalUs:  time.Since(start).Microseconds(),
		}
	}

	// Gate 2: Human-in-the-loop
	g2 := p.humanGate.Check(req)
	gates = append(gates, g2)
	if !g2.Passed {
		return EvalResult{
			Decision: models.HumanGate,
			Reason:   g2.Detail,
			Gates:    gates,
			TotalUs:  time.Since(start).Microseconds(),
		}
	}

	// Gate 3: Capability check
	g3 := p.capability.Check(req)
	gates = append(gates, g3)
	if !g3.Passed {
		return EvalResult{
			Decision: models.Deny,
			Reason:   g3.Detail,
			Gates:    gates,
			TotalUs:  time.Since(start).Microseconds(),
		}
	}

	// Gate 4: Semantic scan
	g4 := p.scanner.Check(req)
	gates = append(gates, g4)
	if !g4.Passed {
		return EvalResult{
			Decision: models.Deny,
			Reason:   g4.Detail,
			Gates:    gates,
			TotalUs:  time.Since(start).Microseconds(),
		}
	}

	// Gate 5: CER threshold
	g5 := p.cer.Check(req)
	gates = append(gates, g5)
	if !g5.Passed {
		return EvalResult{
			Decision: models.Deny,
			Reason:   g5.Detail,
			Gates:    gates,
			TotalUs:  time.Since(start).Microseconds(),
		}
	}

	return EvalResult{
		Decision: models.Allow,
		Reason:   "All 5 security gates passed",
		Gates:    gates,
		TotalUs:  time.Since(start).Microseconds(),
	}
}

// CER returns the current CER value.
func (p *Pipeline) CER() float64 {
	return p.cer.Current()
}

// RateLimiterRef returns the rate limiter for status queries.
func (p *Pipeline) RateLimiterRef() *RateLimiter {
	return p.rateLimiter
}

// CapabilityRef returns the capability checker for skill listing.
func (p *Pipeline) CapabilityRef() *CapabilityChecker {
	return p.capability
}

// ScannerRef returns the scanner for direct scan requests.
func (p *Pipeline) ScannerRef() *SemanticScanner {
	return p.scanner
}
