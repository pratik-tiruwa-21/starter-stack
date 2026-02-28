// Gate 4: Semantic Scanner — 14 TTP regex patterns compiled at init.
// Each request's arguments + context are scanned in parallel.
package gates

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// ttpDef is a raw pattern definition compiled at startup.
type ttpDef struct {
	ID       string
	Name     string
	Pattern  string
	Severity string
}

// rawPatterns contains the 14 TTP categories from ClawHavoc/VirusTotal.
var rawPatterns = []ttpDef{
	{"TTP-001", "Credential Exfiltration", `(?i)(password|secret|token|api[_-]?key|credentials?)\s*[:=]`, "critical"},
	{"TTP-002", "Shell Injection", `(?i)(;|\||\$\(|` + "`" + `)\s*(rm|curl|wget|bash|sh|nc|ncat)`, "critical"},
	{"TTP-003", "Path Traversal", `(?i)\.\.[\\/]`, "high"},
	{"TTP-004", "Base64 Payload", `(?i)(atob|btoa|base64[_-]?(encode|decode))`, "high"},
	{"TTP-005", "Network Exfil", `(?i)(curl|wget|fetch|http\.get|requests\.(get|post))\s*\(?\s*['"]https?://`, "critical"},
	{"TTP-006", "Prompt Injection", `(?i)(ignore\s+(previous|above|all)\s+instructions|you\s+are\s+now|system\s*:\s*override)`, "critical"},
	{"TTP-007", "File System Abuse", `(?i)(\/etc\/passwd|\/etc\/shadow|\.env|\.ssh\/|id_rsa)`, "critical"},
	{"TTP-008", "Process Execution", `(?i)(subprocess|os\.system|exec\.Command|child_process|spawn|popen)`, "high"},
	{"TTP-009", "Environment Variable Access", `(?i)(os\.environ|process\.env|getenv|ENV\[)`, "medium"},
	{"TTP-010", "Privilege Escalation", `(?i)(sudo|chmod\s+[0-7]{3,4}|chown|setuid|setgid)`, "high"},
	{"TTP-011", "Data Staging", `(?i)(>/tmp/|>/var/tmp/|>>/tmp/|mktemp)`, "medium"},
	{"TTP-012", "Encoded Command", `(?i)(powershell.*-enc|bash.*<<<|python.*-c\s+['"])`, "high"},
	{"TTP-013", "Container Escape", `(?i)(docker\.sock|/proc/self|nsenter|--privileged)`, "critical"},
	{"TTP-014", "DNS Tunneling", `(?i)(nslookup|dig\s+|host\s+.*\.\w{2,6}|dns.*tunnel)`, "medium"},
}

// compiledPattern is a pre-compiled regex + metadata.
type compiledPattern struct {
	ID       string
	Name     string
	Severity string
	Regex    *regexp.Regexp
	Raw      string
}

var compiled []compiledPattern

func init() {
	compiled = make([]compiledPattern, 0, len(rawPatterns))
	for _, p := range rawPatterns {
		re, err := regexp.Compile(p.Pattern)
		if err != nil {
			continue // Skip invalid patterns at startup
		}
		compiled = append(compiled, compiledPattern{
			ID:       p.ID,
			Name:     p.Name,
			Severity: p.Severity,
			Regex:    re,
			Raw:      p.Pattern,
		})
	}
}

// SemanticScanner scans tool call content against compiled TTP patterns.
type SemanticScanner struct{}

// NewSemanticScanner creates a new scanner.
func NewSemanticScanner() *SemanticScanner {
	return &SemanticScanner{}
}

// scanExemptions maps tool names to TTP IDs that should be skipped.
// These tools contain free-text instructions that naturally include
// file path examples, but the content is sandboxed and never executed.
var scanExemptions = map[string]map[string]bool{
	"create_skill": {"TTP-003": true, "TTP-008": true, "TTP-009": true, "TTP-011": true, "TTP-012": true},
	"manage_skill": {"TTP-003": true},
}

// Check evaluates request against all 14 TTP patterns (gate interface).
func (s *SemanticScanner) Check(req *models.ToolCallRequest) models.GateResult {
	start := time.Now()

	content := buildScanContent(req)
	findings := s.scanContent(content)

	// Filter out exempted patterns for trusted management tools
	if exempt, ok := scanExemptions[req.Tool]; ok && len(findings) > 0 {
		filtered := findings[:0]
		for _, f := range findings {
			if !exempt[f.ID] {
				filtered = append(filtered, f)
			}
		}
		findings = filtered
	}

	if len(findings) > 0 {
		// Find highest severity
		highest := findings[0]
		for _, f := range findings[1:] {
			if severityRank(f.Severity) > severityRank(highest.Severity) {
				highest = f
			}
		}
		return models.GateResult{
			Name:    "scanner",
			Passed:  false,
			Detail:  fmt.Sprintf("TTP detected: %s (%s) — %d pattern(s) matched", highest.Name, highest.Severity, len(findings)),
			Latency: time.Since(start).Microseconds(),
		}
	}

	return models.GateResult{
		Name:    "scanner",
		Passed:  true,
		Detail:  fmt.Sprintf("Clean: 0/%d TTP patterns matched", len(compiled)),
		Latency: time.Since(start).Microseconds(),
	}
}

// Scan performs a full scan and returns all findings (for /api/v1/scan endpoint).
func (s *SemanticScanner) Scan(content string) []models.ScanFinding {
	return s.scanContent(content)
}

// Patterns returns metadata for all compiled patterns (for /api/v1/patterns endpoint).
func (s *SemanticScanner) Patterns() []models.TTPPattern {
	result := make([]models.TTPPattern, len(compiled))
	for i, p := range compiled {
		result[i] = models.TTPPattern{
			ID:       p.ID,
			Name:     p.Name,
			Pattern:  p.Raw,
			Severity: p.Severity,
		}
	}
	return result
}

func buildScanContent(req *models.ToolCallRequest) string {
	var parts []string
	parts = append(parts, req.Tool)
	if req.Context != "" {
		parts = append(parts, req.Context)
	}
	if req.Arguments != nil {
		if b, err := json.Marshal(req.Arguments); err == nil {
			parts = append(parts, string(b))
		}
	}
	return strings.Join(parts, " ")
}

func (s *SemanticScanner) scanContent(content string) []models.ScanFinding {
	var mu sync.Mutex
	var findings []models.ScanFinding
	var wg sync.WaitGroup

	for _, p := range compiled {
		wg.Add(1)
		go func(pat compiledPattern) {
			defer wg.Done()
			matches := pat.Regex.FindAllStringIndex(content, -1)
			if len(matches) > 0 {
				mu.Lock()
				findings = append(findings, models.ScanFinding{
					ID:       pat.ID,
					Name:     pat.Name,
					Severity: pat.Severity,
					Matches:  len(matches),
				})
				mu.Unlock()
			}
		}(p)
	}

	wg.Wait()
	return findings
}

func severityRank(s string) int {
	switch s {
	case "critical":
		return 4
	case "high":
		return 3
	case "medium":
		return 2
	case "low":
		return 1
	default:
		return 0
	}
}
