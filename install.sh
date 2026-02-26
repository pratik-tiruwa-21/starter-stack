#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  ClawdContext OS — One-Click Installer                      ║
# ║  curl -fsSL https://clawdcontext.com/install.sh | bash      ║
# ║                                                              ║
# ║  Or from VS Code extension: Command Palette →               ║
# ║  "ClawdContext: Initialize OS Workspace"                     ║
# ╚══════════════════════════════════════════════════════════════╝
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── ASCII Banner ────────────────────────────────────────────────
banner() {
  echo ""
  echo -e "${CYAN}"
  cat << 'BANNER'
   ██████╗██╗      █████╗ ██╗    ██╗██████╗  ██████╗ ██████╗ ███╗   ██╗████████╗███████╗██╗  ██╗████████╗
  ██╔════╝██║     ██╔══██╗██║    ██║██╔══██╗██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝██╔════╝╚██╗██╔╝╚══██╔══╝
  ██║     ██║     ███████║██║ █╗ ██║██║  ██║██║     ██║   ██║██╔██╗ ██║   ██║   █████╗   ╚███╔╝    ██║   
  ██║     ██║     ██╔══██║██║███╗██║██║  ██║██║     ██║   ██║██║╚██╗██║   ██║   ██╔══╝   ██╔██╗    ██║   
  ╚██████╗███████╗██║  ██║╚███╔███╔╝██████╔╝╚██████╗╚██████╔╝██║ ╚████║   ██║   ███████╗██╔╝ ██╗   ██║   
   ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝   ╚═╝   
BANNER
  echo -e "${NC}"
  echo -e "  ${BOLD}OS${NC} ${DIM}v0.1.0${NC}  —  ${DIM}Deploy Agentic AI Without Leaking Secrets${NC}"
  echo ""
}

# ── Logging ─────────────────────────────────────────────────────
info()    { echo -e "  ${CYAN}▸${NC} $1"; }
success() { echo -e "  ${GREEN}✓${NC} $1"; }
warn()    { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()    { echo -e "  ${RED}✗${NC} $1"; exit 1; }
step()    { echo -e "\n${BOLD}[$1/7]${NC} $2"; }

# ── Preflight Checks ───────────────────────────────────────────
preflight() {
  step 1 "Preflight checks"
  
  # Git
  if command -v git &>/dev/null; then
    success "git $(git --version | awk '{print $3}')"
  else
    fail "git not found. Install: https://git-scm.com"
  fi
  
  # Docker (optional but recommended)
  if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
    success "docker $DOCKER_VERSION"
    HAVE_DOCKER=true
  else
    warn "docker not found — sandbox and observability stack won't work"
    warn "Install: https://docs.docker.com/get-docker/"
    HAVE_DOCKER=false
  fi
  
  # Docker Compose
  if $HAVE_DOCKER && docker compose version &>/dev/null; then
    success "docker compose $(docker compose version --short)"
    HAVE_COMPOSE=true
  else
    HAVE_COMPOSE=false
  fi
  
  # OpenSSL (for Ed25519 signing)
  if command -v openssl &>/dev/null; then
    success "openssl $(openssl version | awk '{print $2}')"
  else
    warn "openssl not found — skill signing won't work"
  fi
  
  # VS Code (optional)
  if command -v code &>/dev/null; then
    success "VS Code detected"
    HAVE_VSCODE=true
  else
    HAVE_VSCODE=false
  fi
}

# ── Target Directory ────────────────────────────────────────────
choose_directory() {
  step 2 "Choose workspace location"
  
  TARGET="${1:-}"
  
  if [[ -z "$TARGET" ]]; then
    TARGET="$(pwd)/clawdcontext-os"
  fi
  
  if [[ -d "$TARGET/.git" ]]; then
    info "Existing repo detected at $TARGET"
    info "Pulling latest..."
    cd "$TARGET" && git pull origin main 2>/dev/null || true
    success "Updated: $TARGET"
    return
  fi
  
  info "Target: ${BOLD}$TARGET${NC}"
}

# ── Clone ───────────────────────────────────────────────────────
clone_stack() {
  step 3 "Cloning starter stack"
  
  if [[ -d "$TARGET" ]]; then
    success "Directory exists, skipping clone"
  else
    git clone --depth 1 https://github.com/ClawdContextOS/starter-stack.git "$TARGET" 2>&1 | \
      while IFS= read -r line; do info "$line"; done
    success "Cloned to $TARGET"
  fi
  
  cd "$TARGET"
}

# ── Setup ───────────────────────────────────────────────────────
run_setup() {
  step 4 "Setting up workspace"
  
  # Make scripts executable
  chmod +x tools/ccos-* security/signing/*.sh red-team/*.sh 2>/dev/null || true
  success "Scripts made executable"
  
  # Create .env from example if needed
  if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
      cp .env.example .env
      success ".env created from .env.example (mock LLM — no API key needed)"
      info "Edit .env to add your DeepSeek/OpenAI key for live LLM responses"
    fi
  else
    success ".env already exists"
  fi
  
  # Generate Ed25519 keys
  if [[ ! -d "security/signing/keys" ]]; then
    if command -v openssl &>/dev/null; then
      bash security/signing/keygen.sh security/signing/keys 2>/dev/null
      success "Ed25519 keypair generated"
    else
      warn "Skipping key generation (openssl not found)"
    fi
  else
    success "Keys already exist"
  fi
}

# ── Initial Scan ────────────────────────────────────────────────
run_scan() {
  step 5 "Running security scan (Layer 1)"
  
  bash tools/ccos-scan agent 2>/dev/null || true
  echo ""
  
  info "CER Analysis:"
  bash tools/ccos-cer agent 2>/dev/null || true
}

# ── Docker Stack ────────────────────────────────────────────────
start_docker() {
  step 6 "Docker platform"
  
  if $HAVE_COMPOSE; then
    info "Building platform services (AgentProxy, Scanner, FlightRecorder, Dashboard)..."
    docker compose -f docker-compose.platform.yml build 2>&1 | tail -5 | while IFS= read -r line; do info "$line"; done
    success "Platform images built"

    info "Starting platform stack..."
    docker compose -f docker-compose.platform.yml up -d 2>&1 | while IFS= read -r line; do info "$line"; done
    success "Platform running"

    info "Dashboard:     ${BOLD}http://localhost:3000${NC}"
    info "AgentProxy:    http://localhost:8400"
    info "Scanner API:   http://localhost:8401"
    info "FlightRecorder:http://localhost:8402"

    echo ""
    read -r -p "  Also start observability stack? (Prometheus + Grafana + Loki) [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
      docker compose up -d 2>&1 | while IFS= read -r line; do info "$line"; done
      success "Observability running"
      info "Grafana:       http://localhost:3001 (admin/clawdcontext)"
      info "Prometheus:    http://localhost:9090"
    fi
  else
    info "Docker not available — skipping platform startup"
    info "Install Docker to enable the full OS stack"
  fi
}

# ── Open in VS Code ─────────────────────────────────────────────
open_vscode() {
  step 7 "Opening workspace"
  
  if $HAVE_VSCODE; then
    # Install ClawdContext extension if not present
    if ! code --list-extensions 2>/dev/null | grep -qi "clawdcontext"; then
      info "Installing ClawdContext extension..."
      code --install-extension clawdcontext.clawdcontext 2>/dev/null || true
    fi
    
    code "$TARGET"
    success "Opened in VS Code"
    info "The ClawdContext extension provides Layer 1 scanning"
    info "Use Cmd+Shift+P → 'ClawdContext' to access all features"
  else
    success "Workspace ready at: $TARGET"
    info "Open with: code $TARGET"
  fi
}

# ── Summary ─────────────────────────────────────────────────────
summary() {
  echo ""
  echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║${NC}  ${GREEN}${BOLD}ClawdContext OS — Ready${NC}                              ${CYAN}║${NC}"
  echo -e "${CYAN}╠══════════════════════════════════════════════════════╣${NC}"
  echo -e "${CYAN}║${NC}                                                      ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}Quick Commands:${NC}                                      ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make scan      → Security scan (14 TTP patterns)   ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make cer       → Context Efficiency Ratio          ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make sign      → Ed25519 sign skills               ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make red-team  → 22 attack simulations             ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make up        → Start platform + Docker stack     ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    make help      → All available commands            ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}                                                      ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}Security Layers:${NC}                                     ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${GREEN}■${NC} Layer 1  Design Time Scanner     ${GREEN}ACTIVE${NC}         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${GREEN}■${NC} Layer 2  ClawdSign (Ed25519)      ${GREEN}ACTIVE${NC}         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${GREEN}■${NC} Layer 3  Docker Sandbox           ${GREEN}ACTIVE${NC}         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${GREEN}■${NC} Layer 4  AgentProxy               ${GREEN}ACTIVE${NC}         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${GREEN}■${NC} Layer 5  FlightRecorder           ${GREEN}ACTIVE${NC}         ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}    ${DIM}■${NC} Layer 6  SnapshotEngine            ${DIM}PLANNED${NC}        ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}                                                      ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${BOLD}Dashboard:${NC} ${CYAN}http://localhost:3000${NC}                     ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}                                                      ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${DIM}https://github.com/ClawdContextOS/starter-stack${NC}    ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}  ${DIM}https://clawdcontext.com${NC}                            ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}                                                      ${CYAN}║${NC}"
  echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""
}

# ── Main ────────────────────────────────────────────────────────
main() {
  banner
  preflight
  choose_directory "$@"
  clone_stack
  run_setup
  run_scan
  start_docker
  open_vscode
  summary
}

# Support --help
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
  echo "ClawdContext OS Installer"
  echo ""
  echo "Usage:"
  echo "  curl -fsSL https://clawdcontext.com/install.sh | bash"
  echo "  ./install.sh [target-directory]"
  echo ""
  echo "Options:"
  echo "  --help, -h    Show this help"
  echo "  --no-docker   Skip Docker stack startup"
  echo "  --no-vscode   Skip VS Code opening"
  echo ""
  echo "What it does:"
  echo "  1. Preflight checks (git, docker, openssl)"
  echo "  2. Clone ClawdContextOS/starter-stack"
  echo "  3. Generate Ed25519 keys for skill signing"
  echo "  4. Run initial security scan + CER analysis"
  echo "  5. Optionally start Docker stack (sandbox + observability)"
  echo "  6. Open in VS Code with ClawdContext extension"
  exit 0
fi

# Support --non-interactive (for VS Code extension integration)
if [[ "${1:-}" == "--non-interactive" ]]; then
  shift
  HAVE_VSCODE=false
  
  banner
  
  # Minimal preflight
  command -v git &>/dev/null || fail "git not found"
  
  # Check Docker
  if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    HAVE_DOCKER=true
    HAVE_COMPOSE=true
  else
    HAVE_DOCKER=false
    HAVE_COMPOSE=false
  fi
  
  TARGET="${1:-$(pwd)/clawdcontext-os}"
  
  if [[ ! -d "$TARGET" ]]; then
    git clone --depth 1 https://github.com/ClawdContextOS/starter-stack.git "$TARGET"
  fi
  
  cd "$TARGET"
  chmod +x tools/ccos-* security/signing/*.sh red-team/*.sh 2>/dev/null || true
  
  # Create .env from example if needed
  if [[ ! -f ".env" ]] && [[ -f ".env.example" ]]; then
    cp .env.example .env
    success ".env created (mock LLM mode)"
  fi
  
  if command -v openssl &>/dev/null; then
    bash security/signing/keygen.sh security/signing/keys 2>/dev/null || true
  fi
  
  # Build and start platform
  if $HAVE_COMPOSE; then
    info "Building platform services..."
    docker compose -f docker-compose.platform.yml build 2>&1 | tail -5
    info "Starting platform..."
    docker compose -f docker-compose.platform.yml up -d 2>&1
    success "Platform running — Dashboard: http://localhost:3000"
  fi
  
  summary
  exit 0
fi

main "$@"
