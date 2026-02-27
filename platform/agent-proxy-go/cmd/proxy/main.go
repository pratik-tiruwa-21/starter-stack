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
	"github.com/ClawdContextOS/agent-proxy/internal/notify"
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

	// Per-skill token burn tracking
	skillTokens map[string]*skillBurn
}

type skillBurn struct {
	TotalTokens int64     `json:"total_tokens"`
	Calls       int64     `json:"calls"`
	History     [60]int64 `json:"-"` // last 60 seconds
	HistIdx     int       `json:"-"`
	LastSeen    time.Time `json:"last_seen"`
}

func newMetrics() *metricsCollector {
	return &metricsCollector{
		latencies:     make([]int64, 1000),
		gateLatencies: make(map[string]*gateStats),
		skillTokens:   make(map[string]*skillBurn),
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

// recordSkillTokens tracks per-skill token consumption.
func (m *metricsCollector) recordSkillTokens(skill string, tokens int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	sb, ok := m.skillTokens[skill]
	if !ok {
		sb = &skillBurn{}
		m.skillTokens[skill] = sb
	}
	sb.TotalTokens += int64(tokens)
	sb.Calls++
	sb.History[sb.HistIdx] += int64(tokens)
	sb.LastSeen = time.Now()
}

// tickSkillTokens advances the per-skill history ring.
func (m *metricsCollector) tickSkillTokens() {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, sb := range m.skillTokens {
		sb.HistIdx = (sb.HistIdx + 1) % 60
		sb.History[sb.HistIdx] = 0
	}
}

// skillBurnSnapshot returns per-skill token stats.
func (m *metricsCollector) skillBurnSnapshot() map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()
	result := make(map[string]any, len(m.skillTokens))
	for name, sb := range m.skillTokens {
		hist := make([]int64, 60)
		for i := 0; i < 60; i++ {
			idx := (sb.HistIdx + 1 + i) % 60
			hist[i] = sb.History[idx]
		}
		result[name] = map[string]any{
			"total_tokens": sb.TotalTokens,
			"calls":        sb.Calls,
			"history":      hist,
			"last_seen":    sb.LastSeen.Format(time.RFC3339),
		}
	}
	return result
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
	cfg       *config.Config
	pipeline  *gates.Pipeline
	auditLog  *audit.Logger
	hub       *ws.Hub
	metrics   *metricsCollector
	notifyHub *notify.Hub
	approvals *notify.ApprovalManager

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

	wsHub := ws.NewHub()

	// Build notification channels
	channels := []notify.Channel{
		notify.NewNtfy(cfg.NtfyURL, cfg.NtfyTopic, cfg.NtfyToken),
		notify.NewDiscord(cfg.DiscordWebhook),
		notify.NewTelegram(cfg.TelegramBotToken, cfg.TelegramChatID),
		notify.NewSlack(cfg.SlackWebhook),
		notify.NewMatrix(cfg.MatrixHomeserver, cfg.MatrixRoomID, cfg.MatrixAccessToken),
	}
	notifyHub := notify.NewHub(channels)
	approvals := notify.NewApprovalManager(time.Duration(cfg.HITLTimeoutSec) * time.Second)

	return &server{
		cfg:       cfg,
		pipeline:  pipeline,
		auditLog:  auditLog,
		hub:       wsHub,
		metrics:   newMetrics(),
		notifyHub: notifyHub,
		approvals: approvals,
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

	// Per-skill token burn
	if req.TokenCount > 0 {
		s.metrics.recordSkillTokens(req.Skill, req.TokenCount)
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

	// --- Notification dispatch ---
	severity := notify.SeverityInfo
	if result.Decision == models.Deny {
		severity = notify.SeverityCritical
	} else if result.Decision == models.HumanGate {
		severity = notify.SeverityWarning
	}

	evt := notify.Event{
		Severity:  severity,
		Decision:  string(result.Decision),
		Skill:     req.Skill,
		Tool:      req.Tool,
		Reason:    result.Reason,
		Risk:      float64(result.TotalUs) / 1000.0,
		Timestamp: time.Now(),
	}

	if result.Decision == models.HumanGate {
		approval := s.approvals.Create(evt)
		evt.NeedsApproval = true
		evt.ApprovalID = approval.ID
		decision.ApprovalID = approval.ID
	}

	s.notifyHub.Notify(evt)
	// --- End notification dispatch ---

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
	snap["skill_token_burn"] = s.metrics.skillBurnSnapshot()
	snap["notifications"] = map[string]any{
		"channels": s.notifyHub.Status(),
		"enabled":  s.notifyHub.EnabledChannels(),
	}
	writeJSON(w, http.StatusOK, snap)
}

func (s *server) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	s.hub.HandleWebSocket(w, r)
}

// ---------------------------------------------------------------------------
// Notification & HITL Handlers
// ---------------------------------------------------------------------------

func (s *server) handleNotifyStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"channels": s.notifyHub.Status(),
		"enabled":  s.notifyHub.EnabledChannels(),
	})
}

func (s *server) handleNotifyHistory(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if q := r.URL.Query().Get("limit"); q != "" {
		fmt.Sscanf(q, "%d", &limit)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"history": s.notifyHub.History(limit),
	})
}

func (s *server) handleNotifyTest(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Channel string `json:"channel"`
	}
	json.NewDecoder(r.Body).Decode(&body)

	evt := notify.Event{
		Severity:  notify.SeverityInfo,
		Decision:  "TEST",
		Skill:     "test-skill",
		Tool:      "test-tool",
		Reason:    "Test notification from ClawdContext OS dashboard",
		Timestamp: time.Now(),
	}

	s.notifyHub.Notify(evt)

	writeJSON(w, http.StatusOK, map[string]string{"status": "sent"})
}

func (s *server) handleHITLPending(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"pending": s.approvals.Pending(),
		"stats":   s.approvals.Stats(),
	})
}

func (s *server) handleHITLHistory(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if q := r.URL.Query().Get("limit"); q != "" {
		fmt.Sscanf(q, "%d", &limit)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"history": s.approvals.History(limit),
		"stats":   s.approvals.Stats(),
	})
}

func (s *server) handleHITLApprove(w http.ResponseWriter, r *http.Request) {
	id := mux.Vars(r)["id"]
	source := r.URL.Query().Get("source")
	if source == "" {
		source = "dashboard"
	}
	if _, ok := s.approvals.Approve(id, source); ok {
		s.hub.Broadcast(models.WSEvent{
			Type:      "hitl_resolved",
			Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
			Decision:  "APPROVED",
			Reason:    "Approved by " + source,
		})
		writeJSON(w, http.StatusOK, map[string]string{"status": "approved", "id": id})
	} else {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "approval not found or expired"})
	}
}

func (s *server) handleHITLDeny(w http.ResponseWriter, r *http.Request) {
	id := mux.Vars(r)["id"]
	source := r.URL.Query().Get("source")
	if source == "" {
		source = "dashboard"
	}
	if _, ok := s.approvals.Deny(id, source); ok {
		s.hub.Broadcast(models.WSEvent{
			Type:      "hitl_resolved",
			Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
			Decision:  "DENIED",
			Reason:    "Denied by " + source,
		})
		writeJSON(w, http.StatusOK, map[string]string{"status": "denied", "id": id})
	} else {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "approval not found or expired"})
	}
}

func (s *server) handleSkillTokenBurn(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"skill_token_burn": s.metrics.skillBurnSnapshot(),
	})
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
			srv.metrics.tickSkillTokens()
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

	// Notification endpoints
	api.HandleFunc("/notifications/status", srv.handleNotifyStatus).Methods("GET")
	api.HandleFunc("/notifications/history", srv.handleNotifyHistory).Methods("GET")
	api.HandleFunc("/notifications/test", srv.handleNotifyTest).Methods("POST")

	// HITL endpoints
	api.HandleFunc("/hitl/pending", srv.handleHITLPending).Methods("GET")
	api.HandleFunc("/hitl/history", srv.handleHITLHistory).Methods("GET")
	api.HandleFunc("/hitl/approve/{id}", srv.handleHITLApprove).Methods("POST")
	api.HandleFunc("/hitl/deny/{id}", srv.handleHITLDeny).Methods("POST")

	// Per-skill token burn
	api.HandleFunc("/skills/token-burn", srv.handleSkillTokenBurn).Methods("GET")

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
	enabled := srv.notifyHub.EnabledChannels()
	if len(enabled) > 0 {
		log.Printf("[agent-proxy] Notifications: %v", enabled)
	} else {
		log.Printf("[agent-proxy] Notifications: none configured")
	}
	log.Printf("[agent-proxy] HITL timeout: %ds", cfg.HITLTimeoutSec)
	log.Printf("[agent-proxy] Listening on %s", addr)

	if err := http.ListenAndServe(addr, handler); err != nil {
		log.Fatalf("[agent-proxy] Server error: %v", err)
	}
}
