package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/mux"
	"github.com/rs/cors"

	"github.com/ClawdContextOS/agent-proxy/internal/audit"
	"github.com/ClawdContextOS/agent-proxy/internal/config"
	"github.com/ClawdContextOS/agent-proxy/internal/gates"
	"github.com/ClawdContextOS/agent-proxy/internal/models"
	"github.com/ClawdContextOS/agent-proxy/internal/ws"
)

// ---------------------------------------------------------------------------
// Metrics Collector — rolling performance counters for the dashboard
// ---------------------------------------------------------------------------

type gateStats struct {
	total int64
	sumUs int64
	maxUs int64
	ring  [100]int64
	idx   int
	full  bool
}

type metricsCollector struct {
	mu sync.Mutex

	latencies []int64
	latIdx    int
	latFull   bool

	gateLatencies map[string]*gateStats

	rpsHistory      [60]float64
	denyRateHistory [60]float64
	cerHistory      [60]float64
	histIdx         int

	secStart time.Time
	secCount int64
	secDeny  int64
}

func newMetrics() *metricsCollector {
	return &metricsCollector{
		latencies:     make([]int64, 1000),
		gateLatencies: make(map[string]*gateStats),
		secStart:      time.Now(),
	}
}

func (m *metricsCollector) record(totalUs int64, gateResults []models.GateResult, _ float64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.latencies[m.latIdx] = totalUs
	m.latIdx = (m.latIdx + 1) % len(m.latencies)
	if m.latIdx == 0 {
		m.latFull = true
	}

	for _, g := range gateResults {
		gs, ok := m.gateLatencies[g.Name]
		if !ok {
			gs = &gateStats{}
			m.gateLatencies[g.Name] = gs
		}
		gs.total++
		gs.sumUs += g.Latency
		if g.Latency > gs.maxUs {
			gs.maxUs = g.Latency
		}
		gs.ring[gs.idx] = g.Latency
		gs.idx = (gs.idx + 1) % len(gs.ring)
		if gs.idx == 0 {
			gs.full = true
		}
	}

	m.secCount++
}

func (m *metricsCollector) recordDeny() {
	m.mu.Lock()
	m.secDeny++
	m.mu.Unlock()
}

func (m *metricsCollector) tick(cer float64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	elapsed := time.Since(m.secStart).Seconds()
	if elapsed < 1 {
		elapsed = 1
	}

	rps := float64(m.secCount) / elapsed
	denyRate := float64(0)
	if m.secCount > 0 {
		denyRate = float64(m.secDeny) / float64(m.secCount)
	}

	m.rpsHistory[m.histIdx] = rps
	m.denyRateHistory[m.histIdx] = denyRate
	m.cerHistory[m.histIdx] = cer
	m.histIdx = (m.histIdx + 1) % 60

	m.secStart = time.Now()
	m.secCount = 0
	m.secDeny = 0
}

func (m *metricsCollector) avgLatency() int64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	count := len(m.latencies)
	if !m.latFull {
		count = m.latIdx
	}
	if count == 0 {
		return 0
	}
	var sum int64
	for i := 0; i < count; i++ {
		sum += m.latencies[i]
	}
	return sum / int64(count)
}

func (m *metricsCollector) p99Latency() int64 {
	m.mu.Lock()
	defer m.mu.Unlock()
	count := len(m.latencies)
	if !m.latFull {
		count = m.latIdx
	}
	if count == 0 {
		return 0
	}
	sorted := make([]int64, count)
	copy(sorted, m.latencies[:count])
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	idx := int(math.Ceil(float64(count)*0.99)) - 1
	if idx < 0 {
		idx = 0
	}
	return sorted[idx]
}

func (m *metricsCollector) snapshot() map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()

	rps := make([]float64, 60)
	deny := make([]float64, 60)
	cer := make([]float64, 60)
	for i := 0; i < 60; i++ {
		idx := (m.histIdx + i) % 60
		rps[i] = m.rpsHistory[idx]
		deny[i] = m.denyRateHistory[idx]
		cer[i] = m.cerHistory[idx]
	}

	gateAvgs := make([]models.GateAvg, 0, len(m.gateLatencies))
	for name, gs := range m.gateLatencies {
		avg := int64(0)
		if gs.total > 0 {
			avg = gs.sumUs / gs.total
		}
		count := len(gs.ring)
		if !gs.full {
			count = gs.idx
		}
		p99 := int64(0)
		if count > 0 {
			sorted := make([]int64, count)
			copy(sorted, gs.ring[:count])
			sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
			p99idx := int(math.Ceil(float64(count)*0.99)) - 1
			if p99idx < 0 {
				p99idx = 0
			}
			p99 = sorted[p99idx]
		}
		gateAvgs = append(gateAvgs, models.GateAvg{
			Name:  name,
			AvgUs: avg,
			MaxUs: gs.maxUs,
			P99Us: p99,
		})
	}

	return map[string]any{
		"rps_history":       rps,
		"deny_rate_history": deny,
		"cer_history":       cer,
		"gate_latencies":    gateAvgs,
	}
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

type server struct {
	cfg      *config.Config
	pipeline *gates.Pipeline
	auditLog *audit.Logger
	hub      *ws.Hub
	metrics  *metricsCollector

	startTime time.Time

	total      atomic.Int64
	allowed    atomic.Int64
	denied     atomic.Int64
	humanGated atomic.Int64
}

func newServer(cfg *config.Config) (*server, error) {
	auditLog, err := audit.NewLogger(cfg.AuditFile)
	if err != nil {
		return nil, fmt.Errorf("audit init: %w", err)
	}

	rl := gates.NewRateLimiter(cfg.MaxRateRPM)
	hg := gates.NewHumanGate()
	cap := gates.NewCapabilityChecker(cfg.SkillsDir)
	sc := gates.NewSemanticScanner()
	cer := gates.NewCERGate(cfg.CERWarn, cfg.CERCrit)
	pipeline := gates.NewPipeline(rl, hg, cap, sc, cer)

	hub := ws.NewHub()

	return &server{
		cfg:       cfg,
		pipeline:  pipeline,
		auditLog:  auditLog,
		hub:       hub,
		metrics:   newMetrics(),
		startTime: time.Now(),
	}, nil
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

func (s *server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"service": "agent-proxy",
		"layer":   4,
		"runtime": "go",
	})
}

func (s *server) handleStatus(w http.ResponseWriter, _ *http.Request) {
	status := models.SystemStatus{
		UptimeSeconds:    time.Since(s.startTime).Seconds(),
		TotalEvaluations: s.total.Load(),
		Allowed:          s.allowed.Load(),
		Denied:           s.denied.Load(),
		HumanGated:       s.humanGated.Load(),
		CERCurrent:       s.pipeline.CER(),
		Layers: map[string]bool{
			"layer1_scanner":   true,
			"layer2_clawdsign": true,
			"layer3_sandbox":   true,
			"layer4_proxy":     true,
			"layer5_recorder":  true,
			"layer6_snapshot":  false,
		},
		RPS:          0,
		AvgLatencyUs: s.metrics.avgLatency(),
		P99LatencyUs: s.metrics.p99Latency(),
	}
	writeJSON(w, http.StatusOK, status)
}

func (s *server) handleEvaluate(w http.ResponseWriter, r *http.Request) {
	var req models.ToolCallRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	if req.TokenBudget <= 0 {
		req.TokenBudget = 200000
	}

	result := s.pipeline.Evaluate(&req)

	s.total.Add(1)
	switch result.Decision {
	case models.Allow:
		s.allowed.Add(1)
	case models.Deny:
		s.denied.Add(1)
		s.metrics.recordDeny()
	case models.HumanGate:
		s.humanGated.Add(1)
	}

	checks := make(map[string]any)
	for _, g := range result.Gates {
		checks[g.Name] = map[string]any{
			"passed":     g.Passed,
			"detail":     g.Detail,
			"latency_us": g.Latency,
		}
	}

	hash := s.auditLog.Log(req.Skill, req.Tool, string(result.Decision), result.Reason, checks)
	s.metrics.record(result.TotalUs, result.Gates, s.pipeline.CER())

	decision := models.ProxyDecision{
		Decision:  result.Decision,
		Reason:    result.Reason,
		Checks:    checks,
		Gates:     result.Gates,
		LatencyUs: result.TotalUs,
		AuditHash: hash,
	}

	s.hub.Broadcast(models.WSEvent{
		Type:      "evaluation",
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Skill:     req.Skill,
		Tool:      req.Tool,
		Decision:  string(result.Decision),
		Reason:    result.Reason,
		LatencyUs: result.TotalUs,
	})

	for _, g := range result.Gates {
		s.hub.Broadcast(models.WSEvent{
			Type:      "gate",
			Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
			Gate:      g.Name,
			Decision:  boolToStr(g.Passed, "PASS", "FAIL"),
			Reason:    g.Detail,
			GateMs:    float64(g.Latency) / 1000.0,
		})
	}

	writeJSON(w, http.StatusOK, decision)
}

func (s *server) handleAudit(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if q := r.URL.Query().Get("limit"); q != "" {
		fmt.Sscanf(q, "%d", &limit)
	}
	entries := s.auditLog.Recent(limit)
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

func (s *server) handleVerifyAudit(w http.ResponseWriter, _ *http.Request) {
	status := s.auditLog.VerifyChain()
	writeJSON(w, http.StatusOK, status)
}

func (s *server) handleScan(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Content string `json:"content"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	findings := s.pipeline.ScannerRef().Scan(body.Content)
	writeJSON(w, http.StatusOK, map[string]any{"findings": findings, "count": len(findings)})
}

func (s *server) handleSkills(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"skills": s.pipeline.CapabilityRef().Skills()})
}

func (s *server) handleReloadSkills(w http.ResponseWriter, _ *http.Request) {
	s.pipeline.CapabilityRef().Reload()
	skills := s.pipeline.CapabilityRef().Skills()
	writeJSON(w, http.StatusOK, map[string]any{"status": "reloaded", "count": len(skills)})
}

func (s *server) handlePatterns(w http.ResponseWriter, _ *http.Request) {
	patterns := s.pipeline.ScannerRef().Patterns()
	result := make(map[string]any, len(patterns))
	for _, p := range patterns {
		result[p.ID] = map[string]any{"pattern": p.Pattern, "name": p.Name, "severity": p.Severity}
	}
	writeJSON(w, http.StatusOK, map[string]any{"patterns": result})
}

func (s *server) handleMetrics(w http.ResponseWriter, _ *http.Request) {
	snap := s.metrics.snapshot()
	snap["total_evaluations"] = s.total.Load()
	snap["allowed"] = s.allowed.Load()
	snap["denied"] = s.denied.Load()
	snap["human_gated"] = s.humanGated.Load()
	snap["cer_current"] = s.pipeline.CER()
	snap["avg_latency_us"] = s.metrics.avgLatency()
	snap["p99_latency_us"] = s.metrics.p99Latency()
	snap["ws_clients"] = s.hub.ClientCount()
	snap["uptime_seconds"] = time.Since(s.startTime).Seconds()
	writeJSON(w, http.StatusOK, snap)
}

func (s *server) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	s.hub.HandleWebSocket(w, r)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}

func boolToStr(b bool, t, f string) string {
	if b {
		return t
	}
	return f
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

func main() {
	cfg := config.Load()

	srv, err := newServer(cfg)
	if err != nil {
		log.Fatalf("[agent-proxy] Fatal: %v", err)
	}
	defer srv.auditLog.Close()

	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for range ticker.C {
			srv.metrics.tick(srv.pipeline.CER())
		}
	}()

	r := mux.NewRouter()

	r.HandleFunc("/healthz", srv.handleHealth).Methods("GET")

	api := r.PathPrefix("/api/v1").Subrouter()
	api.HandleFunc("/status", srv.handleStatus).Methods("GET")
	api.HandleFunc("/evaluate", srv.handleEvaluate).Methods("POST")
	api.HandleFunc("/audit", srv.handleAudit).Methods("GET")
	api.HandleFunc("/audit/verify", srv.handleVerifyAudit).Methods("GET")
	api.HandleFunc("/scan", srv.handleScan).Methods("POST")
	api.HandleFunc("/skills", srv.handleSkills).Methods("GET")
	api.HandleFunc("/skills/reload", srv.handleReloadSkills).Methods("POST")
	api.HandleFunc("/patterns", srv.handlePatterns).Methods("GET")
	api.HandleFunc("/metrics", srv.handleMetrics).Methods("GET")

	r.HandleFunc("/ws/events", srv.handleWebSocket)

	handler := cors.New(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"*"},
		AllowCredentials: true,
	}).Handler(r)

	addr := ":" + cfg.Port
	log.Printf("========================================================")
	log.Printf("  ClawdContext OS -- AgentProxy (Layer 4)")
	log.Printf("  Runtime: Go | Port: %s | Gates: 5", cfg.Port)
	log.Printf("  Anderson Report: Complete Mediation")
	log.Printf("========================================================")
	log.Printf("[agent-proxy] Skills dir: %s", cfg.SkillsDir)
	log.Printf("[agent-proxy] Audit file: %s", cfg.AuditFile)
	log.Printf("[agent-proxy] Rate limit: %d rpm", cfg.MaxRateRPM)
	log.Printf("[agent-proxy] CER thresholds: warn=%.1f crit=%.1f", cfg.CERWarn, cfg.CERCrit)
	log.Printf("[agent-proxy] Listening on %s", addr)

	if err := http.ListenAndServe(addr, handler); err != nil {
		log.Fatalf("[agent-proxy] Server error: %v", err)
	}
}
