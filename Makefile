# ClawdContext OS — Starter Stack Makefile
# Run `make help` for available targets

.PHONY: help setup scan cer sign check red-team audit up down logs clean

# Default target
help: ## Show this help
	@echo ""
	@echo "╔══════════════════════════════════════════╗"
	@echo "║   ClawdContext OS — Starter Stack        ║"
	@echo "║   make <target>                          ║"
	@echo "╚══════════════════════════════════════════╝"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

# === Setup ===

setup: ## Initial setup: make scripts executable, generate keys
	@echo "Setting up ClawdContext OS Starter Stack..."
	@chmod +x tools/ccos-* security/signing/*.sh red-team/*.sh 2>/dev/null || true
	@echo "✓ Scripts made executable"
	@bash security/signing/keygen.sh security/signing/keys
	@echo ""
	@echo "Setup complete! Try:"
	@echo "  make scan      — Scan for security issues"
	@echo "  make cer       — Calculate CER"
	@echo "  make red-team  — Run attack simulations"

# === Security Scanning (Layer 1) ===

scan: ## Scan agent/ for TTP patterns (Layer 1)
	@bash tools/ccos-scan agent

scan-all: ## Scan entire workspace for TTP patterns
	@bash tools/ccos-scan .

scan-json: ## Scan with JSON output
	@bash tools/ccos-scan agent --format json

# === CER Analysis (Eureka #3) ===

cer: ## Calculate Context Efficiency Ratio
	@bash tools/ccos-cer agent

cer-whatif: ## Simulate CER with 5 skills loaded
	@bash tools/ccos-cer agent --what-if 5

# === Signing (Layer 2) ===

sign-init: ## Generate Ed25519 keypair
	@bash tools/ccos-sign init

sign: ## Sign all skills (except _malicious)
	@bash tools/ccos-sign sign

check: ## Verify all skill signatures
	@bash tools/ccos-sign check

# === Red-Team (Attack Simulation) ===

red-team: ## Run all red-team tests
	@echo "Running red-team test suite..."
	@echo ""
	@bash red-team/test-injection.sh || true
	@echo ""
	@bash red-team/test-exfil.sh || true
	@echo ""
	@bash red-team/test-escalation.sh || true
	@echo ""
	@echo "Red-team suite complete."

red-team-injection: ## Run prompt injection tests only
	@bash red-team/test-injection.sh

red-team-exfil: ## Run data exfiltration tests only
	@bash red-team/test-exfil.sh

red-team-escalation: ## Run privilege escalation tests only
	@bash red-team/test-escalation.sh

# === Audit (Layer 5) ===

audit: ## Query audit log (last 50 entries)
	@bash tools/ccos-audit

audit-critical: ## Show only critical audit entries
	@bash tools/ccos-audit --severity critical

audit-verify: ## Verify audit chain integrity
	@bash tools/ccos-audit --verify

# === Docker Stack ===

up: ## Start full stack (sandbox + observability)
	@docker compose up -d
	@echo ""
	@echo "Stack running:"
	@echo "  Sandbox:    docker exec -it ccos-agent-sandbox bash"
	@echo "  Grafana:    http://localhost:3000 (admin/clawdcontext)"
	@echo "  Prometheus: http://localhost:9090"

down: ## Stop all containers
	@docker compose down

logs: ## Follow container logs
	@docker compose logs -f

status: ## Show container status
	@docker compose ps

# === Observability Only ===

obs-up: ## Start observability stack only (Prometheus + Grafana + Loki)
	@docker compose -f observability/docker-compose.obs.yaml up -d
	@echo "Grafana: http://localhost:3000 (admin/clawdcontext)"

obs-down: ## Stop observability stack
	@docker compose -f observability/docker-compose.obs.yaml down

# === Cleanup ===

clean: ## Remove generated files and containers
	@docker compose down -v 2>/dev/null || true
	@docker compose -f observability/docker-compose.obs.yaml down -v 2>/dev/null || true
	@rm -rf security/signing/keys/
	@echo "✓ Cleaned up"

# === Full Pipeline ===

all: setup scan cer check ## Run full pipeline: setup → scan → cer → check
	@echo ""
	@echo "╔══════════════════════════════════════════╗"
	@echo "║   Full pipeline complete!                ║"
	@echo "║   Next: make red-team                    ║"
	@echo "╚══════════════════════════════════════════╝"
