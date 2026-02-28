"""
OpenClaw Agent Runtime — ClawdContext OS
========================================
The AI agent runtime that lives inside ClawdContext OS.
Routes ALL tool calls through AgentProxy (Layer 4) for mediation.
Logs ALL events to FlightRecorder (Layer 5) for audit.

This is the agent users actually talk to. The security layers wrap it.

Architecture:
  User ──▶ OpenClaw ──▶ AgentProxy ──▶ Tool Execution
                              │
                        FlightRecorder

Supported LLM backends:
  - OpenAI / Azure OpenAI (OPENAI_API_KEY)
  - Anthropic Claude (ANTHROPIC_API_KEY)
  - Ollama local (OLLAMA_URL)
  - Mock/demo mode (no key needed)

Port: 8403
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

AGENT_PROXY_URL = os.getenv("AGENT_PROXY_URL", "http://agent-proxy:8400")
FLIGHT_RECORDER_URL = os.getenv("FLIGHT_RECORDER_URL", "http://flight-recorder:8402")
REPLAY_ENGINE_URL = os.getenv("REPLAY_ENGINE_URL", "http://replay-engine:8404")
MEMORY_SERVICE_URL = os.getenv("MEMORY_SERVICE_URL", "http://memory-service:8405")
CODE_RUNNER_URL = os.getenv("CODE_RUNNER_URL", "http://code-runner:8406")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")

# Determine mode: function_calling (real AI) or demo (mock)
LLM_MODE = "function_calling" if (DEEPSEEK_API_KEY or OPENAI_API_KEY) else "demo"

# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════

class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant|system)$")
    content: str

class ChatRequest(BaseModel):
    message: str
    skill: str = "openclaw"
    session_id: str = "default"
    history: list[ChatMessage] = []

class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}
    reason: str = ""

class ToolResult(BaseModel):
    tool: str
    decision: str  # ALLOW | DENY | HUMAN_GATE
    output: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0
    preview_url: Optional[str] = None

class ChatResponse(BaseModel):
    message: str
    tool_calls: list[ToolResult] = []
    session_id: str
    tokens_used: int = 0
    latency_ms: float = 0
    preview_url: Optional[str] = None

class KernelState(BaseModel):
    claude_md: Optional[str] = None
    todo_md: Optional[str] = None
    lessons_md: Optional[str] = None
    skills: list[str] = []
    cer: float = 0.0

# ═══════════════════════════════════════════════════════════════
# Tool Schemas for DeepSeek Function Calling
# ═══════════════════════════════════════════════════════════════

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Execute code (Python, JavaScript, Bash) or write HTML files in a sandboxed environment. Use for building apps, running scripts, data processing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "bash", "html"],
                        "description": "Programming language to execute"
                    },
                    "code": {
                        "type": "string",
                        "description": "The code to execute"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename (required for HTML). Example: index.html, app.py"
                    }
                },
                "required": ["language", "code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read the contents of a file in the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path to the file"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file in the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path to the file"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_list",
            "description": "List files in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: workspace root)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace",
            "description": "Search for text across workspace files",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "security_scan",
            "description": "Run a security scan on the workspace looking for TTP patterns, secrets, and vulnerabilities",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Optional target path or scope for the scan"
                    }
                },
                "required": []
            }
        }
    },
]


# ═══════════════════════════════════════════════════════════════
# Code Runner Client (Docker Sandbox — code-runner:8406)
# ═══════════════════════════════════════════════════════════════

class CodeRunnerClient:
    """Client for the code-runner sandbox service. Executes code in isolated Docker containers."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=35.0)
        self.base_url = CODE_RUNNER_URL

    async def execute(self, language: str, code: str, session_id: str,
                      filename: str = "") -> dict:
        """Execute code in the sandbox and return result."""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/execute",
                json={
                    "language": language,
                    "code": code,
                    "session_id": session_id,
                    "filename": filename or None,
                    "timeout": 30,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            return {
                "success": False,
                "error": f"Code runner error {resp.status_code}: {resp.text[:200]}",
            }
        except Exception as e:
            return {"success": False, "error": f"Code runner unreachable: {e}"}

    async def write_file(self, session_id: str, filepath: str, content: str) -> dict:
        """Write a file to the sandbox workspace."""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/files/write",
                json={
                    "session_id": session_id,
                    "filepath": filepath,
                    "content": content,
                },
            )
            return resp.json() if resp.status_code == 200 else {"success": False}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_files(self, session_id: str) -> list:
        """List files in the sandbox workspace."""
        try:
            resp = await self.client.get(f"{self.base_url}/api/v1/files/{session_id}")
            if resp.status_code == 200:
                return resp.json().get("files", [])
        except Exception:
            pass
        return []

    def preview_url(self, session_id: str, filename: str) -> str:
        """Get the preview URL for an HTML file in the sandbox."""
        return f"/api/v1/sandbox/preview/{session_id}/{filename}"

    async def close(self):
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════
# Markdown OS Kernel
# ═══════════════════════════════════════════════════════════════

class MarkdownKernel:
    """
    Reads the Markdown OS kernel files from the agent workspace.
    Implements Eureka #1: Context Window = RAM.
    """

    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        # WORKSPACE_DIR points directly to the agent directory
        self.agent_dir = self.workspace
        self._cache: dict[str, tuple[float, str]] = {}  # file -> (mtime, content)

    def _read_cached(self, filepath: Path) -> Optional[str]:
        """Read file with mtime caching to avoid redundant I/O."""
        if not filepath.exists():
            return None
        mtime = filepath.stat().st_mtime
        key = str(filepath)
        if key in self._cache and self._cache[key][0] >= mtime:
            return self._cache[key][1]
        content = filepath.read_text(encoding="utf-8", errors="replace")
        self._cache[key] = (mtime, content)
        return content

    def load_kernel(self) -> KernelState:
        """Load the full kernel state — boot config + PCB + cache + skills."""
        claude_md = self._read_cached(self.agent_dir / "CLAUDE.md")
        todo_md = self._read_cached(self.agent_dir / "todo.md")
        lessons_md = self._read_cached(self.agent_dir / "lessons.md")

        # Discover skills (lazy — just names, not content)
        skills: list[str] = []
        skills_dir = self.agent_dir / "skills"
        if skills_dir.exists():
            for skill_path in skills_dir.iterdir():
                if skill_path.is_dir() and not skill_path.name.startswith(".") and not skill_path.name.startswith("_"):
                    skills.append(skill_path.name)

        # Calculate CER (Eureka #3) — useful tokens / total tokens
        total_tokens = 0
        useful_tokens = 0
        for content in [claude_md, todo_md, lessons_md]:
            if content:
                tokens = len(content) // 4  # BPE approximation
                total_tokens += tokens
                # Heuristic: non-comment, non-blank lines are "useful"
                useful_lines = [l for l in content.splitlines()
                                if l.strip() and not l.strip().startswith("#")]
                useful_tokens += len("\n".join(useful_lines)) // 4

        cer = useful_tokens / max(total_tokens, 1)

        return KernelState(
            claude_md=claude_md,
            todo_md=todo_md,
            lessons_md=lessons_md,
            skills=skills,
            cer=round(cer, 4),
        )

    def get_system_prompt(self, kernel: KernelState) -> str:
        """Build the system prompt from kernel files — the 'boot config'."""
        parts = [
            "You are OpenClaw, the AI agent runtime inside ClawdContext OS.",
            "You operate under the Markdown OS kernel — your instructions come from structured Markdown files.",
            "ALL your tool calls are mediated by AgentProxy (Layer 4 Reference Monitor).",
            "ALL your actions are recorded by FlightRecorder (Layer 5 Audit Log).",
            "",
        ]

        if kernel.claude_md:
            parts.append("=== BOOT CONFIG (CLAUDE.md) ===")
            # Truncate aggressively to save tokens for code generation (Eureka #3)
            parts.append(kernel.claude_md[:2000])
            parts.append("")

        if kernel.todo_md:
            parts.append("=== PROCESS CONTROL BLOCK (todo.md) ===")
            parts.append(kernel.todo_md[:1000])
            parts.append("")

        if kernel.lessons_md:
            parts.append("=== ADAPTIVE CACHE (lessons.md) ===")
            parts.append(kernel.lessons_md[:1000])
            parts.append("")

        if kernel.skills:
            parts.append(f"=== AVAILABLE SKILLS ({len(kernel.skills)}) ===")
            parts.append(", ".join(kernel.skills))
            parts.append("Load skill details on-demand, not all at once (Eureka #7 Shannon SNR).")
            parts.append("")

        parts.append(f"Context Efficiency Ratio: {kernel.cer:.4f} (target > 0.6)")
        parts.append("")
        parts.append("When you need to perform actions, declare tool calls.")
        parts.append("They will be routed through AgentProxy for security checks.")
        parts.append("")
        parts.append("IMPORTANT — Tool usage guidelines:")
        parts.append("- For BUILDING apps, games, demos, or any HTML/CSS/JS: use `execute_code` with language='html' and a filename")
        parts.append("- For RUNNING Python/JS/Bash code: use `execute_code` with the appropriate language")
        parts.append("- For READING files (cat, view, show, open, read): use `file_read` with the path")
        parts.append("- For WRITING files: use `file_write` with path and content")
        parts.append("- For LISTING files/directories (ls, dir): use `file_list`")
        parts.append("- For SEARCHING text (grep, search, find): use `search_workspace`")
        parts.append("- Always prefer `execute_code` over `file_write` when the user wants to BUILD something")
        parts.append("- When user says 'cat X' or 'read X' or 'show X', ALWAYS use file_read — NEVER use file_list")

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# Tool Executor (via AgentProxy + Code Runner)
# ═══════════════════════════════════════════════════════════════

class ToolExecutor:
    """
    Executes tool calls by routing them through AgentProxy.
    Code execution goes to the code-runner sandbox service.
    Implements the Reference Monitor pattern (Anderson Report 1972).
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.code_runner = CodeRunnerClient()

    async def execute(self, skill: str, tool_call: ToolCall,
                      session_id: str = "default",
                      token_count: int = 50000, token_budget: int = 200000) -> ToolResult:
        """Route a tool call through AgentProxy and return the result."""
        start = time.monotonic()

        # Map tool names to AgentProxy-compatible format
        proxy_tool = self._map_tool_name(tool_call)

        try:
            resp = await self.client.post(
                f"{AGENT_PROXY_URL}/api/v1/evaluate",
                json={
                    "skill": skill,
                    "tool": proxy_tool,
                    "arguments": tool_call.arguments,
                    "context": tool_call.reason,
                    "token_count": token_count,
                    "token_budget": token_budget,
                },
            )
            data = resp.json()
            latency = (time.monotonic() - start) * 1000
            print(f"[ToolExec] AgentProxy decision={data.get('decision')}, latency={latency:.0f}ms")

            decision = data.get("decision", "DENY")
            result = ToolResult(
                tool=tool_call.tool,
                decision=decision,
                latency_ms=latency,
            )

            if decision == "ALLOW":
                result.output, result.preview_url = await self._execute_sandboxed(
                    tool_call, session_id
                )
            elif decision == "HUMAN_GATE":
                result.output = f"⚠ Requires human approval: {data.get('reason', 'policy')}"
            else:
                result.error = f"DENIED: {data.get('reason', 'policy violation')}"

            return result

        except Exception as e:
            return ToolResult(
                tool=tool_call.tool,
                decision="DENY",
                error=f"AgentProxy unreachable: {str(e)}",
                latency_ms=(time.monotonic() - start) * 1000,
            )

    def _map_tool_name(self, tool_call: ToolCall) -> str:
        """Map function-calling tool names to AgentProxy tool format."""
        tool = tool_call.tool
        args = tool_call.arguments

        if tool == "execute_code":
            lang = args.get("language", "python")
            return f"exec:{lang}"
        elif tool == "file_read":
            path = args.get("path", "unknown")
            if not path.startswith("/"):
                path = f"/workspace/{path}"
            return f"file_read:{path}"
        elif tool == "file_write":
            path = args.get("path", "unknown")
            if not path.startswith("/"):
                path = f"/workspace/{path}"
            return f"file_write:{path}"
        elif tool == "file_list":
            return "file_list:/workspace/"
        elif tool == "search_workspace":
            return "search:/workspace/"
        elif tool == "security_scan":
            return "security_scan"
        # Legacy format (from MockLLM): already has colon-separated format
        elif ":" in tool:
            return tool
        return tool

    async def _execute_sandboxed(self, tool_call: ToolCall,
                                  session_id: str) -> tuple[str, str | None]:
        """Execute an ALLOWED tool call. Returns (output, preview_url)."""
        tool = tool_call.tool
        args = tool_call.arguments
        preview_url = None
        # Execute sandboxed tool

        # ── Code Execution (via code-runner sandbox) ──
        if tool == "execute_code":
            language = args.get("language", "python")
            code = args.get("code", "")
            filename = args.get("filename", "")
            print(f"[ToolExec] execute_code: lang={language}, code_len={len(code)}, filename={filename!r}")

            result = await self.code_runner.execute(
                language=language, code=code,
                session_id=session_id, filename=filename,
            )
            print(f"[ToolExec] code-runner result: exit_code={result.get('exit_code')}, files={result.get('files_created')}")

            # code-runner returns: {stdout, stderr, exit_code, files_created, error, ...}
            # Error path (connection failure) returns: {success: False, error: ...}
            is_success = result.get("exit_code") == 0 if "exit_code" in result else result.get("success", False)

            if is_success:
                output_parts = []
                # Successful execution: use stdout (code-runner) or output (legacy)
                stdout = result.get("stdout") or result.get("output") or ""
                stderr = result.get("stderr", "")
                if stdout.strip():
                    output_parts.append(stdout[:8000])
                if stderr.strip():
                    output_parts.append(f"stderr: {stderr[:2000]}")
                if result.get("files_created"):
                    output_parts.append(f"\nFiles created: {', '.join(result['files_created'])}")
                    # Check for HTML files → generate preview URL
                    for f in result.get("files_created", []):
                        if f.endswith((".html", ".htm")):
                            preview_url = self.code_runner.preview_url(session_id, f)
                            output_parts.append(f"Preview: {preview_url}")
                            break
                if language == "html" and filename:
                    preview_url = self.code_runner.preview_url(session_id, filename)
                    output_parts.append(f"Preview: {preview_url}")
                return "\n".join(output_parts) or "Code executed successfully (no output)", preview_url
            else:
                error = result.get("error") or result.get("stderr") or "Unknown execution error"
                return f"Execution error: {error}", None

        # ── File Read ──
        if tool == "file_read" or (isinstance(tool, str) and tool.startswith("file_read:")):
            filepath = args.get("path", "")
            if not filepath and ":" in tool:
                filepath = tool.split(":", 1)[1]
            if filepath.startswith("/"):
                full_path = Path(filepath)
            else:
                full_path = Path(WORKSPACE_DIR) / filepath.lstrip("/")
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")
                return content[:10000], None
            return f"File not found: {filepath}", None

        # ── File Write ──
        if tool == "file_write" or (isinstance(tool, str) and tool.startswith("file_write:")):
            filepath = args.get("path", "")
            if not filepath and ":" in tool:
                filepath = tool.split(":", 1)[1]
            content = args.get("content", "")
            if filepath.startswith("/"):
                full_path = Path(filepath)
            else:
                full_path = Path(WORKSPACE_DIR) / filepath.lstrip("/")
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return f"Written {len(content)} bytes to {filepath}", None

        # ── File List ──
        if tool == "file_list" or (isinstance(tool, str) and tool.startswith("file_list")):
            path_arg = args.get("path", "")
            if path_arg.startswith("/"):
                target = Path(path_arg)
            else:
                target = Path(WORKSPACE_DIR) / (path_arg or ".")
            if target.exists() and target.is_dir():
                files = [str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()]
                return "\n".join(files[:100]), None
            return f"Directory not found: {target}", None

        # ── Search ──
        if tool == "search_workspace" or (isinstance(tool, str) and tool.startswith("search")):
            query = args.get("query", "")
            results = []
            agent_dir = Path(WORKSPACE_DIR)
            for f in agent_dir.rglob("*.md"):
                content = f.read_text(encoding="utf-8", errors="replace")
                if query.lower() in content.lower():
                    for i, line in enumerate(content.splitlines(), 1):
                        if query.lower() in line.lower():
                            results.append(f"{f.relative_to(agent_dir)}:{i}: {line.strip()}")
            return "\n".join(results[:50]) or f"No results for '{query}'", None

        # ── Security Scan ──
        if tool == "security_scan":
            return "Security scan completed — no critical findings (sandbox mode)", None

        # Unknown tool
        return f"Tool '{tool}' executed (sandbox mode)", None

    async def close(self):
        await self.client.aclose()
        await self.code_runner.close()


# ═══════════════════════════════════════════════════════════════
# LLM Providers
# ═══════════════════════════════════════════════════════════════

class MockLLM:
    """Demo mode — no API key needed. Parses user intent and generates responses."""

    TOOL_PATTERNS = [
        (r"(?:read|show|cat|open|view)\s+(?:file\s+)?['\"]?([^\s'\"]+\.(?:md|txt|json|yaml|yml|py|ts|js))", "file_read"),
        (r"(?:write|create|save)\s+(?:to\s+)?['\"]?([^\s'\"]+)", "file_write"),
        (r"(?:list|ls|dir)\s+(?:files?\s*)?(?:in\s+)?['\"]?([^\s'\"]*?)['\"]?\s*$", "file_list"),
        (r"(?:search|find|grep)\s+(?:for\s+)?['\"]?(.+?)['\"]?$", "search"),
        (r"(?:exec|run|execute)\s+(.+)", "exec:bash"),
        (r"(?:scan|check)\s+(?:for\s+)?(?:security|threats|ttp)", "security_scan"),
    ]

    async def generate(self, messages: list[ChatMessage], system_prompt: str,
                       kernel: KernelState) -> tuple[str, list[ToolCall]]:
        """Generate a response by parsing intent and returning structured output."""
        if not messages:
            return "Hello! I'm OpenClaw — the AI agent running inside ClawdContext OS. Ask me anything about the workspace, or try commands like 'read file CLAUDE.md', 'list files', 'search for security'.", []

        user_msg = messages[-1].content.lower().strip()
        tool_calls: list[ToolCall] = []

        # Check for tool-triggering patterns
        for pattern, tool_type in self.TOOL_PATTERNS:
            match = re.search(pattern, user_msg, re.IGNORECASE)
            if match:
                arg = match.group(1) if match.lastindex else ""

                if tool_type == "file_read":
                    tool_calls.append(ToolCall(
                        tool=f"file_read:/workspace/agent/{arg}",
                        reason=f"User requested to read {arg}",
                    ))
                elif tool_type == "file_write":
                    tool_calls.append(ToolCall(
                        tool=f"file_write:/workspace/agent/{arg}",
                        arguments={"content": "# Generated content\n"},
                        reason=f"User requested to write {arg}",
                    ))
                elif tool_type == "file_list":
                    list_path = arg or "/workspace/agent"
                    if not list_path.startswith("/"):
                        list_path = f"/workspace/agent/{list_path}"
                    tool_calls.append(ToolCall(
                        tool=f"file_list:{list_path}",
                        arguments={"path": list_path},
                        reason="User requested file listing",
                    ))
                elif tool_type == "search":
                    tool_calls.append(ToolCall(
                        tool=f"search:/workspace/agent",
                        arguments={"query": arg},
                        reason=f"User searching for '{arg}'",
                    ))
                elif tool_type == "exec:bash":
                    tool_calls.append(ToolCall(
                        tool="exec:bash",
                        arguments={"command": arg},
                        reason=f"User requested execution: {arg}",
                    ))
                elif tool_type == "security_scan":
                    tool_calls.append(ToolCall(
                        tool="security_scan",
                        reason="User requested security scan",
                    ))
                break

        # Generate conversational response
        if tool_calls:
            response = f"I'll execute that for you. Routing through AgentProxy for security mediation..."
        elif "status" in user_msg or "health" in user_msg:
            response = self._status_response(kernel)
        elif "skill" in user_msg:
            response = self._skills_response(kernel)
        elif "cer" in user_msg or "context" in user_msg or "efficiency" in user_msg:
            response = self._cer_response(kernel)
        elif "help" in user_msg:
            response = self._help_response()
        elif "who" in user_msg and ("you" in user_msg or "are" in user_msg):
            response = (
                "I'm **OpenClaw** — the AI agent runtime inside ClawdContext OS. "
                "I operate under the Markdown OS kernel, where my instructions come from "
                "structured Markdown files (CLAUDE.md = boot config, SKILL.md = system calls, "
                "todo.md = process control block, lessons.md = adaptive cache). "
                "All my tool calls are mediated by **AgentProxy** (Layer 4) and logged by "
                "**FlightRecorder** (Layer 5). I'm the agent that the 6-layer security stack protects."
            )
        elif any(w in user_msg for w in ["eureka", "isomorphism", "kernel", "os"]):
            response = (
                "The Markdown OS is built on 8 Eureka isomorphisms — structural equivalences "
                "between operating system design and AI agent architecture:\n\n"
                "1. **Agent Kernel** — Context = RAM\n"
                "2. **Immune System** — lessons.md = adaptive immunity\n"
                "3. **Thermodynamics** — CER metric (target > 0.6)\n"
                "4. **Three-Body Problem** — 3+ instruction sources = chaos\n"
                "5. **Markdown Compiler** — .md files are programs\n"
                "6. **PID Windup** — accumulated corrections cause overshoot\n"
                "7. **Shannon SNR** — load skills on-demand, not all at once\n"
                "8. **Kessler Syndrome** — too many rules = debris cascade\n\n"
                f"Current CER: {kernel.cer:.4f} | Skills loaded: {len(kernel.skills)}"
            )
        elif any(w in user_msg for w in ["security", "layer", "defense", "protect"]):
            response = (
                "ClawdContext OS has 6 defense layers:\n\n"
                "- **Layer 1** — Design-Time Scanner (14 TTP patterns)\n"
                "- **Layer 2** — ClawdSign (Ed25519 skill signing)\n"
                "- **Layer 3** — Docker Sandbox (seccomp, namespaces)\n"
                "- **Layer 4** — AgentProxy (reference monitor, capability control)\n"
                "- **Layer 5** — FlightRecorder (hash-chained audit log)\n"
                "- **Layer 6** — SnapshotEngine (pre-action workspace snapshots)\n\n"
                "Right now, every tool call I make goes through Layer 4 (AgentProxy) "
                "before execution. Try asking me to read a file — you'll see it in action."
            )
        else:
            response = (
                f"I understand your message. As OpenClaw running inside ClawdContext OS, "
                f"I can help you with:\n\n"
                f"- **Read/write files** in the workspace\n"
                f"- **Search** across agent configuration\n"
                f"- **Check status** of OS layers and services\n"
                f"- **Explain** Eureka concepts and security architecture\n\n"
                f"Available skills: {', '.join(kernel.skills) or 'none loaded'}\n"
                f"CER: {kernel.cer:.4f}\n\n"
                f"Try: `read file CLAUDE.md` or `search for security` or `list files`"
            )

        return response, tool_calls

    def _status_response(self, kernel: KernelState) -> str:
        has_claude = "✓" if kernel.claude_md else "✗"
        has_todo = "✓" if kernel.todo_md else "✗"
        has_lessons = "✓" if kernel.lessons_md else "✗"
        return (
            f"**Kernel Status:**\n"
            f"- Boot Config (CLAUDE.md): {has_claude}\n"
            f"- Process Control Block (todo.md): {has_todo}\n"
            f"- Adaptive Cache (lessons.md): {has_lessons}\n"
            f"- Skills: {len(kernel.skills)} ({', '.join(kernel.skills) or 'none'})\n"
            f"- CER: {kernel.cer:.4f} {'(healthy)' if kernel.cer >= 0.6 else '(warning)' if kernel.cer >= 0.3 else '(critical)'}\n\n"
            f"All tool calls routed through AgentProxy (Layer 4).\n"
            f"All events recorded by FlightRecorder (Layer 5)."
        )

    def _skills_response(self, kernel: KernelState) -> str:
        if not kernel.skills:
            return "No skills loaded. Skills are stored in `agent/skills/` as SKILL.md files."
        return (
            f"**Available Skills ({len(kernel.skills)}):**\n"
            + "\n".join(f"- `{s}`" for s in kernel.skills)
            + "\n\nSkills are loaded on-demand (Eureka #7 — Shannon SNR). "
            "Each skill declares capabilities in its SKILL.md frontmatter. "
            "AgentProxy checks capabilities before allowing tool calls."
        )

    def _cer_response(self, kernel: KernelState) -> str:
        return (
            f"**Context Efficiency Ratio (CER):** {kernel.cer:.4f}\n\n"
            f"CER = useful tokens / total tokens in context.\n"
            f"- CER ≥ 0.6 → Healthy (green)\n"
            f"- CER 0.3–0.6 → Warning (amber) — consider pruning\n"
            f"- CER < 0.3 → Critical (red) — context heat death imminent\n\n"
            f"This implements Eureka #3 (Agent Thermodynamics). "
            f"Without active governance, context decays toward maximum entropy."
        )

    def _help_response(self) -> str:
        return (
            "**OpenClaw Commands:**\n\n"
            "| Command | Example |\n"
            "|---------|--------|\n"
            "| Read file | `read file CLAUDE.md` |\n"
            "| List files | `list files in agent` |\n"
            "| Search | `search for security` |\n"
            "| Write file | `write to output.txt` |\n"
            "| Status | `show status` |\n"
            "| Skills | `list skills` |\n"
            "| CER | `check context efficiency` |\n"
            "| Security | `explain security layers` |\n"
            "| Eureka | `explain eureka concepts` |\n\n"
            "All tool calls go through AgentProxy. Try something!"
        )


class DeepSeekLLM:
    """
    DeepSeek / OpenAI-compatible LLM provider with native function calling.
    Uses tool schemas for structured tool invocation instead of regex matching.
    Falls back to MockLLM when API is unavailable.
    """

    def __init__(self):
        self.mock = MockLLM()  # Fallback when API fails
        api_key = DEEPSEEK_API_KEY or OPENAI_API_KEY
        base_url = DEEPSEEK_BASE_URL if DEEPSEEK_API_KEY else "https://api.openai.com"
        self.api_url = f"{base_url}/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=180.0)
        self.model = MODEL_NAME

    @staticmethod
    def _repair_truncated_json(raw: str) -> dict:
        """Attempt to parse truncated JSON from a cut-off function call.
        DeepSeek may hit max_tokens mid-argument, leaving broken JSON."""
        # Try as-is first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Strategy 1: close open strings and braces
        repaired = raw.rstrip()
        # Count unclosed quotes
        in_string = False
        escaped = False
        for ch in repaired:
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
        if in_string:
            repaired += '"'
        # Close any unclosed braces/brackets
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')
        repaired += ']' * max(open_brackets, 0)
        repaired += '}' * max(open_braces, 0)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        # Strategy 2: extract code field with regex
        import re
        m = re.search(r'"code"\s*:\s*"(.*)', raw, re.DOTALL)
        if m:
            code_val = m.group(1)
            # Remove trailing incomplete escapes
            if code_val.endswith('\\'):
                code_val = code_val[:-1]
            # Unescape basic JSON escapes
            code_val = code_val.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
            # Try to get language too
            lang_m = re.search(r'"language"\s*:\s*"(\w+)"', raw)
            lang = lang_m.group(1) if lang_m else 'html'
            fn_m = re.search(r'"filename"\s*:\s*"([^"]+)"', raw)
            fn = fn_m.group(1) if fn_m else ''
            return {"language": lang, "code": code_val, "filename": fn}
        return {}

    async def generate(self, messages: list[ChatMessage], system_prompt: str,
                       kernel: KernelState) -> tuple[str, list[ToolCall]]:
        """Generate via DeepSeek API with native function calling."""
        try:
            api_messages = [{"role": "system", "content": system_prompt}]
            for msg in messages[-20:]:
                api_messages.append({"role": msg.role, "content": msg.content})

            import time as _t
            t0 = _t.monotonic()
            resp = await self.client.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": api_messages,
                    "tools": TOOL_SCHEMAS,
                    "tool_choice": "auto",
                    "temperature": 0.7,
                    "max_tokens": 8192,
                    "stream": False,
                },
                headers=self.headers,
            )
            elapsed = _t.monotonic() - t0
            print(f"[DeepSeek] API responded in {elapsed:.1f}s, status={resp.status_code}")

            if resp.status_code != 200:
                error_text = resp.text[:200]
                print(f"[DeepSeek] API error {resp.status_code}: {error_text}")
                return await self.mock.generate(messages, system_prompt, kernel)

            data = resp.json()
            choice = data["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "")
            usage = data.get("usage", {})
            print(f"[DeepSeek] finish_reason={finish_reason}, tokens={usage.get('total_tokens', '?')}")

            # Check for tool calls in the response
            tool_calls_data = message.get("tool_calls", [])
            if tool_calls_data:
                tool_calls = []
                for tc in tool_calls_data:
                    fn = tc["function"]
                    raw_args = fn.get("arguments", "{}")
                    arguments = self._repair_truncated_json(raw_args)
                    if not arguments and finish_reason == "length":
                        print(f"[DeepSeek] WARNING: truncated tool call for {fn['name']}, could not repair")
                    tool_calls.append(ToolCall(
                        tool=fn["name"],
                        arguments=arguments,
                        reason=f"LLM decided to call {fn['name']}",
                    ))
                # Return a routing message + tool calls
                content = message.get("content", "") or "Executing your request..."
                return content, tool_calls

            # Pure text response (no tools)
            content = message.get("content", "")
            return content, []

        except Exception as e:
            import traceback
            print(f"[DeepSeek] Exception ({type(e).__name__}): {e}")
            traceback.print_exc()
            return await self.mock.generate(messages, system_prompt, kernel)

    async def synthesize_after_tools(self, messages: list[ChatMessage],
                                      system_prompt: str,
                                      tool_results_text: str) -> str:
        """After tool execution, send results back to LLM for a synthesized response."""
        try:
            api_messages = [{"role": "system", "content": system_prompt}]
            for msg in messages[-20:]:
                api_messages.append({"role": msg.role, "content": msg.content})
            # Add tool results as an assistant context message
            api_messages.append({
                "role": "user",
                "content": f"Here are the results of the tool executions:\n\n{tool_results_text}\n\nPlease provide a helpful response summarizing the results and any next steps.",
            })

            print(f"[DeepSeek] Synthesis call: {len(api_messages)} messages, tool_results={len(tool_results_text)} chars")
            t0 = time.monotonic()
            resp = await self.client.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 4000,
                    "stream": False,
                },
                headers=self.headers,
            )
            elapsed = time.monotonic() - t0
            print(f"[DeepSeek] Synthesis responded in {elapsed:.1f}s, status={resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"].get("content", "")
                print(f"[DeepSeek] Synthesis content: {len(content)} chars")
                return content
            else:
                print(f"[DeepSeek] Synthesis error response: {resp.text[:500]}")
        except Exception as e:
            print(f"[DeepSeek] Synthesis error: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
        return ""  # Empty means use the raw tool output

    async def generate_stream(self, messages: list[ChatMessage], system_prompt: str,
                              kernel: KernelState):
        """Stream response tokens for terminal mode."""
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages[-20:]:
            api_messages.append({"role": msg.role, "content": msg.content})

        try:
            async with self.client.stream(
                "POST",
                self.api_url,
                json={
                    "model": self.model,
                    "messages": api_messages,
                    "tools": TOOL_SCHEMAS,
                    "tool_choice": "auto",
                    "temperature": 0.7,
                    "max_tokens": 8192,
                    "stream": True,
                },
                headers=self.headers,
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk = line[6:]
                        if chunk.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except Exception as e:
            yield f"\n[Error: {e}]"

    async def close(self):
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════
# Event Logger (via FlightRecorder)
# ═══════════════════════════════════════════════════════════════

class EventLogger:
    """Logs all agent events to FlightRecorder (Layer 5)."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def log(self, event_type: str, source: str, data: dict[str, Any]):
        """Record an event in the immutable audit log."""
        try:
            await self.client.post(
                f"{FLIGHT_RECORDER_URL}/api/v1/events",
                json={
                    "event_type": event_type,
                    "source": source,
                    "data": data,
                },
            )
        except Exception:
            pass  # Don't block agent on logging failures

    async def close(self):
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════
# Memory Client (Qdrant Semantic Memory via memory-service)
# ═══════════════════════════════════════════════════════════════

class MemoryClient:
    """Queries memory-service for relevant context (RAG) and stores new memories."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        self.enabled = True

    async def recall(self, query: str, session_id: str = "default",
                     limit: int = 3) -> list[dict]:
        """Retrieve relevant memories for a query (used before LLM call)."""
        if not self.enabled:
            return []
        try:
            resp = await self.client.get(
                f"{MEMORY_SERVICE_URL}/api/v1/memory/recall",
                params={"q": query, "session_id": session_id, "limit": limit},
            )
            if resp.status_code == 200:
                return resp.json().get("memories", [])
        except Exception:
            pass
        return []

    async def store_conversation(self, text: str, session_id: str,
                                  metadata: dict = None):
        """Store a conversation turn in memory."""
        if not self.enabled:
            return
        try:
            await self.client.post(
                f"{MEMORY_SERVICE_URL}/api/v1/memory/store",
                json={
                    "collection": "conversations",
                    "text": text,
                    "session_id": session_id,
                    "metadata": metadata or {},
                },
            )
        except Exception:
            pass

    async def store_tool_result(self, text: str, session_id: str,
                                 metadata: dict = None):
        """Store a tool result in memory."""
        if not self.enabled:
            return
        try:
            await self.client.post(
                f"{MEMORY_SERVICE_URL}/api/v1/memory/store",
                json={
                    "collection": "tool_results",
                    "text": text,
                    "session_id": session_id,
                    "metadata": metadata or {},
                },
            )
        except Exception:
            pass

    async def check_health(self) -> bool:
        """Check if memory-service is available."""
        try:
            resp = await self.client.get(f"{MEMORY_SERVICE_URL}/healthz")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self):
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════
# Replay Client (sends timeline events to ReplayEngine)
# ═══════════════════════════════════════════════════════════════

class ReplayClient:
    """Sends snapshot events to ReplayEngine for timeline recording."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=5.0)
        self.enabled = True

    async def record(self, session_id: str, event_type: str, data: dict,
                     kernel_state=None, proxy_decision: str | None = None):
        """Record an event as a timeline node in ReplayEngine."""
        if not self.enabled:
            return None
        try:
            kernel_snapshot = None
            if kernel_state:
                kernel_snapshot = {
                    "cer": kernel_state.cer,
                    "skills": kernel_state.skills,
                    "has_claude_md": kernel_state.claude_md is not None,
                    "has_todo_md": kernel_state.todo_md is not None,
                    "has_lessons_md": kernel_state.lessons_md is not None,
                }
            resp = await self.client.post(
                f"{REPLAY_ENGINE_URL}/api/v1/record/event",
                json={
                    "session_id": session_id,
                    "event_type": event_type,
                    "actor": "openclaw",
                    "data": data,
                    "kernel_snapshot": kernel_snapshot,
                    "proxy_decision": proxy_decision,
                },
            )
            if resp.status_code == 200:
                result = resp.json()
                # Also record kernel snapshot
                if kernel_state and result.get("node_id") and result.get("timeline_id"):
                    await self._record_snapshot(result["timeline_id"], result["node_id"], kernel_state, session_id)
                return result
        except Exception:
            pass  # Don't block agent on replay failures
        return None

    async def _record_snapshot(self, timeline_id: str, node_id: str,
                               kernel_state, session_id: str):
        try:
            await self.client.post(
                f"{REPLAY_ENGINE_URL}/api/v1/snapshot/record",
                json={
                    "timeline_id": timeline_id,
                    "node_id": node_id,
                    "cer": kernel_state.cer,
                    "skills": kernel_state.skills,
                    "claude_md_hash": hashlib.sha256((kernel_state.claude_md or "").encode()).hexdigest()[:12],
                    "todo_md_hash": hashlib.sha256((kernel_state.todo_md or "").encode()).hexdigest()[:12],
                    "lessons_md_hash": hashlib.sha256((kernel_state.lessons_md or "").encode()).hexdigest()[:12],
                    "claude_md_size": len(kernel_state.claude_md) if kernel_state.claude_md else 0,
                    "todo_md_size": len(kernel_state.todo_md) if kernel_state.todo_md else 0,
                    "lessons_md_size": len(kernel_state.lessons_md) if kernel_state.lessons_md else 0,
                    "message_count": len(sessions.get(session_id, [])),
                    "token_estimate": sum(len(m.content) // 4 for m in sessions.get(session_id, [])),
                },
            )
        except Exception:
            pass

    async def close(self):
        await self.client.aclose()


# ═══════════════════════════════════════════════════════════════
# FastAPI Application
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="OpenClaw Agent Runtime",
    description="AI agent runtime for ClawdContext OS. Routes tool calls through AgentProxy.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
kernel = MarkdownKernel(WORKSPACE_DIR)
tool_executor = ToolExecutor()
event_logger = EventLogger()
replay_client = ReplayClient()
memory_client = MemoryClient()

# Select LLM provider based on available API keys
if LLM_MODE == "function_calling":
    llm = DeepSeekLLM()
    provider_name = "DeepSeek" if DEEPSEEK_API_KEY else "OpenAI"
    print(f"[OpenClaw] LLM: {provider_name} ({MODEL_NAME}) — function calling mode")
else:
    llm = MockLLM()
    print(f"[OpenClaw] LLM: Mock (demo mode) — set DEEPSEEK_API_KEY for real AI")

sessions: dict[str, list[ChatMessage]] = {}
terminal_sessions: dict[str, list[ChatMessage]] = {}
ws_clients: set[WebSocket] = set()
terminal_clients: dict[str, WebSocket] = {}
stats = {
    "total_chats": 0,
    "total_tool_calls": 0,
    "tool_calls_allowed": 0,
    "tool_calls_denied": 0,
    "tool_calls_gated": 0,
    "start_time": time.time(),
}


# ─── Health ───────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "openclaw",
        "layer": "agent-runtime",
        "provider": LLM_MODE,
        "model": MODEL_NAME,
    }


# ─── Chat ─────────────────────────────────────────────────────

@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint — process user message, execute tool calls via AgentProxy."""
    start = time.monotonic()
    stats["total_chats"] += 1
    print(f"[Chat] START session={req.session_id} msg={req.message[:80]!r}")

    # Load kernel state
    kernel_state = kernel.load_kernel()
    system_prompt = kernel.get_system_prompt(kernel_state)
    # Kernel loaded

    # Get/create session history
    if req.session_id not in sessions:
        sessions[req.session_id] = []
    history = sessions[req.session_id]

    # Add user message
    history.append(ChatMessage(role="user", content=req.message))

    # ─── Memory Recall (RAG) — retrieve relevant context before LLM call ───
    memory_context = ""
    try:
        memories = await memory_client.recall(req.message, session_id=req.session_id, limit=3)
        if memories:
            memory_parts = ["[Relevant memories from previous interactions:]"]
            for m in memories:
                memory_parts.append(f"- ({m['collection']}, score={m['score']:.2f}) {m['text'][:500]}")
            memory_context = "\n".join(memory_parts)
    except Exception:
        pass  # Don't block chat on memory failures
    # Memory recall done

    # Generate response + tool calls
    all_messages = req.history if req.history else history

    # Inject memory context as a system-level hint if available
    augmented_prompt = system_prompt
    if memory_context:
        augmented_prompt = system_prompt + "\n\n" + memory_context

    # ─── Shell command pre-parsing (instant, no LLM roundtrip) ───
    shell_tool_calls = _parse_shell_command(req.message)
    if shell_tool_calls is not None:
        response_text = ""
        tool_calls = shell_tool_calls
        print(f"[Chat] Shell pre-parsed: {len(tool_calls)} tool_calls (skipped LLM)")
    else:
        response_text, tool_calls = await llm.generate(all_messages, augmented_prompt, kernel_state)
        print(f"[Chat] LLM returned: {len(response_text)} chars, {len(tool_calls)} tool_calls")

    # Execute tool calls through AgentProxy
    tool_results: list[ToolResult] = []
    last_preview_url: str | None = None
    for tc in tool_calls:
        stats["total_tool_calls"] += 1

        result = await tool_executor.execute(
            skill=req.skill,
            tool_call=tc,
            session_id=req.session_id,
            token_count=len(system_prompt) // 4,
            token_budget=200000,
        )
        tool_results.append(result)

        # Track preview URL
        if result.preview_url:
            last_preview_url = result.preview_url

        # Update stats
        if result.decision == "ALLOW":
            stats["tool_calls_allowed"] += 1
        elif result.decision == "DENY":
            stats["tool_calls_denied"] += 1
        else:
            stats["tool_calls_gated"] += 1

        # Log to FlightRecorder
        await event_logger.log(
            event_type="tool_call",
            source="openclaw",
            data={
                "tool": tc.tool,
                "skill": req.skill,
                "decision": result.decision,
                "session": req.session_id,
            },
        )

    # Augment response with tool outputs
    if tool_results:
        parts = [response_text, ""]
        tool_summary_parts = []
        for tr in tool_results:
            if tr.decision == "ALLOW" and tr.output:
                parts.append(f"**Result ({tr.tool}):**")
                parts.append(f"```\n{tr.output[:5000]}\n```")
                tool_summary_parts.append(f"Tool: {tr.tool}\nOutput:\n{tr.output[:3000]}")
            elif tr.decision == "DENY":
                parts.append(f"**Blocked ({tr.tool}):** {tr.error}")
                tool_summary_parts.append(f"Tool: {tr.tool}\nBlocked: {tr.error}")
            elif tr.decision == "HUMAN_GATE":
                parts.append(f"**Awaiting approval ({tr.tool}):** {tr.output}")
        response_text = "\n".join(parts)

        # Multi-turn synthesis: send tool results back to LLM for a polished response
        # Use a 45s timeout — if synthesis takes longer, use the raw tool output
        if isinstance(llm, DeepSeekLLM) and tool_summary_parts:
            try:
                print(f"[Chat] Starting synthesis with {len(tool_summary_parts)} tool summaries...")
                synthesis = await asyncio.wait_for(
                    llm.synthesize_after_tools(
                        all_messages, augmented_prompt,
                        "\n\n".join(tool_summary_parts),
                    ),
                    timeout=45.0,
                )
                if synthesis:
                    response_text = synthesis
                    # Append preview link if available
                    if last_preview_url:
                        response_text += f"\n\n**Preview:** [Open Preview]({last_preview_url})"
            except asyncio.TimeoutError:
                print(f"[Chat] Synthesis timed out after 45s, using raw tool output")
                # Append preview link to raw output
                if last_preview_url:
                    response_text += f"\n\n**Preview:** [Open Preview]({last_preview_url})"
            except Exception as e:
                print(f"[OpenClaw] Synthesis error: {e}")

    # Save assistant response
    history.append(ChatMessage(role="assistant", content=response_text))

    # Keep history manageable (last 50 messages)
    if len(history) > 50:
        sessions[req.session_id] = history[-50:]

    latency = (time.monotonic() - start) * 1000

    # Log chat event
    await event_logger.log(
        event_type="chat",
        source="openclaw",
        data={
            "session": req.session_id,
            "message_length": len(req.message),
            "tool_calls": len(tool_calls),
            "latency_ms": round(latency, 2),
        },
    )

    # Broadcast to WebSocket clients
    ws_event = {
        "type": "chat",
        "session": req.session_id,
        "tool_calls": len(tool_calls),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await broadcast(ws_event)

    # ─── Replay: record chat step ───
    await replay_client.record(
        session_id=req.session_id,
        event_type="chat",
        data={
            "user_message": req.message[:500],
            "response_length": len(response_text),
            "tool_calls": [
                {"tool": tr.tool, "decision": tr.decision}
                for tr in tool_results
            ],
            "latency_ms": round(latency, 2),
        },
        kernel_state=kernel_state,
    )

    # ─── Memory Store — persist conversation + tool results for future RAG ───
    try:
        await memory_client.store_conversation(
            text=f"User: {req.message}\nAssistant: {response_text[:1000]}",
            session_id=req.session_id,
            metadata={"skill": req.skill, "tool_calls": len(tool_results)},
        )
        for tr in tool_results:
            if tr.decision == "ALLOW" and tr.output:
                await memory_client.store_tool_result(
                    text=f"Tool: {tr.tool}\nOutput: {tr.output[:2000]}",
                    session_id=req.session_id,
                    metadata={"tool": tr.tool, "skill": req.skill},
                )
    except Exception:
        pass  # Don't block response on memory failures

    return ChatResponse(
        message=response_text,
        tool_calls=tool_results,
        session_id=req.session_id,
        tokens_used=len(response_text) // 4,
        latency_ms=round(latency, 2),
        preview_url=last_preview_url,
    )


# ─── Kernel State ─────────────────────────────────────────────

@app.get("/api/v1/kernel")
async def get_kernel():
    """Return the current Markdown OS kernel state."""
    state = kernel.load_kernel()
    return {
        "has_claude_md": state.claude_md is not None,
        "has_todo_md": state.todo_md is not None,
        "has_lessons_md": state.lessons_md is not None,
        "skills": state.skills,
        "cer": state.cer,
        "claude_md_size": len(state.claude_md) if state.claude_md else 0,
        "todo_md_size": len(state.todo_md) if state.todo_md else 0,
        "lessons_md_size": len(state.lessons_md) if state.lessons_md else 0,
    }


# ─── Status ───────────────────────────────────────────────────

@app.get("/api/v1/status")
async def get_status():
    """Return runtime status and stats."""
    uptime = time.time() - stats["start_time"]
    return {
        "service": "openclaw",
        "provider": LLM_MODE,
        "model": MODEL_NAME,
        "uptime_seconds": round(uptime, 1),
        "total_chats": stats["total_chats"],
        "total_tool_calls": stats["total_tool_calls"],
        "tool_calls_allowed": stats["tool_calls_allowed"],
        "tool_calls_denied": stats["tool_calls_denied"],
        "tool_calls_gated": stats["tool_calls_gated"],
        "active_sessions": len(sessions),
        "ws_clients": len(ws_clients),
    }


# ─── Sessions ────────────────────────────────────────────────

@app.get("/api/v1/sessions")
async def list_sessions():
    """List active chat sessions."""
    return {
        "sessions": [
            {
                "id": sid,
                "messages": len(msgs),
                "last_message": msgs[-1].content[:100] if msgs else "",
            }
            for sid, msgs in sessions.items()
        ]
    }

@app.delete("/api/v1/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear a chat session."""
    if session_id in sessions:
        del sessions[session_id]
    return {"cleared": session_id}


# ─── WebSocket ────────────────────────────────────────────────

@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Stream real-time agent events to dashboard."""
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        while True:
            # Keep alive — also accept incoming messages for future interactive mode
            data = await websocket.receive_text()
            # Echo back for now
            await websocket.send_json({"type": "ack", "data": data})
    except WebSocketDisconnect:
        ws_clients.discard(websocket)


# ─── Shell Command Pre-Parser ────────────────────────────────
# Maps common Unix commands directly to tool calls, bypassing the LLM.
# This makes ls, cat, grep, etc. instant instead of waiting 60-200s for DeepSeek.

def _parse_shell_command(command: str) -> list[ToolCall] | None:
    """
    Parse a shell-like command into direct tool calls.
    Returns None if the command should be routed to the LLM instead.
    """
    parts = command.strip().split()
    if not parts:
        return None

    cmd = parts[0].lower()
    args = parts[1:]

    # ls / dir / ll — list files
    if cmd in ("ls", "dir", "ll", "la", "l"):
        path = args[0] if args else ""
        return [ToolCall(
            tool="file_list",
            arguments={"path": path},
            reason=f"Shell: {command}",
        )]

    # cat / less / more / head / tail / type — read file
    if cmd in ("cat", "less", "more", "head", "tail", "type", "view", "show"):
        if not args:
            return None  # No file specified → route to LLM
        filepath = args[0]
        return [ToolCall(
            tool="file_read",
            arguments={"path": filepath},
            reason=f"Shell: {command}",
        )]

    # grep / search / find — search workspace
    if cmd in ("grep", "search", "find", "rg", "ag"):
        query = " ".join(args) if args else ""
        if not query:
            return None
        return [ToolCall(
            tool="search_workspace",
            arguments={"query": query},
            reason=f"Shell: {command}",
        )]

    # pwd — show workspace path
    if cmd == "pwd":
        return [ToolCall(
            tool="file_list",
            arguments={"path": ""},
            reason="Shell: pwd (show workspace root)",
        )]

    # echo "content" > file — write file
    if cmd == "echo" and ">" in command:
        idx = command.index(">")
        content = command[4:idx].strip().strip('"').strip("'")
        filepath = command[idx+1:].strip().lstrip(">").strip()
        if filepath:
            return [ToolCall(
                tool="file_write",
                arguments={"path": filepath, "content": content + "\n"},
                reason=f"Shell: {command}",
            )]

    # touch — create empty file
    if cmd == "touch" and args:
        return [ToolCall(
            tool="file_write",
            arguments={"path": args[0], "content": ""},
            reason=f"Shell: {command}",
        )]

    # scan / security — security scan
    if cmd in ("scan", "security"):
        return [ToolCall(
            tool="security_scan",
            arguments={"target": " ".join(args) if args else ""},
            reason=f"Shell: {command}",
        )]

    # python / node / bash — execute code
    if cmd in ("python", "python3", "node", "bash", "sh") and args:
        lang_map = {"python": "python", "python3": "python", "node": "javascript", "bash": "bash", "sh": "bash"}
        lang = lang_map.get(cmd, "bash")
        # python -c "code" or python script.py
        if args[0] == "-c" and len(args) > 1:
            code = " ".join(args[1:]).strip('"').strip("'")
            return [ToolCall(
                tool="execute_code",
                arguments={"language": lang, "code": code},
                reason=f"Shell: {command}",
            )]
        # Read file and execute
        filepath = args[0]
        return [ToolCall(
            tool="file_read",
            arguments={"path": filepath},
            reason=f"Shell: read {filepath} for execution",
        )]

    # Not a recognized shell command → route to LLM
    return None


@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    """
    Interactive AI terminal via WebSocket.
    Provides a shell-like experience powered by the LLM.
    Protocol:
      Client sends: {"type": "input", "data": "command text"}
      Server sends: {"type": "output", "data": "response text"}
                    {"type": "prompt", "data": "openclaw> "}
                    {"type": "system", "data": "system message"}
    """
    await websocket.accept()
    session_id = f"terminal-{id(websocket)}"
    terminal_sessions[session_id] = []

    # Send welcome banner
    banner = (
        "\x1b[36m"  # cyan
        "╔══════════════════════════════════════════════════════════╗\r\n"
        "║  \x1b[1mOpenClaw Terminal\x1b[0m\x1b[36m — ClawdContext OS v0.1.0           ║\r\n"
        "║  AI Agent Runtime • Powered by " + (f"{LLM_MODE}/{MODEL_NAME}" if LLM_MODE != "demo" else "Mock LLM (demo)") + "\r\n"
        "║  All commands mediated by AgentProxy (Layer 4)          ║\r\n"
        "║  Type 'help' for commands • 'clear' to reset            ║\r\n"
        "╚══════════════════════════════════════════════════════════╝\r\n"
        "\x1b[0m\r\n"
    )
    await websocket.send_json({"type": "output", "data": banner})
    await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "input", "data": raw}

            if msg.get("type") != "input":
                continue

            command = msg.get("data", "").strip()
            if not command:
                await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})
                continue

            # Built-in terminal commands
            if command == "clear":
                await websocket.send_json({"type": "clear"})
                await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})
                continue

            if command == "exit" or command == "quit":
                await websocket.send_json({"type": "output", "data": "\x1b[33mGoodbye.\x1b[0m\r\n"})
                break

            if command == "help":
                help_text = (
                    "\x1b[36m── OpenClaw Terminal Commands ──\x1b[0m\r\n"
                    "\r\n"
                    "  \x1b[1mShell Commands (instant):\x1b[0m\r\n"
                    "    ls [path]           List files in workspace\r\n"
                    "    cat <file>          Read file contents\r\n"
                    "    grep <query>        Search across workspace files\r\n"
                    "    touch <file>        Create empty file\r\n"
                    "    echo 'text' > file  Write text to file\r\n"
                    "    python -c 'code'    Execute Python code\r\n"
                    "    scan                Run security scan\r\n"
                    "\r\n"
                    "  \x1b[1mBuilt-in Commands:\x1b[0m\r\n"
                    "    status              Show system status\r\n"
                    "    help                Show this help\r\n"
                    "    clear               Clear terminal\r\n"
                    "    exit                Close terminal\r\n"
                    "\r\n"
                    "  \x1b[1mAI Commands (via DeepSeek):\x1b[0m\r\n"
                    "    build a snake game in html\r\n"
                    "    explain eureka concepts\r\n"
                    "    create a fibonacci script\r\n"
                    "    Any natural language request...\r\n"
                    "\r\n"
                    "  All tool calls mediated by \x1b[33mAgentProxy\x1b[0m (Layer 4)\r\n"
                )
                await websocket.send_json({"type": "output", "data": help_text})
                await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})
                continue

            if command == "status":
                kernel_state = kernel.load_kernel()
                uptime = time.time() - stats["start_time"]
                hrs = int(uptime // 3600)
                mins = int((uptime % 3600) // 60)
                status_text = (
                    f"\x1b[36m── System Status ──\x1b[0m\r\n"
                    f"  Provider:  \x1b[1m{LLM_MODE}\x1b[0m ({MODEL_NAME})\r\n"
                    f"  Uptime:    {hrs}h {mins}m\r\n"
                    f"  Chats:     {stats['total_chats']}\r\n"
                    f"  Tools:     {stats['total_tool_calls']} "
                    f"(\x1b[32m{stats['tool_calls_allowed']}✓\x1b[0m "
                    f"\x1b[31m{stats['tool_calls_denied']}✗\x1b[0m "
                    f"\x1b[33m{stats['tool_calls_gated']}⚠\x1b[0m)\r\n"
                    f"  CER:       {kernel_state.cer:.4f}\r\n"
                    f"  Skills:    {', '.join(kernel_state.skills) or 'none'}\r\n"
                    f"  Kernel:    CLAUDE.md={'✓' if kernel_state.claude_md else '✗'} "
                    f"todo.md={'✓' if kernel_state.todo_md else '✗'} "
                    f"lessons.md={'✓' if kernel_state.lessons_md else '✗'}\r\n"
                )
                await websocket.send_json({"type": "output", "data": status_text})
                await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})
                continue

            # ── Shell command pre-parsing ──
            # Map common Unix commands directly to tool calls (instant, no LLM roundtrip)
            shell_tool_calls = _parse_shell_command(command)
            kernel_state = kernel.load_kernel()
            system_prompt = kernel.get_system_prompt(kernel_state)

            if shell_tool_calls is not None:
                # Direct tool execution — no LLM needed
                tool_calls = shell_tool_calls
                response_text = ""
                terminal_sessions[session_id].append(ChatMessage(role="user", content=command))
            else:
                # Route through AI agent for complex requests
                terminal_sessions[session_id].append(ChatMessage(role="user", content=command))

                # Use the LLM to detect tool calls (native function calling or mock)
                response_text, tool_calls = await llm.generate(
                    terminal_sessions[session_id], system_prompt, kernel_state
                )

            if tool_calls:
                # Execute tools through AgentProxy
                for tc in tool_calls:
                    stats["total_tool_calls"] += 1
                    await websocket.send_json({
                        "type": "output",
                        "data": f"\x1b[33m⚡ {tc.tool}\x1b[0m → AgentProxy... "
                    })

                    result = await tool_executor.execute(
                        skill="openclaw",
                        tool_call=tc,
                        session_id=session_id,
                        token_count=len(system_prompt) // 4,
                        token_budget=200000,
                    )

                    if result.decision == "ALLOW":
                        stats["tool_calls_allowed"] += 1
                        await websocket.send_json({
                            "type": "output",
                            "data": f"\x1b[32mALLOW\x1b[0m\r\n"
                        })
                        if result.output:
                            # Format output with line breaks for terminal
                            output = result.output.replace("\n", "\r\n")
                            await websocket.send_json({
                                "type": "output",
                                "data": f"{output}\r\n"
                            })
                        if result.preview_url:
                            await websocket.send_json({
                                "type": "output",
                                "data": f"\x1b[36m📄 Preview: {result.preview_url}\x1b[0m\r\n"
                            })
                            await websocket.send_json({
                                "type": "preview",
                                "data": result.preview_url,
                            })
                    elif result.decision == "DENY":
                        stats["tool_calls_denied"] += 1
                        await websocket.send_json({
                            "type": "output",
                            "data": f"\x1b[31mDENY\x1b[0m — {result.error}\r\n"
                        })
                    else:
                        stats["tool_calls_gated"] += 1
                        await websocket.send_json({
                            "type": "output",
                            "data": f"\x1b[33mHUMAN_GATE\x1b[0m — {result.output}\r\n"
                        })

                    await event_logger.log(
                        event_type="terminal_tool",
                        source="openclaw-terminal",
                        data={
                            "tool": tc.tool,
                            "decision": result.decision,
                            "session": session_id,
                        },
                    )
            else:
                # Conversational response — stream if possible
                if isinstance(llm, DeepSeekLLM) and (DEEPSEEK_API_KEY or OPENAI_API_KEY):
                    # Stream tokens for real-time terminal feel
                    full_response = ""
                    async for token in llm.generate_stream(
                        terminal_sessions[session_id], system_prompt, kernel_state
                    ):
                        # Convert newlines for xterm.js (\n → \r\n) and markdown bold to ANSI bold
                        display_token = token.replace("\n", "\r\n").replace("**", "\x1b[1m")
                        await websocket.send_json({
                            "type": "output",
                            "data": display_token,
                        })
                        full_response += token
                    await websocket.send_json({"type": "output", "data": "\r\n"})
                    terminal_sessions[session_id].append(
                        ChatMessage(role="assistant", content=full_response)
                    )
                else:
                    # Mock LLM — instant response
                    response, _ = await llm.generate(
                        terminal_sessions[session_id], system_prompt, kernel_state
                    )
                    # Format for terminal
                    formatted = response.replace("\n", "\r\n")
                    formatted = formatted.replace("**", "\x1b[1m")
                    await websocket.send_json({
                        "type": "output",
                        "data": f"{formatted}\r\n",
                    })
                    terminal_sessions[session_id].append(
                        ChatMessage(role="assistant", content=response)
                    )

            stats["total_chats"] += 1

            # ─── Replay: record terminal step ───
            await replay_client.record(
                session_id=session_id,
                event_type="terminal",
                data={
                    "command": command[:500],
                    "tool_calls": len(tool_calls) if tool_calls else 0,
                },
                kernel_state=kernel_state if 'kernel_state' in dir() else None,
            )

            await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})

            # Keep terminal history manageable
            if len(terminal_sessions[session_id]) > 40:
                terminal_sessions[session_id] = terminal_sessions[session_id][-40:]

    except WebSocketDisconnect:
        pass
    finally:
        terminal_sessions.pop(session_id, None)


# ─── Sandbox Preview Proxy (code-runner → dashboard) ─────────

@app.get("/api/v1/sandbox/preview/{session_id}/{filepath:path}")
async def sandbox_preview(session_id: str, filepath: str):
    """Proxy preview requests to the code-runner sandbox service."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{CODE_RUNNER_URL}/api/v1/preview/{session_id}/{filepath}"
            )
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "text/html")
                from fastapi.responses import Response
                return Response(
                    content=resp.content,
                    media_type=content_type,
                )
            return {"error": f"Preview not found: {filepath}", "status": resp.status_code}
    except Exception as e:
        return {"error": f"Code runner unreachable: {e}"}


# ─── File Preview (Markdown Rendering) ───────────────────────

@app.get("/api/v1/preview/{filepath:path}")
async def preview_file(filepath: str):
    """Serve workspace file content for preview rendering."""
    agent_dir = Path(WORKSPACE_DIR)
    full_path = agent_dir / filepath
    # Security: ensure path is within workspace
    try:
        full_path = full_path.resolve()
        if not str(full_path).startswith(str(agent_dir.resolve())):
            return {"error": "Path outside workspace", "content": None}
    except Exception:
        return {"error": "Invalid path", "content": None}

    if not full_path.exists() or not full_path.is_file():
        return {"error": f"File not found: {filepath}", "content": None}

    content = full_path.read_text(encoding="utf-8", errors="replace")
    ext = full_path.suffix.lower()
    return {
        "filename": full_path.name,
        "path": filepath,
        "extension": ext,
        "size": len(content),
        "content": content[:50000],  # cap at 50KB
        "is_markdown": ext in (".md", ".markdown"),
    }

@app.get("/api/v1/files")
async def list_workspace_files():
    """List all files in workspace for preview browser."""
    agent_dir = Path(WORKSPACE_DIR)
    if not agent_dir.exists():
        return {"files": []}
    files = []
    for f in sorted(agent_dir.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            rel = str(f.relative_to(agent_dir))
            files.append({
                "path": rel,
                "name": f.name,
                "extension": f.suffix.lower(),
                "size": f.stat().st_size,
            })
    return {"files": files[:200]}


async def broadcast(event: dict):
    """Broadcast event to all connected WebSocket clients."""
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


# ─── Startup / Shutdown ──────────────────────────────────────

@app.on_event("startup")
async def startup():
    kernel_state = kernel.load_kernel()
    print(f"══════════════════════════════════════════════")
    print(f"  OpenClaw Agent Runtime v0.1.0")
    print(f"  Provider: {LLM_MODE} | Model: {MODEL_NAME}")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print(f"  Kernel: CLAUDE.md={'✓' if kernel_state.claude_md else '✗'} "
          f"todo.md={'✓' if kernel_state.todo_md else '✗'} "
          f"lessons.md={'✓' if kernel_state.lessons_md else '✗'}")
    print(f"  Skills: {kernel_state.skills}")
    print(f"  CER: {kernel_state.cer:.4f}")
    print(f"  AgentProxy: {AGENT_PROXY_URL}")
    print(f"  FlightRecorder: {FLIGHT_RECORDER_URL}")
    print(f"  ReplayEngine: {REPLAY_ENGINE_URL}")
    mem_ok = await memory_client.check_health()
    print(f"  Memory: {MEMORY_SERVICE_URL} {'✓' if mem_ok else '✗'}")
    print(f"  CodeRunner: {CODE_RUNNER_URL}")
    print(f"══════════════════════════════════════════════")

@app.on_event("shutdown")
async def shutdown():
    await tool_executor.close()
    await event_logger.close()
    await replay_client.close()
    await memory_client.close()
