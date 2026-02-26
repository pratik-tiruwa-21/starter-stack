---
skill: file-writer
version: 1.0.0
author: clawdcontext-os
capabilities:
  - file_read:/workspace/**
  - file_write:/workspace/output/**
  - file_write:/workspace/agent/todo.md
  - file_write:/workspace/agent/lessons.md
rate_limit: 50/minute
token_budget: 4000
signature: "UNSIGNED — run `make sign` to generate"
signed_by: ""
signed_at: ""
---

# SKILL.md — File Writer

## Purpose

Create and modify files within the sandboxed workspace.
All writes are restricted to `/workspace/output/` and governance files.

## When to Load

- Builder agent needs to create implementation files
- Any agent needs to update todo.md or lessons.md
- User explicitly asks to "write", "create", or "save" a file

## Instructions

1. Validate target path is within capability grants:
   - `/workspace/output/**` — any new files
   - `/workspace/agent/todo.md` — task state updates
   - `/workspace/agent/lessons.md` — governed cache updates
2. Check file doesn't already exist (create) or confirm overwrite intent (modify)
3. Write file with appropriate encoding (UTF-8)
4. Report created/modified path and byte count

## Constraints

- **NEVER** write outside `/workspace/output/` or listed governance files
- **NEVER** write to `/workspace/agent/CLAUDE.md` (kernel is immutable)
- **NEVER** write executable files (no `.sh`, `.py`, `.js` with exec permissions)
- **NEVER** write files larger than 100KB
- **NEVER** write binary files (images, compiled code, archives)
- **NEVER** overwrite without explicit user confirmation

## Sandboxing

This skill operates within the Docker sandbox:
- Filesystem: read-only root, writable `/workspace/output/` volume
- User: UID 1000 (non-root)
- No network access (net: not granted)
- No process execution (exec: not granted)
- Seccomp profile: `security/sandbox/seccomp-strict.json`

## Audit Trail

Every file write is logged:
```
[ISO-8601] FILE_WRITE path=/workspace/output/foo.md size=1234 agent=builder skill=file-writer
```

Logs are immutable and shipped to FlightRecorder (Layer 5).
