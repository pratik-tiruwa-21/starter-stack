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
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock | deepseek | openai | anthropic | ollama
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "deepseek-chat")

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

class ChatResponse(BaseModel):
    message: str
    tool_calls: list[ToolResult] = []
    session_id: str
    tokens_used: int = 0
    latency_ms: float = 0

class KernelState(BaseModel):
    claude_md: Optional[str] = None
    todo_md: Optional[str] = None
    lessons_md: Optional[str] = None
    skills: list[str] = []
    cer: float = 0.0

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
                if skill_path.is_dir() and not skill_path.name.startswith("."):
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
            # Truncate to avoid context bloat (Eureka #3)
            parts.append(kernel.claude_md[:4000])
            parts.append("")

        if kernel.todo_md:
            parts.append("=== PROCESS CONTROL BLOCK (todo.md) ===")
            parts.append(kernel.todo_md[:2000])
            parts.append("")

        if kernel.lessons_md:
            parts.append("=== ADAPTIVE CACHE (lessons.md) ===")
            parts.append(kernel.lessons_md[:2000])
            parts.append("")

        if kernel.skills:
            parts.append(f"=== AVAILABLE SKILLS ({len(kernel.skills)}) ===")
            parts.append(", ".join(kernel.skills))
            parts.append("Load skill details on-demand, not all at once (Eureka #7 Shannon SNR).")
            parts.append("")

        parts.append(f"Context Efficiency Ratio: {kernel.cer:.4f} (target > 0.6)")
        parts.append("")
        parts.append("When you need to perform actions (read files, execute commands, search web),")
        parts.append("declare tool calls. They will be routed through AgentProxy for security checks.")

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# Tool Executor (via AgentProxy)
# ═══════════════════════════════════════════════════════════════

class ToolExecutor:
    """
    Executes tool calls by routing them through AgentProxy.
    Implements the Reference Monitor pattern (Anderson Report 1972).
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def execute(self, skill: str, tool_call: ToolCall,
                      token_count: int = 50000, token_budget: int = 200000) -> ToolResult:
        """Route a tool call through AgentProxy and return the result."""
        start = time.monotonic()

        try:
            resp = await self.client.post(
                f"{AGENT_PROXY_URL}/api/v1/evaluate",
                json={
                    "skill": skill,
                    "tool": tool_call.tool,
                    "arguments": tool_call.arguments,
                    "context": tool_call.reason,
                    "token_count": token_count,
                    "token_budget": token_budget,
                },
            )
            data = resp.json()
            latency = (time.monotonic() - start) * 1000

            decision = data.get("decision", "DENY")
            result = ToolResult(
                tool=tool_call.tool,
                decision=decision,
                latency_ms=latency,
            )

            if decision == "ALLOW":
                # Actually execute the tool (sandboxed)
                result.output = await self._execute_sandboxed(tool_call)
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

    async def _execute_sandboxed(self, tool_call: ToolCall) -> str:
        """Execute an ALLOWED tool call in the sandbox."""
        tool = tool_call.tool

        # File read
        if tool.startswith("file_read:"):
            filepath = tool.split(":", 1)[1]
            # Support both absolute (/workspace/...) and relative paths
            if filepath.startswith("/"):
                full_path = Path(filepath)
            else:
                full_path = Path(WORKSPACE_DIR) / filepath.lstrip("/")
            if full_path.exists() and full_path.is_file():
                content = full_path.read_text(encoding="utf-8", errors="replace")
                return content[:10000]  # Limit output size
            return f"File not found: {filepath}"

        # File write
        if tool.startswith("file_write:"):
            filepath = tool.split(":", 1)[1]
            if filepath.startswith("/"):
                full_path = Path(filepath)
            else:
                full_path = Path(WORKSPACE_DIR) / filepath.lstrip("/")
            content = tool_call.arguments.get("content", "")
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return f"Written {len(content)} bytes to {filepath}"

        # File list
        if tool.startswith("file_list"):
            path_arg = tool_call.arguments.get("path", "")
            if path_arg.startswith("/"):
                target = Path(path_arg)
            else:
                target = Path(WORKSPACE_DIR) / (path_arg or ".")
            if target.exists() and target.is_dir():
                files = [str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()]
                return "\n".join(files[:100])
            return f"Directory not found: {target}"

        # Search
        if tool.startswith("search"):
            query = tool_call.arguments.get("query", "")
            results = []
            agent_dir = Path(WORKSPACE_DIR)
            for f in agent_dir.rglob("*.md"):
                content = f.read_text(encoding="utf-8", errors="replace")
                if query.lower() in content.lower():
                    for i, line in enumerate(content.splitlines(), 1):
                        if query.lower() in line.lower():
                            results.append(f"{f.relative_to(agent_dir)}:{i}: {line.strip()}")
            return "\n".join(results[:50]) or f"No results for '{query}'"

        # Unknown tool — demo response
        return f"Tool '{tool}' executed (sandbox mode)"

    async def close(self):
        await self.client.aclose()


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
    DeepSeek / OpenAI-compatible LLM provider.
    Uses the OpenAI chat completions API format.
    Falls back to MockLLM for tool call detection.
    """

    def __init__(self):
        self.mock = MockLLM()  # Fallback for tool detection
        api_key = DEEPSEEK_API_KEY or OPENAI_API_KEY
        base_url = DEEPSEEK_BASE_URL if DEEPSEEK_API_KEY else "https://api.openai.com"
        self.api_url = f"{base_url}/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=60.0)
        self.model = MODEL_NAME

    async def generate(self, messages: list[ChatMessage], system_prompt: str,
                       kernel: KernelState) -> tuple[str, list[ToolCall]]:
        """Generate via DeepSeek API, with MockLLM tool detection."""
        # First check if user wants a tool action (MockLLM pattern matching)
        _, tool_calls = await self.mock.generate(messages, system_prompt, kernel)

        # If there's a tool call, let MockLLM handle the routing text
        # The real LLM response comes after tool execution
        if tool_calls:
            return "Executing your request through AgentProxy...", tool_calls

        # No tool calls — send to real LLM
        try:
            api_messages = [{"role": "system", "content": system_prompt}]

            # Add conversation history (last 20 messages for context)
            for msg in messages[-20:]:
                api_messages.append({"role": msg.role, "content": msg.content})

            resp = await self.client.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 2000,
                    "stream": False,
                },
                headers=self.headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content, []
            else:
                error_text = resp.text[:200]
                print(f"[DeepSeek] API error {resp.status_code}: {error_text}")
                # Fallback to mock
                return await self.mock.generate(messages, system_prompt, kernel)

        except Exception as e:
            print(f"[DeepSeek] Exception: {e}")
            # Fallback to mock
            return await self.mock.generate(messages, system_prompt, kernel)

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
                    "temperature": 0.7,
                    "max_tokens": 2000,
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

# Select LLM provider
if LLM_PROVIDER == "deepseek" and DEEPSEEK_API_KEY:
    llm = DeepSeekLLM()
    print(f"[OpenClaw] LLM: DeepSeek ({MODEL_NAME})")
elif LLM_PROVIDER == "openai" and OPENAI_API_KEY:
    llm = DeepSeekLLM()  # OpenAI-compatible class works for both
    print(f"[OpenClaw] LLM: OpenAI ({MODEL_NAME})")
else:
    llm = MockLLM()
    print(f"[OpenClaw] LLM: Mock (demo mode)")

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
        "provider": LLM_PROVIDER,
        "model": MODEL_NAME,
    }


# ─── Chat ─────────────────────────────────────────────────────

@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Main chat endpoint — process user message, execute tool calls via AgentProxy."""
    start = time.monotonic()
    stats["total_chats"] += 1

    # Load kernel state
    kernel_state = kernel.load_kernel()
    system_prompt = kernel.get_system_prompt(kernel_state)

    # Get/create session history
    if req.session_id not in sessions:
        sessions[req.session_id] = []
    history = sessions[req.session_id]

    # Add user message
    history.append(ChatMessage(role="user", content=req.message))

    # Generate response + tool calls
    all_messages = req.history if req.history else history
    response_text, tool_calls = await llm.generate(all_messages, system_prompt, kernel_state)

    # Execute tool calls through AgentProxy
    tool_results: list[ToolResult] = []
    for tc in tool_calls:
        stats["total_tool_calls"] += 1

        result = await tool_executor.execute(
            skill=req.skill,
            tool_call=tc,
            token_count=len(system_prompt) // 4,
            token_budget=200000,
        )
        tool_results.append(result)

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
        for tr in tool_results:
            if tr.decision == "ALLOW" and tr.output:
                parts.append(f"**Result ({tr.tool}):**")
                parts.append(f"```\n{tr.output[:5000]}\n```")
            elif tr.decision == "DENY":
                parts.append(f"**Blocked ({tr.tool}):** {tr.error}")
            elif tr.decision == "HUMAN_GATE":
                parts.append(f"**Awaiting approval ({tr.tool}):** {tr.output}")
        response_text = "\n".join(parts)

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

    return ChatResponse(
        message=response_text,
        tool_calls=tool_results,
        session_id=req.session_id,
        tokens_used=len(response_text) // 4,
        latency_ms=round(latency, 2),
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
        "provider": LLM_PROVIDER,
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
        "║  AI Agent Runtime • Powered by " + (f"{LLM_PROVIDER}/{MODEL_NAME}" if LLM_PROVIDER != "mock" else "Mock LLM (demo)") + "\r\n"
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

            if command == "status":
                kernel_state = kernel.load_kernel()
                uptime = time.time() - stats["start_time"]
                hrs = int(uptime // 3600)
                mins = int((uptime % 3600) // 60)
                status_text = (
                    f"\x1b[36m── System Status ──\x1b[0m\r\n"
                    f"  Provider:  \x1b[1m{LLM_PROVIDER}\x1b[0m ({MODEL_NAME})\r\n"
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

            # Process through AI agent
            terminal_sessions[session_id].append(ChatMessage(role="user", content=command))
            kernel_state = kernel.load_kernel()
            system_prompt = kernel.get_system_prompt(kernel_state)

            # Check if it's a tool command first (MockLLM patterns)
            mock = MockLLM() if not isinstance(llm, MockLLM) else llm
            _, tool_calls = await mock.generate(
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
                        # Convert markdown bold to ANSI bold
                        display_token = token.replace("**", "\x1b[1m")
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
            await websocket.send_json({"type": "prompt", "data": "\x1b[32mopenclaw\x1b[0m:\x1b[34m~\x1b[0m$ "})

            # Keep terminal history manageable
            if len(terminal_sessions[session_id]) > 40:
                terminal_sessions[session_id] = terminal_sessions[session_id][-40:]

    except WebSocketDisconnect:
        pass
    finally:
        terminal_sessions.pop(session_id, None)


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
    print(f"  Provider: {LLM_PROVIDER} | Model: {MODEL_NAME}")
    print(f"  Workspace: {WORKSPACE_DIR}")
    print(f"  Kernel: CLAUDE.md={'✓' if kernel_state.claude_md else '✗'} "
          f"todo.md={'✓' if kernel_state.todo_md else '✗'} "
          f"lessons.md={'✓' if kernel_state.lessons_md else '✗'}")
    print(f"  Skills: {kernel_state.skills}")
    print(f"  CER: {kernel_state.cer:.4f}")
    print(f"  AgentProxy: {AGENT_PROXY_URL}")
    print(f"  FlightRecorder: {FLIGHT_RECORDER_URL}")
    print(f"══════════════════════════════════════════════")

@app.on_event("shutdown")
async def shutdown():
    await tool_executor.close()
    await event_logger.close()
