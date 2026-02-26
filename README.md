```
 ██████╗██╗      █████╗ ██╗    ██╗██████╗  ██████╗████████╗██╗  ██╗
██╔════╝██║     ██╔══██╗██║    ██║██╔══██╗██╔════╝╚══██╔══╝╚██╗██╔╝
██║     ██║     ███████║██║ █╗ ██║██║  ██║██║        ██║    ╚███╔╝
██║     ██║     ██╔══██║██║███╗██║██║  ██║██║        ██║    ██╔██╗
╚██████╗███████╗██║  ██║╚███╔███╔╝██████╔╝╚██████╗   ██║   ██╔╝ ██╗
 ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═════╝  ╚═════╝   ╚═╝   ╚═╝  ╚═╝
                       ═══ O S  v3.0 ═══
       6 layers  ·  0 trust  ·  context window = RAM
```

# ClawdContext OS — Starter Stack

> **Clone it. Scan it. Break it. Harden it. Ship.**

The reference agent workspace with ClawdContext OS governance baked in. A day-zero setup for securing autonomous AI agents — scan it, red-team it, build on it.

```bash
git clone https://github.com/openclawos/starter-stack.git
cd starter-stack
make setup    # Generate signing keys + pull containers
make scan     # Scan all skills — the _malicious one gets flagged
make cer      # Calculate your CER — target: > 0.6
make red-team # Run attack sims — see what gets caught
```

---

## Why This Exists

OpenClaw hit 175K stars then got shredded — 5 CVEs in one week, 900+ malicious skills on ClawHub (~20% of the registry), 135K exposed instances with no auth, a Meta director's inbox wiped by a rogue agent. Kaspersky called it "the biggest insider threat of 2026."

**The root cause:** no OS-level governance for agents with filesystem access, shell exec, and persistent memory.

50 years of OS security research already solved this for traditional computing. We're applying all of it to agents.

---

## The Thesis

**Context window = RAM → treat it like an OS.**

```
Traditional OS            Agent OS (ClawdContext)
───────────────────────────────────────────────
RAM                    →  Context Window
Boot config (/etc)     →  CLAUDE.md
System calls (libc)    →  SKILL.md files
Cache + GC             →  lessons.md (TTL + eviction)
Task state (PCB)       →  todo.md
Reference Monitor      →  AgentProxy
SELinux / AppArmor     →  Policy Engine (OPA/Rego)
auditd / SIEM          →  FlightRecorder
btrfs snapshots        →  SnapshotEngine rollback
```

### CER — Context Efficiency Ratio

The one metric that matters: `useful work ÷ total context`.

```
CER: 0.0 ─── 0.2 ─── 0.4 ─── 0.6 ─── 0.8 ─── 1.0
      ☠️       ⚠️       🔶       ✅       🔥
   thermal  bloated  workable  healthy   elite
```

Most production agents sit at 0.2–0.4. Stale context isn't just waste — it's a security vulnerability.

---

## What's in the Box

```
starter-stack/
│
├── agent/                        ← Your agent's "filesystem"
│   ├── CLAUDE.md                    Kernel boot config (~180 tokens, minimal)
│   ├── AGENTS.md                    Multi-agent routing table
│   ├── skills/
│   │   ├── web-search/SKILL.md      Example skill (safe, signed)
│   │   ├── file-writer/SKILL.md     Example skill (sandboxed)
│   │   └── _malicious/SKILL.md      ⚠️ Intentionally poisoned (for testing)
│   ├── lessons.md                   Governed cache (TTL on every entry)
│   └── todo.md                      Task state template
│
├── security/                     ← Layer configs
│   ├── scanner.config.yaml          14 pattern categories + thresholds
│   ├── policies/
│   │   ├── capabilities.rego        OPA: per-skill capability grants
│   │   ├── rate-limits.rego         OPA: token budgets per skill per session
│   │   └── flow-control.rego        OPA: Bell-LaPadula info flow control
│   ├── sandbox/
│   │   ├── docker-compose.yaml      Agent sandbox (net:none, ro-root, uid 1000)
│   │   ├── seccomp-strict.json      Syscall filter (block mount, ptrace, raw_net)
│   │   └── network-policy.yaml      Per-skill egress allowlists
│   └── signing/
│       ├── keygen.sh                Generate Ed25519 keypair
│       └── verify.sh               Validate skill signatures
│
├── observability/                ← Monitoring stack
│   ├── docker-compose.obs.yaml      Prometheus + Grafana + Loki
│   ├── dashboards/
│   │   ├── cer-dashboard.json       CER gauge + history + per-skill breakdown
│   │   ├── security-score.json      6-layer status + threat events
│   │   └── cost-attribution.json    Token spend per skill per session
│   └── alerts/
│       ├── anomaly-rules.yaml       Behavioral deviation triggers
│       └── incident-rules.yaml      Critical event escalation
│
├── tools/                        ← CLI utilities
│   ├── ccos-scan                    Scan any SKILL.md / CLAUDE.md (standalone)
│   ├── ccos-cer                     Calculate CER from agent directory
│   ├── ccos-sign                    Sign a skill bundle with Ed25519
│   └── ccos-audit                   Query the immutable audit chain
│
├── red-team/                     ← Attack simulations
│   ├── test-injection.sh            Prompt injection via fake skill
│   ├── test-exfil.sh               Data exfiltration attempt
│   ├── test-escalation.sh          Privilege escalation via skill chaining
│   └── expected-results.md         What each layer should catch
│
├── docker-compose.yaml           ← Full stack: agent + sandbox + proxy + recorder
├── Makefile                      ← make scan / make cer / make sign / make red-team
└── README.md                     ← You are here
```

---

## Three Planes

### 🧠 Memory Plane (Context = RAM)

CER budgets · demand-loading · placement-aware ordering · TTL/eviction · rot + contradiction detection.

Your `agent/CLAUDE.md` is the kernel boot config (~180 tokens). Skills are demand-paged — loaded only when needed, evicted when done. `lessons.md` is a governed cache with TTL on every entry. Stale lessons are liabilities, not assets.

### 🛡️ Runtime Plane (Execution = OS)

AgentProxy (reference monitor) · OPA policy engine · Docker sandbox · Semantic Firewall · HITL gates · FlightRecorder · SnapshotEngine.

Every tool call from the LLM traverses the full enforcement chain: `LLM → AgentProxy → Rate Limiter → Semantic Firewall → HITL Gate → Sandbox → FlightLog → Snapshot`. The `security/` directory contains real, functional policies.

### 🔬 Feedback Plane (Hardening)

Incidents / post-mortems · threat model deltas · OpenClaw case studies · red team results · regression tests.

The `red-team/` directory has real attack simulations based on ClawHavoc campaign patterns. Run them, find bypasses, write new ones.

---

## 6-Layer Security Architecture

| Layer | Name | Tool | Status | OS Parallel |
|---|---|---|---|---|
| 1 | **Design Time** | ClawdContext Scanner | ✅ BUILT | `gcc -Wall` + `shellcheck` |
| 2 | **Supply Chain** | ClawdSign | 🔨 NOW | `apt-secure` + GPG + SBOM |
| 3 | **Deploy Time** | Docker Sandbox | ⚠️ PARTIAL | Secure Boot + SELinux |
| 4 | **Runtime** | AgentProxy | 🎯 NEXT | Anderson Report 1972 |
| 5 | **Observability** | FlightRecorder | 🎯 NEXT | `auditd` + SIEM + Falco |
| 6 | **Recovery** | SnapshotEngine | ⬡ FUTURE | btrfs/ZFS + IR playbooks |

---

## Quickstart

### Prerequisites

- Docker + Docker Compose
- `openssl` (for Ed25519 key generation)
- Bash 4+ / Zsh

### First 3 Commands

```bash
# 1. Scan all skills — the _malicious one should get flagged
make scan

# 2. Calculate your CER — target: > 0.6
make cer

# 3. Run attack simulations — see what gets caught, what doesn't
make red-team
```

### Use With Your Own Agent

```bash
# Point ccos-cer at your own agent directory
./tools/ccos-cer --dir /path/to/your/agent

# Scan a specific file
./tools/ccos-scan /path/to/your/SKILL.md

# Sign a skill bundle
./tools/ccos-sign --key security/signing/private.pem agent/skills/web-search/
```

---

## The Poisoned Skill

`agent/skills/_malicious/SKILL.md` is intentionally poisoned with real patterns from the ClawHavoc campaign:

- **Data exfiltration** — curl to external endpoint with env vars
- **Obfuscated eval** — base64-encoded shell commands
- **Credential harvesting** — reads ~/.ssh, ~/.aws, .env files
- **Prompt injection** — instruction override via "system" prefix

**Your first job:** make sure every layer catches it.
**Your second job:** write a new attack that bypasses something.

---

## Pick Your Lane

**🟢 Operators** — Clone the stack, run `make cer` on your own agent dir, [post your numbers](https://reddit.com/r/clawdcontext).

**🔵 Systems builders** — Phase 2-3 components need implementers. AgentProxy (Rust), ClawdSign (Go), FlightRecorder (Go). Open an issue or RFC.

**🟠 Red team** — Run `make red-team`, then write attacks that bypass it. Submit a PR.

**🟣 Policy designers** — Write real OPA/Rego policies in `security/policies/`. "Skill X can read /workspace but not /credentials, max 5 API calls/min."

**🔴 Incident survivors** — Had an agent go wrong? Write the post-mortem in an issue.

---

## Roadmap

```
PHASE 1 ✅ SHIPPED        PHASE 2 🔨 NOW           PHASE 3 🎯 NEXT          PHASE 4 ⬡ FUTURE
──────────────────       ──────────────────       ──────────────────       ──────────────────
VS Code Extension        ClawdSign (Ed25519       AgentProxy               Full Platform
  14-pattern scanner       skill signing)           Reference Monitor        ClawdContext OS
  AI validator (mdcc)    Skill lockfile             OPA policy engine        Web dashboard
  CER dashboard            (skills.lock)           Semantic Firewall        Multi-agent mesh
  Positional analysis    VirusTotal integration    HITL approval gates      Enterprise SSO
  Contradiction detect   SBOM generation           Rate limiting            Managed cloud
  Quick-fix actions      Reputation scoring        FlightRecorder
                                                   SnapshotEngine
```

---

## Related Projects

- **[ClawdContext VS Code Extension](https://marketplace.visualstudio.com/items?itemName=yaamwebsolutions.clawdcontext)** — Layer 1 scanner (14 patterns, AI validator, CER dashboard). Install it now.
- **[ClawdContext Website](https://clawdcontext.com)** — Platform docs, OS architecture, Eureka research.
- **[r/clawdcontext](https://reddit.com/r/clawdcontext)** — Community: post your CER numbers, policies, red-team reports.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Good contributions:** CER diffs · OPA snippets · sandbox configs · incident post-mortems · architecture RFCs · tool PRs · red team reports.

**Flair tags:** `[CER]` `[POLICY]` `[SANDBOX]` `[INCIDENT]` `[ARCH]` `[TOOL]` `[RED-TEAM]`

---

## License

MIT — see [LICENSE](LICENSE).

---

**Whoever builds the OS layer between agents and the world owns the next platform shift.**

```
git clone https://github.com/openclawos/starter-stack.git
```
