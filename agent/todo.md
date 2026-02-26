# todo.md — Process Control Block

> Task state for the current agent session.
> Only ONE task should be `in-progress` at a time.
> Each task needs explicit `done-criteria` before it can be marked `completed`.

## Format

```markdown
- [ ] Task description
  - **status**: not-started | in-progress | completed | blocked
  - **agent**: planner | builder | scanner | reviewer
  - **done-criteria**: specific, measurable condition
  - **evidence**: link or proof of completion
```

## Current Tasks

- [ ] Verify skill signatures in `agent/skills/`
  - **status**: not-started
  - **agent**: scanner
  - **done-criteria**: All skills have valid Ed25519 signatures or are flagged as unsigned
  - **evidence**: (pending)

- [ ] Scan `_malicious/SKILL.md` for TTP patterns
  - **status**: not-started
  - **agent**: scanner
  - **done-criteria**: Scanner detects ≥ 8 of 10 embedded patterns (see expected results table)
  - **evidence**: (pending)

- [ ] Calculate workspace CER
  - **status**: not-started
  - **agent**: scanner
  - **done-criteria**: CER metric computed, reported > 0.6 or remediation plan created
  - **evidence**: (pending)

- [ ] Review security policies for completeness
  - **status**: not-started
  - **agent**: reviewer
  - **done-criteria**: All 3 OPA policies (capabilities, rate-limits, flow-control) pass `opa test`
  - **evidence**: (pending)

## Constraints

- Max 10 active tasks (prevents Kessler Syndrome)
- Tasks older than 7 days without progress → auto-escalate to Planner
- Blocked tasks must specify blocker and resolution path

## History

(Completed tasks move here with timestamp and evidence)
