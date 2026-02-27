// Gate 3: Capability Checker — enforces per-skill capability grants.
// Parses SKILL.md YAML frontmatter for capability declarations.
package gates

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/ClawdContextOS/agent-proxy/internal/models"
)

// CapabilityChecker verifies that a skill has been granted the requested capability.
type CapabilityChecker struct {
	mu        sync.RWMutex
	skillsDir string
	skillCaps map[string][]string // skill name → list of capabilities
}

// NewCapabilityChecker creates a checker and loads all skills from disk.
func NewCapabilityChecker(skillsDir string) *CapabilityChecker {
	c := &CapabilityChecker{
		skillsDir: skillsDir,
		skillCaps: make(map[string][]string),
	}
	c.Reload()
	return c
}

// Check evaluates whether the skill has the required capability.
func (c *CapabilityChecker) Check(req *models.ToolCallRequest) models.GateResult {
	start := time.Now()

	// Block unsafe prefixes
	if strings.HasPrefix(req.Skill, "_") {
		return models.GateResult{
			Name:    "capability",
			Passed:  false,
			Detail:  fmt.Sprintf("Blocked: skill '%s' has unsafe prefix", req.Skill),
			Latency: time.Since(start).Microseconds(),
		}
	}

	c.mu.RLock()
	caps, exists := c.skillCaps[req.Skill]
	c.mu.RUnlock()

	if !exists {
		return models.GateResult{
			Name:    "capability",
			Passed:  false,
			Detail:  fmt.Sprintf("Unknown skill: '%s' not registered", req.Skill),
			Latency: time.Since(start).Microseconds(),
		}
	}

	// Check exact match
	for _, cap := range caps {
		if cap == req.Tool {
			return models.GateResult{
				Name:    "capability",
				Passed:  true,
				Detail:  fmt.Sprintf("Capability '%s' granted to '%s'", req.Tool, req.Skill),
				Latency: time.Since(start).Microseconds(),
			}
		}
	}

	// Check wildcard match (e.g., file_read:/workspace/** matches file_read:/workspace/agent/foo.txt)
	for _, cap := range caps {
		if matchWildcard(cap, req.Tool) {
			return models.GateResult{
				Name:    "capability",
				Passed:  true,
				Detail:  fmt.Sprintf("Wildcard capability match: %s", cap),
				Latency: time.Since(start).Microseconds(),
			}
		}
	}

	return models.GateResult{
		Name:    "capability",
		Passed:  false,
		Detail:  fmt.Sprintf("Capability '%s' not granted to skill '%s'", req.Tool, req.Skill),
		Latency: time.Since(start).Microseconds(),
	}
}

// matchWildcard checks if a capability pattern matches the requested tool.
// Pattern examples: "file_read:/workspace/**", "net:*"
func matchWildcard(cap, tool string) bool {
	if !strings.Contains(cap, ":") || !strings.Contains(tool, ":") {
		return false
	}
	capName, capScope := splitFirst(cap, ":")
	toolName, toolScope := splitFirst(tool, ":")

	if capName != toolName {
		return false
	}
	if !strings.HasSuffix(capScope, "*") {
		return false
	}
	prefix := strings.TrimRight(capScope, "*")
	return strings.HasPrefix(toolScope, prefix)
}

func splitFirst(s, sep string) (string, string) {
	idx := strings.Index(s, sep)
	if idx < 0 {
		return s, ""
	}
	return s[:idx], s[idx+1:]
}

// Reload re-reads all SKILL.md files from the skills directory.
func (c *CapabilityChecker) Reload() {
	newCaps := make(map[string][]string)

	entries, err := os.ReadDir(c.skillsDir)
	if err != nil {
		// Skills dir may not exist yet; that's OK
		c.mu.Lock()
		c.skillCaps = newCaps
		c.mu.Unlock()
		return
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		skillFile := filepath.Join(c.skillsDir, entry.Name(), "SKILL.md")
		caps := parseCapabilities(skillFile)
		if len(caps) > 0 {
			newCaps[entry.Name()] = caps
		}
	}

	c.mu.Lock()
	c.skillCaps = newCaps
	c.mu.Unlock()
}

// parseCapabilities extracts capability lines from SKILL.md YAML frontmatter.
func parseCapabilities(path string) []string {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()

	var caps []string
	inFrontmatter := false
	scanner := bufio.NewScanner(f)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "---" {
			if inFrontmatter {
				break // End of frontmatter
			}
			inFrontmatter = true
			continue
		}
		if inFrontmatter && strings.HasPrefix(line, "- ") {
			cap := strings.TrimPrefix(line, "- ")
			cap = strings.TrimSpace(cap)
			if cap != "" && !strings.HasPrefix(cap, "#") {
				caps = append(caps, cap)
			}
		}
	}

	return caps
}

// Skills returns a copy of all registered skills and their capabilities.
func (c *CapabilityChecker) Skills() map[string][]string {
	c.mu.RLock()
	defer c.mu.RUnlock()

	result := make(map[string][]string, len(c.skillCaps))
	for k, v := range c.skillCaps {
		cp := make([]string, len(v))
		copy(cp, v)
		result[k] = cp
	}
	return result
}
