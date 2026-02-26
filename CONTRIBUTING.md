# Contributing to ClawdContext OS Starter Stack

Thank you for your interest in making AI agents safer. This guide will help you get started.

## Quick Start

```bash
git clone https://github.com/openclawos/starter-stack.git
cd starter-stack
make setup
make scan
```

## Pick Your Lane

There are five ways to contribute — pick the one that fits:

### 1. Agent Builders — Write New Skills

Create new `SKILL.md` files following the governed pattern:

```
agent/skills/your-skill/SKILL.md
```

**Requirements:**
- YAML frontmatter with explicit `capabilities` (no wildcards)
- Token budget under 50,000 unless justified
- Rate limit specified
- Must pass `make scan` with zero CRITICAL findings
- Must be signable via `make sign`

### 2. Security Researchers — Harden the Stack

Improve detection patterns, policies, or sandbox configs:

- `security/scanner.config.yaml` — add new TTP patterns (CC-015+)
- `security/policies/*.rego` — strengthen OPA policy rules
- `security/sandbox/` — tighten container isolation
- `red-team/` — write new attack simulations

### 3. Platform Engineers — Build the Runtime

Work on the layers that don't exist yet:

| Layer | Component | Status |
|-------|-----------|--------|
| 2 | ClawdSign (Ed25519 skill verification) | Scripts exist, needs daemon |
| 4 | AgentProxy (reference monitor) | PLANNED |
| 5 | FlightRecorder (audit logging) | PLANNED |
| 6 | SnapshotEngine (rollback) | PLANNED |

### 4. Observability Engineers — Dashboards & Alerts

Improve the monitoring stack:

- `observability/dashboards/` — Grafana dashboard JSON
- `observability/alerts/` — Prometheus alert rules
- Metric exporters for CER, security scores, cost attribution

### 5. Documentation & Education

- Improve README, SKILL.md examples, inline comments
- Write tutorials and walkthroughs
- Translate to additional languages

## Development Workflow

### Branch Naming

```
feat/description      — New features
fix/description       — Bug fixes
security/description  — Security improvements
docs/description      — Documentation
red-team/description  — New attack simulations
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(scanner): add CC-015 WebSocket exfiltration pattern
fix(cer): correct token count for YAML frontmatter
security(sandbox): restrict /proc mount to read-only
docs(readme): add quickstart video link
red-team(exfil): add DNS tunneling test case
```

### Pull Request Checklist

Before submitting a PR, verify:

- [ ] `make scan` passes with zero CRITICAL findings
- [ ] `make cer` shows CER > 0.6 for any modified agent files
- [ ] `make check` verifies all skill signatures (except `_malicious`)
- [ ] `make red-team` — all tests that should detect attacks do detect them
- [ ] No secrets, keys, or credentials in committed files
- [ ] Changes documented in PR description

### Security-Sensitive Changes

For changes to security policies, scanner patterns, or sandbox configs:

1. Open the PR with `security/` prefix branch
2. Tag it with the `security` label
3. Include before/after scan results
4. Red-team tests must cover the new pattern
5. Two approvals required (one from a maintainer)

## Adding a New Scanner Pattern

1. Add the pattern regex to `security/scanner.config.yaml`:

```yaml
- id: CC-015
  name: your-pattern-name
  severity: CRITICAL|HIGH|MEDIUM|LOW
  pattern: 'your-regex-here'
  description: "What this pattern detects"
  reference: "Link to TTP documentation"
```

2. Add a test case to `agent/skills/_malicious/SKILL.md`:

```markdown
<!-- Attack: your-pattern-name -->
your malicious payload here
```

3. Update `red-team/expected-results.md` with expected detection

4. Run `make scan` and `make red-team` to verify

## Adding a Red-Team Test

1. Choose the appropriate test script:
   - `test-injection.sh` — prompt injection attacks
   - `test-exfil.sh` — data exfiltration attempts
   - `test-escalation.sh` — privilege escalation

2. Follow the existing pattern:

```bash
run_test "test-name" '
---
capabilities:
  - relevant:capability
---
# Your attack payload here
'
```

3. Update `expected-results.md`

4. Run `make red-team` to verify detection

## Code of Conduct

- Be respectful and constructive
- Security vulnerabilities should be reported privately (not in public issues)
- All contributions must be compatible with the MIT license
- No intentionally malicious code outside of `_malicious/` and `red-team/`

## Questions?

- Open a [Discussion](https://github.com/openclawos/starter-stack/discussions)
- File an [Issue](https://github.com/openclawos/starter-stack/issues)
- Read the [README](README.md) for architecture context

---

**Thank you for helping make AI agents safer.**
