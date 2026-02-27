// Package config provides centralized configuration for the AgentProxy.
// All values are loaded from environment variables with sensible defaults.
package config

import (
	"os"
	"strconv"
)

// Config holds all AgentProxy configuration values.
type Config struct {
	// Server
	Port string

	// Paths
	SkillsDir string
	AuditFile string

	// Rate limiting
	MaxRateRPM int

	// CER thresholds
	CERWarn float64
	CERCrit float64
}

// Load reads configuration from environment variables with defaults.
func Load() *Config {
	return &Config{
		Port:       envOr("PORT", "8400"),
		SkillsDir:  envOr("CCOS_SKILLS_DIR", "/workspace/agent/skills"),
		AuditFile:  envOr("CCOS_AUDIT_FILE", "/data/audit.jsonl"),
		MaxRateRPM: envInt("CCOS_MAX_RATE_RPM", 60),
		CERWarn:    envFloat("CCOS_CER_WARN", 0.6),
		CERCrit:    envFloat("CCOS_CER_CRIT", 0.3),
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func envFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}
