## Description

<!-- What does this PR do? Link to related issue(s). -->

Closes #

## Type of Change

- [ ] 🐛 Bug fix (non-breaking change that fixes an issue)
- [ ] ✨ New feature (non-breaking change that adds functionality)
- [ ] 🔒 Security improvement (detection pattern, policy, sandbox hardening)
- [ ] 💥 Breaking change (fix or feature that would cause existing functionality to change)
- [ ] 📚 Documentation update
- [ ] 🧪 Red-team test (new attack simulation)

## Layer(s) Affected

- [ ] Layer 1 — Design Time (Scanner)
- [ ] Layer 2 — Supply Chain (ClawdSign)
- [ ] Layer 3 — Deploy Time (Sandbox)
- [ ] Layer 4 — Runtime (AgentProxy)
- [ ] Layer 5 — Observability (FlightRecorder)
- [ ] Layer 6 — Recovery (SnapshotEngine)

## Verification Checklist

- [ ] `make scan` — zero CRITICAL findings
- [ ] `make cer` — CER > 0.6 for modified agent files
- [ ] `make check` — all signatures valid (except `_malicious`)
- [ ] `make red-team` — attack simulations pass as expected
- [ ] No secrets, keys, or credentials in committed files
- [ ] Tests cover new patterns (if adding security rules)

## Scanner Results

```bash
# Paste output of: make scan
```

## CER Results

```bash
# Paste output of: make cer
```

## Screenshots / Logs

<!-- If applicable, add screenshots or log output -->
