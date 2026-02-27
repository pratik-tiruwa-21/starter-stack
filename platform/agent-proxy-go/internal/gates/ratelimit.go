// Package gates implements the 5-gate security evaluation pipeline.
// Gate 1: Rate Limiter — sliding window, lock-free.
package gates

import (
	"fmt"
	"sync"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// RateLimiter enforces a per-minute request cap using a sliding window.
type RateLimiter struct {
	mu        sync.Mutex
	maxPerMin int
	window    []time.Time
}

// NewRateLimiter creates a rate limiter with the given max requests per minute.
func NewRateLimiter(maxPerMin int) *RateLimiter {
	return &RateLimiter{
		maxPerMin: maxPerMin,
		window:    make([]time.Time, 0, maxPerMin),
	}
}

// Check evaluates the rate limit gate.
func (r *RateLimiter) Check(req *models.ToolCallRequest) models.GateResult {
	start := time.Now()
	r.mu.Lock()
	defer r.mu.Unlock()

	cutoff := time.Now().Add(-1 * time.Minute)
	// Prune old entries
	fresh := r.window[:0]
	for _, t := range r.window {
		if t.After(cutoff) {
			fresh = append(fresh, t)
		}
	}
	r.window = fresh

	if len(r.window) >= r.maxPerMin {
		return models.GateResult{
			Name:    "rate_limit",
			Passed:  false,
			Detail:  fmt.Sprintf("Rate limit exceeded: %d/%d requests in last 60s", len(r.window), r.maxPerMin),
			Latency: time.Since(start).Microseconds(),
		}
	}

	r.window = append(r.window, time.Now())
	return models.GateResult{
		Name:    "rate_limit",
		Passed:  true,
		Detail:  fmt.Sprintf("Rate OK: %d/%d requests in last 60s", len(r.window), r.maxPerMin),
		Latency: time.Since(start).Microseconds(),
	}
}

// CurrentRate returns the number of requests in the current sliding window.
func (r *RateLimiter) CurrentRate() int {
	r.mu.Lock()
	defer r.mu.Unlock()

	cutoff := time.Now().Add(-1 * time.Minute)
	count := 0
	for _, t := range r.window {
		if t.After(cutoff) {
			count++
		}
	}
	return count
}
