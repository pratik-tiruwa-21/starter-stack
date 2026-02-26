# AGENTS.md — Multi-Agent Routing Table

> Defines agent roles, responsibilities, and handoff protocols.
> The kernel (CLAUDE.md) loads this to understand the agent topology.

## Agent Roles

| Role | Trigger | Skills Loaded | Trust Level |
|------|---------|---------------|-------------|
| **Planner** | "plan", "break down", "design" | None (kernel only) | HIGH |
| **Builder** | "implement", "create", "build" | Task-specific skills | MEDIUM |
| **Scanner** | "scan", "check", "audit" | `security/*` | HIGH |
| **Reviewer** | "review", "validate" | Task-specific + security | HIGH |
| **Red-Team** | "attack", "test security" | `red-team/*` | SANDBOXED |

## Handoff Protocol

```
Planner → Builder → Scanner → Reviewer
   ↑                              │
   └──────── (if rejected) ───────┘
```

1. **Planner** breaks work into checkable steps in `todo.md`
2. **Builder** implements one step at a time, marks complete with evidence
3. **Scanner** runs security checks after each build step
4. **Reviewer** validates against CLAUDE.md invariants; rejects back to Planner if broken

## Subagent Isolation

Each agent role runs as an isolated "process":
- Separate context window (no cross-contamination)
- Capability grants scoped to role (Builder can't run security scans)
- All actions logged to FlightRecorder (Layer 5)
- Red-Team agent runs in SANDBOXED mode (no real network, no real credentials)

## Escalation Rules

| Condition | Action |
|-----------|--------|
| Builder attempts `net:*` without skill grant | → Block + alert Reviewer |
| Any agent modifies CLAUDE.md | → Kernel panic (halt all agents) |
| CER drops below 0.3 | → Force-unload all skills, restart with kernel only |
| Scanner finds TTP match | → Quarantine skill, notify Planner |
| Red-Team escapes sandbox | → Halt, snapshot state, escalate to human |

## Communication Protocol

Agents communicate via structured state files:
- `todo.md` — task state (Planner writes, Builder updates, Reviewer validates)
- `lessons.md` — shared cache (any agent writes, all agents read, TTL enforced)
- Security findings → `observability/alerts/` (Scanner writes, Reviewer reads)
