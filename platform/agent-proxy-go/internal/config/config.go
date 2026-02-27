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

	// Notifications — ntfy
	NtfyURL   string
	NtfyTopic string
	NtfyToken string

	// Notifications — Discord
	DiscordWebhook string

	// Notifications — Telegram
	TelegramBotToken string
	TelegramChatID   string

	// Notifications — Slack
	SlackWebhook string

	// Notifications — Matrix
	MatrixHomeserver  string
	MatrixRoomID      string
	MatrixAccessToken string

	// HITL
	HITLTimeoutSec int
	ProxyPublicURL string // for HITL callback URLs
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

		// Notifications
		NtfyURL:   envOr("CCOS_NTFY_URL", ""),
		NtfyTopic: envOr("CCOS_NTFY_TOPIC", ""),
		NtfyToken: envOr("CCOS_NTFY_TOKEN", ""),

		DiscordWebhook: envOr("CCOS_DISCORD_WEBHOOK", ""),

		TelegramBotToken: envOr("CCOS_TELEGRAM_BOT_TOKEN", ""),
		TelegramChatID:   envOr("CCOS_TELEGRAM_CHAT_ID", ""),

		SlackWebhook: envOr("CCOS_SLACK_WEBHOOK", ""),

		MatrixHomeserver:  envOr("CCOS_MATRIX_HOMESERVER", ""),
		MatrixRoomID:      envOr("CCOS_MATRIX_ROOM_ID", ""),
		MatrixAccessToken: envOr("CCOS_MATRIX_ACCESS_TOKEN", ""),

		HITLTimeoutSec: envInt("CCOS_HITL_TIMEOUT", 300),
		ProxyPublicURL: envOr("CCOS_PROXY_PUBLIC_URL", "http://localhost:8400"),
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
