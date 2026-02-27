---
skill: openclaw
version: 2.0.0
author: clawdcontext-os
capabilities:
  - file_read:/workspace/**
  - file_list:/workspace/**
  - file_write:/workspace/**
  - search:/workspace/**
  - exec:bash
  - exec:python
  - exec:javascript
  - exec:html
  - execute_code
  - search_workspace
  - security_scan
  - security_scan:*
rate_limit: 100/minute
token_budget: 200000
signature: "UNSIGNED — run `make sign` to generate"
signed_by: ""
signed_at: ""
---

# SKILL.md — OpenClaw Agent Runtime

## Purpose

Core AI agent runtime for ClawdContext OS. Reads Markdown OS kernel files,
executes user requests, and routes all tool calls through AgentProxy (Layer 4).
Now supports real code execution via Docker sandbox (code-runner service).

## When to Load

- Always loaded — this is the primary agent skill.

## Capabilities

- **execute_code** — Execute Python/JavaScript/Bash/HTML in Docker sandbox
- **file_read** — Read any file in the workspace
- **file_write** — Write files to the workspace
- **file_list** — List directory contents
- **search_workspace** — Search workspace for patterns
- **exec:bash** — Execute sandboxed bash commands
- **security_scan** — Trigger Layer 1 scanner on content

## Constraints

- All tool calls are mediated by AgentProxy
- All actions are logged by FlightRecorder (Layer 5)
- Code execution happens in isolated Docker sandbox (code-runner)
- No network access (net: not granted)
- No credential access
- No file writes (use file-writer skill for that)

## Audit Trail

Every tool call is logged to FlightRecorder with full chain-of-custody:
```
[ISO-8601] TOOL_CALL tool=file_read:CLAUDE.md decision=ALLOW agent=openclaw
```
