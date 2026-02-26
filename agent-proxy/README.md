# AgentProxy — Layer 4 Reference Monitor

> **Status:** 🚧 Work In Progress  
> **Branch:** `feat/agentproxy-layer4`  
> **Layer:** 4 — Runtime  
> **OS Parallel:** Anderson Report 1972 + Bell-LaPadula + RBAC

## What Is This?

AgentProxy is the **keystone** of ClawdContext OS. It sits between the LLM and every tool call, enforcing security policy in real time.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   LLM       │────▶│  AgentProxy  │────▶│   Tools     │
│  (Claude,   │     │  (Layer 4)   │     │  (file,     │
│   GPT, etc) │     │              │     │   network,  │
│             │◀────│  ALLOW/DENY  │◀────│   exec)     │
└─────────────┘     └──────────────┘     └─────────────┘
                          │
                    ┌─────▼─────┐
                    │  OPA/Rego │
                    │  Policies │
                    └───────────┘
```

## Architecture

### Core Components

```
agent-proxy/
├── proxy.py              # Main proxy server (FastAPI)
├── policy_engine.py      # OPA policy evaluation
├── capability_checker.py # Per-skill capability grants
├── flow_control.py       # Bell-LaPadula information flow
├── rate_limiter.py       # Token budget + rate limiting
├── audit_logger.py       # Append-only audit chain
├── semantic_firewall.py  # Second LLM validates intent
├── config.yaml           # Proxy configuration
└── tests/
    ├── test_capability.py
    ├── test_flow_control.py
    └── test_rate_limiter.py
```

### Request Flow

```
1. LLM emits tool_call(skill="web-search", action="fetch", target="https://example.com")
2. AgentProxy intercepts
3. Capability Check: Does web-search have net:example.com? → CHECK policies/capabilities.rego
4. Rate Limit Check: Is web-search under 10 req/min? → CHECK policies/rate-limits.rego
5. Flow Control: Can web-search read this data? → CHECK policies/flow-control.rego
6. Semantic Firewall: Does this look like exfiltration? → OPTIONAL second LLM
7. Human Gate: Is this a destructive action? → OPTIONAL approval
8. ALLOW or DENY → Log to audit chain
```

### Policy Integration

AgentProxy consumes the OPA/Rego policies already in the starter stack:

| Policy | What It Enforces |
|--------|-----------------|
| `capabilities.rego` | Per-skill capability grants (file, network, exec) |
| `rate-limits.rego` | Token budgets, request rate limits, CER thresholds |
| `flow-control.rego` | Bell-LaPadula: no read up, no write down |

### Human-in-the-Loop Gates

Certain actions require explicit human approval:

```yaml
human_gates:
  - action: file_delete
    scope: "**/*"
  - action: exec
    scope: "**/*"
  - action: network
    scope: "!allowlisted_hosts"
  - action: credential_access
    scope: "**/*"
```

## API Design

```python
# POST /proxy/tool_call
{
  "skill": "web-search",
  "action": "fetch",
  "target": "https://duckduckgo.com/search?q=test",
  "context": {
    "cer": 0.72,
    "security_score": 85,
    "session_id": "sess_abc123"
  }
}

# Response
{
  "decision": "ALLOW",
  "policy_results": {
    "capability": "PASS",
    "rate_limit": "PASS",
    "flow_control": "PASS"
  },
  "audit_id": "audit_xyz789",
  "latency_ms": 12
}
```

## Getting Started

```bash
# This is a WIP — not functional yet
# See the design doc above for the architecture

# When ready:
cd agent-proxy
pip install -r requirements.txt
python proxy.py --config config.yaml
```

## Contributing

This is the most impactful component to work on. If you're interested:

1. Check the [AgentProxy issues](https://github.com/ClawdContextOS/starter-stack/labels/layer-4)
2. Read the OPA policies in `security/policies/`
3. Start with `capability_checker.py` — the simplest component
4. See `CONTRIBUTING.md` for the full workflow

## References

- [Anderson Report (1972)](https://csrc.nist.gov/publications/detail/sp/500-19/final) — Reference monitor concept
- [Bell-LaPadula Model](https://en.wikipedia.org/wiki/Bell%E2%80%93LaPadula_model) — Information flow control
- [OPA Documentation](https://www.openpolicyagent.org/docs/latest/) — Policy engine
- [ClawdContext OS Architecture](https://clawdcontext.com/en/os) — Full platform vision
