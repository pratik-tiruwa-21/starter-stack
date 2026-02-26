# lessons.md — Adaptive Cache

> Governed memory with TTL. Max 20 entries.
> Older entries are evicted when limit is reached.
> Tags: `local` (this project), `global` (cross-project), `deprecated` (pending removal)

## Active Lessons

### 1. [global] Always verify skill signatures before loading
- **Added**: 2025-01-15
- **TTL**: permanent
- **Source**: ClawHavoc incident analysis — 26% of 31K community skills contained at least one TTP pattern
- **Rule**: Never load a SKILL.md without valid Ed25519 signature. Unsigned = untrusted.

### 2. [global] CER degrades faster than expected under multi-skill load
- **Added**: 2025-01-20
- **TTL**: 90 days
- **Source**: SkillsBench benchmarks (arXiv:2602.12670 §4.2)
- **Rule**: With 3+ skills loaded simultaneously, CER drops ~40%. Load one at a time, unload when done.

### 3. [global] Lost-in-the-Middle affects governance instructions
- **Added**: 2025-02-01
- **TTL**: 90 days
- **Source**: Stanford/NoLiMa research — LLMs ignore tokens in the middle 60% of context
- **Rule**: Place critical rules in first 500 tokens (CLAUDE.md) and last 200 tokens (active skill). Never in the middle.

### 4. [local] Forged signatures pass string-match validation
- **Added**: 2025-02-10
- **TTL**: 60 days
- **Source**: Red-team test (test-injection.sh round 3)
- **Rule**: Signature validation must use cryptographic verification (Ed25519), not string comparison. The `_malicious/SKILL.md` forged sig should fail crypto validation even though it "looks" valid.

### 5. [global] Docker socket mount = full host compromise
- **Added**: 2025-02-15
- **TTL**: permanent
- **Source**: CVE-2019-5736, container escape research
- **Rule**: Never mount `/var/run/docker.sock` into agent containers. Use rootless Docker or Podman.

---

## Eviction Policy

When entries exceed 20:
1. Remove all `deprecated` entries
2. Remove expired TTL entries (past TTL date)
3. Remove oldest `local` entries first
4. `global` + `permanent` entries are evicted last
5. If still over 20, force-evict oldest regardless of tag
