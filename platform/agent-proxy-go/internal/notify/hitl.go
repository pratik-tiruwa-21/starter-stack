package notify

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"
)

// ApprovalStatus tracks the state of a HITL approval.
type ApprovalStatus string

const (
	StatusPending  ApprovalStatus = "pending"
	StatusApproved ApprovalStatus = "approved"
	StatusDenied   ApprovalStatus = "denied"
	StatusExpired  ApprovalStatus = "expired"
)

// Approval represents a pending human-in-the-loop decision.
type Approval struct {
	ID         string         `json:"id"`
	Event      Event          `json:"event"`
	Status     ApprovalStatus `json:"status"`
	CreatedAt  time.Time      `json:"created_at"`
	ExpiresAt  time.Time      `json:"expires_at"`
	ResolvedAt *time.Time     `json:"resolved_at,omitempty"`
	ResolvedBy string         `json:"resolved_by,omitempty"`
}

// ApprovalStats holds summary statistics.
type ApprovalStats struct {
	Pending  int `json:"pending"`
	Approved int `json:"approved"`
	Denied   int `json:"denied"`
	Expired  int `json:"expired"`
	Total    int `json:"total"`
}

// ApprovalManager manages HITL approvals with TTL expiry.
type ApprovalManager struct {
	mu       sync.RWMutex
	pending  map[string]*Approval
	resolved []*Approval
	ttl      time.Duration
	stop     chan struct{}
}

// NewApprovalManager creates a manager with the given TTL.
func NewApprovalManager(ttl time.Duration) *ApprovalManager {
	am := &ApprovalManager{
		pending: make(map[string]*Approval),
		ttl:     ttl,
		stop:    make(chan struct{}),
	}
	go am.expiryLoop()
	return am
}

// Create registers a new pending approval.
func (am *ApprovalManager) Create(event Event) *Approval {
	id := generateID()
	now := time.Now()
	a := &Approval{
		ID:        id,
		Event:     event,
		Status:    StatusPending,
		CreatedAt: now,
		ExpiresAt: now.Add(am.ttl),
	}
	am.mu.Lock()
	am.pending[id] = a
	am.mu.Unlock()
	return a
}

// Approve marks an approval as approved.
func (am *ApprovalManager) Approve(id, actor string) (*Approval, bool) {
	am.mu.Lock()
	defer am.mu.Unlock()
	a, ok := am.pending[id]
	if !ok {
		return nil, false
	}
	now := time.Now()
	a.Status = StatusApproved
	a.ResolvedAt = &now
	a.ResolvedBy = actor
	delete(am.pending, id)
	am.resolved = append(am.resolved, a)
	return a, true
}

// Deny marks an approval as denied.
func (am *ApprovalManager) Deny(id, actor string) (*Approval, bool) {
	am.mu.Lock()
	defer am.mu.Unlock()
	a, ok := am.pending[id]
	if !ok {
		return nil, false
	}
	now := time.Now()
	a.Status = StatusDenied
	a.ResolvedAt = &now
	a.ResolvedBy = actor
	delete(am.pending, id)
	am.resolved = append(am.resolved, a)
	return a, true
}

// Get retrieves an approval by ID from pending or resolved.
func (am *ApprovalManager) Get(id string) (*Approval, bool) {
	am.mu.RLock()
	defer am.mu.RUnlock()
	if a, ok := am.pending[id]; ok {
		return a, true
	}
	for _, a := range am.resolved {
		if a.ID == id {
			return a, true
		}
	}
	return nil, false
}

// Pending returns all pending approvals.
func (am *ApprovalManager) Pending() []*Approval {
	am.mu.RLock()
	defer am.mu.RUnlock()
	out := make([]*Approval, 0, len(am.pending))
	for _, a := range am.pending {
		out = append(out, a)
	}
	return out
}

// History returns the last N resolved approvals.
func (am *ApprovalManager) History(limit int) []*Approval {
	am.mu.RLock()
	defer am.mu.RUnlock()
	n := len(am.resolved)
	if limit > n {
		limit = n
	}
	out := make([]*Approval, limit)
	copy(out, am.resolved[n-limit:])
	return out
}

// Stats returns approval statistics.
func (am *ApprovalManager) Stats() ApprovalStats {
	am.mu.RLock()
	defer am.mu.RUnlock()
	s := ApprovalStats{Pending: len(am.pending)}
	for _, a := range am.resolved {
		switch a.Status {
		case StatusApproved:
			s.Approved++
		case StatusDenied:
			s.Denied++
		case StatusExpired:
			s.Expired++
		}
	}
	s.Total = s.Pending + s.Approved + s.Denied + s.Expired
	return s
}

// Stop halts the expiry loop.
func (am *ApprovalManager) Stop() {
	close(am.stop)
}

func (am *ApprovalManager) expiryLoop() {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-am.stop:
			return
		case now := <-ticker.C:
			am.mu.Lock()
			for id, a := range am.pending {
				if now.After(a.ExpiresAt) {
					a.Status = StatusExpired
					a.ResolvedAt = &now
					a.ResolvedBy = "system:ttl"
					delete(am.pending, id)
					am.resolved = append(am.resolved, a)
				}
			}
			am.mu.Unlock()
		}
	}
}

func generateID() string {
	b := make([]byte, 12)
	rand.Read(b)
	return hex.EncodeToString(b)
}
