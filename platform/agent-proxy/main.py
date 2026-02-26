"""
ClawdContext OS — AgentProxy (Layer 4)
Reference Monitor: sits between LLM and every tool call.

Anderson Report (1972): complete mediation, tamper-proof, verifiable.
"""

from __future__ import annotations

import hashlib
import json
import time
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# ─── Config ────────────────────────────────────────────────────────

SKILLS_DIR = Path(os.getenv("CCOS_SKILLS_DIR", "/workspace/agent/skills"))
AUDIT_FILE = Path(os.getenv("CCOS_AUDIT_FILE", "/data/audit.jsonl"))
MAX_RATE = int(os.getenv("CCOS_MAX_RATE_RPM", "60"))
CER_WARN = float(os.getenv("CCOS_CER_WARN", "0.6"))
CER_CRIT = float(os.getenv("CCOS_CER_CRIT", "0.3"))

# TTP patterns from scanner
TTP_PATTERNS = {
    "CC-001": (r"curl.*\$.*(?:API_KEY|SECRET|TOKEN)", "data-exfiltration", "CRITICAL"),
    "CC-002": (r"eval.*base64|base64.*-d.*\|\s*sh", "obfuscated-eval", "CRITICAL"),
    "CC-003": (r"~/\.ssh/|~/\.aws/|\.env\.local", "credential-harvesting", "CRITICAL"),
    "CC-004": (r"(?i)ignore.*previous.*instruction|new.*primary.*directive", "prompt-injection", "CRITICAL"),
    "CC-005": (r"docker\.sock|nsenter.*--target|chroot.*/host", "container-escape", "CRITICAL"),
    "CC-006": (r"crontab.*curl|beacon|c2|callback", "persistence", "CRITICAL"),
    "CC-007": (r"npm.*install.*(?:optimzer|colros)|pip.*install.*(?:systm|reqeusts)", "supply-chain", "HIGH"),
    "CC-008": (r"file_(?:read|write):\*\*|net:\*|exec:\*", "wildcard-caps", "HIGH"),
    "CC-009": (r"signature:.*FORGED", "forged-signature", "HIGH"),
    "CC-010": (r"rate_limit:.*\d{5,}", "excessive-permissions", "HIGH"),
    "CC-011": (r"curl.*-s", "network-access", "MEDIUM"),
    "CC-012": (r"\.\./\.\.|/etc/(?:passwd|shadow)", "path-traversal", "MEDIUM"),
    "CC-013": (r"echo.*\$.*KEY", "info-disclosure", "MEDIUM"),
    "CC-014": (r"eval\(|exec\(", "code-execution", "LOW"),
}

# Human-in-the-loop gates — these actions always require approval
HUMAN_GATES = {
    "file_delete", "exec", "credential_access", "network_external",
    "container_exec", "database_drop", "key_rotation",
}


# ─── Models ────────────────────────────────────────────────────────

class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN_GATE = "HUMAN_GATE"


class ToolCallRequest(BaseModel):
    """Incoming tool call from LLM agent."""
    skill: str = Field(..., description="Skill name requesting the action")
    tool: str = Field(..., description="Tool being invoked (e.g. file_write)")
    arguments: dict = Field(default_factory=dict, description="Tool arguments")
    context: str = Field(default="", description="Surrounding prompt context")
    token_count: int = Field(default=0, description="Tokens used so far")
    token_budget: int = Field(default=200000, description="Total token budget")


class ProxyDecision(BaseModel):
    """AgentProxy verdict."""
    decision: Decision
    reason: str
    checks: dict = Field(default_factory=dict)
    latency_ms: float = 0.0
    audit_hash: str = ""


class AuditEntry(BaseModel):
    """Immutable audit log entry."""
    timestamp: str
    skill: str
    tool: str
    decision: str
    reason: str
    checks: dict
    prev_hash: str
    entry_hash: str


class SystemStatus(BaseModel):
    """OS system status."""
    uptime_seconds: float
    total_evaluations: int
    allowed: int
    denied: int
    human_gated: int
    cer_current: float
    layers: dict


# ─── Audit Logger (hash-chained) ──────────────────────────────────

class AuditLogger:
    def __init__(self, audit_file: Path):
        self.audit_file = audit_file
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        self.prev_hash = "GENESIS"
        self.entries: list[AuditEntry] = []
        self._load_existing()

    def _load_existing(self):
        if self.audit_file.exists():
            for line in self.audit_file.read_text().strip().split("\n"):
                if line:
                    try:
                        entry = json.loads(line)
                        self.prev_hash = entry.get("entry_hash", self.prev_hash)
                    except json.JSONDecodeError:
                        pass

    def log(self, skill: str, tool: str, decision: str, reason: str, checks: dict) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = f"{timestamp}|{skill}|{tool}|{decision}|{self.prev_hash}"
        entry_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]

        entry = AuditEntry(
            timestamp=timestamp,
            skill=skill,
            tool=tool,
            decision=decision,
            reason=reason,
            checks=checks,
            prev_hash=self.prev_hash,
            entry_hash=entry_hash,
        )

        self.entries.append(entry)
        with open(self.audit_file, "a") as f:
            f.write(json.dumps(entry.model_dump()) + "\n")

        self.prev_hash = entry_hash
        return entry_hash

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        return self.entries[-limit:]

    def verify_chain(self) -> tuple[bool, int]:
        """Verify hash chain integrity. Returns (valid, verified_count)."""
        if not self.entries:
            return True, 0
        prev = "GENESIS"
        for i, entry in enumerate(self.entries):
            if entry.prev_hash != prev:
                return False, i
            prev = entry.entry_hash
        return True, len(self.entries)


# ─── Capability Checker ───────────────────────────────────────────

class CapabilityChecker:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skill_caps: dict[str, set[str]] = {}
        self._load_skills()

    def _load_skills(self):
        if not self.skills_dir.exists():
            return
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            caps = self._parse_capabilities(skill_file)
            self.skill_caps[name] = caps

    def _parse_capabilities(self, path: Path) -> set[str]:
        text = path.read_text()
        caps: set[str] = set()
        in_frontmatter = False
        for line in text.split("\n"):
            if line.strip() == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter and line.strip().startswith("- "):
                cap = line.strip().lstrip("- ").strip()
                if cap and not cap.startswith("#"):
                    caps.add(cap)
        return caps

    def check(self, skill: str, tool: str) -> tuple[bool, str]:
        if skill.startswith("_"):
            return False, f"Blocked: skill '{skill}' has unsafe prefix"
        if skill not in self.skill_caps:
            return False, f"Unknown skill: '{skill}' not registered"
        caps = self.skill_caps[skill]
        # Check exact match
        if tool in caps:
            return True, f"Capability '{tool}' granted to '{skill}'"
        # Check wildcard match (e.g., file_read:/workspace/** matches file_read:/workspace/agent/foo.txt)
        for cap in caps:
            if ":" in cap:
                cap_name, cap_scope = cap.split(":", 1)
                if ":" in tool:
                    tool_name, tool_scope = tool.split(":", 1)
                    if cap_name == tool_name and cap_scope.endswith("*"):
                        prefix = cap_scope.rstrip("*")  # /workspace/** → /workspace/
                        if tool_scope.startswith(prefix):
                            return True, f"Wildcard capability match: {cap}"
        return False, f"Capability '{tool}' not granted to skill '{skill}'"

    def reload(self):
        self.skill_caps.clear()
        self._load_skills()


# ─── Semantic Scanner ─────────────────────────────────────────────

class SemanticScanner:
    """Scans tool arguments and context for TTP patterns."""

    def scan(self, content: str) -> list[dict]:
        findings = []
        for ttp_id, (pattern, name, severity) in TTP_PATTERNS.items():
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                findings.append({
                    "id": ttp_id,
                    "name": name,
                    "severity": severity,
                    "matches": len(matches),
                })
        return findings

    def scan_request(self, req: ToolCallRequest) -> list[dict]:
        # Scan arguments and context
        content = json.dumps(req.arguments) + "\n" + req.context
        return self.scan(content)


# ─── Rate Limiter ─────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, max_rpm: int = 60):
        self.max_rpm = max_rpm
        self.window: list[float] = []

    def check(self) -> tuple[bool, str]:
        now = time.time()
        cutoff = now - 60
        self.window = [t for t in self.window if t > cutoff]
        if len(self.window) >= self.max_rpm:
            return False, f"Rate limit exceeded: {len(self.window)}/{self.max_rpm} rpm"
        self.window.append(now)
        return True, f"Rate OK: {len(self.window)}/{self.max_rpm} rpm"


# ─── AgentProxy Orchestrator ─────────────────────────────────────

class AgentProxy:
    def __init__(self):
        self.capability_checker = CapabilityChecker(SKILLS_DIR)
        self.scanner = SemanticScanner()
        self.rate_limiter = RateLimiter(MAX_RATE)
        self.audit = AuditLogger(AUDIT_FILE)
        self.start_time = time.time()
        self.stats = {"total": 0, "allowed": 0, "denied": 0, "human_gated": 0}
        self.cer_current = 1.0
        self.websocket_clients: list[WebSocket] = []

    def evaluate(self, req: ToolCallRequest) -> ProxyDecision:
        start = time.time()
        checks: dict = {}
        self.stats["total"] += 1

        # 1. Rate limit
        rate_ok, rate_msg = self.rate_limiter.check()
        checks["rate_limit"] = {"passed": rate_ok, "detail": rate_msg}
        if not rate_ok:
            return self._deny(req, rate_msg, checks, start)

        # 2. Human gate check
        if req.tool in HUMAN_GATES:
            self.stats["human_gated"] += 1
            checks["human_gate"] = {"passed": False, "detail": f"Tool '{req.tool}' requires human approval"}
            decision = ProxyDecision(
                decision=Decision.HUMAN_GATE,
                reason=f"Tool '{req.tool}' requires human-in-the-loop approval",
                checks=checks,
                latency_ms=(time.time() - start) * 1000,
            )
            decision.audit_hash = self.audit.log(req.skill, req.tool, "HUMAN_GATE", decision.reason, checks)
            return decision

        # 3. Capability check
        cap_ok, cap_msg = self.capability_checker.check(req.skill, req.tool)
        checks["capability"] = {"passed": cap_ok, "detail": cap_msg}
        if not cap_ok:
            return self._deny(req, cap_msg, checks, start)

        # 4. Semantic scan
        findings = self.scanner.scan_request(req)
        has_critical = any(f["severity"] == "CRITICAL" for f in findings)
        checks["semantic_scan"] = {
            "passed": not has_critical,
            "findings": findings,
            "detail": f"{len(findings)} finding(s)" if findings else "Clean",
        }
        if has_critical:
            return self._deny(req, f"CRITICAL TTP detected: {findings[0]['name']}", checks, start)

        # 5. CER check
        if req.token_budget > 0:
            self.cer_current = max(0, 1 - (req.token_count / req.token_budget))
        checks["cer"] = {
            "passed": self.cer_current > CER_CRIT,
            "value": round(self.cer_current, 3),
            "detail": f"CER={self.cer_current:.3f} (crit={CER_CRIT})",
        }
        if self.cer_current <= CER_CRIT:
            return self._deny(req, f"CER critical: {self.cer_current:.3f}", checks, start)

        # All checks passed
        self.stats["allowed"] += 1
        decision = ProxyDecision(
            decision=Decision.ALLOW,
            reason="All security gates passed",
            checks=checks,
            latency_ms=(time.time() - start) * 1000,
        )
        decision.audit_hash = self.audit.log(req.skill, req.tool, "ALLOW", decision.reason, checks)
        return decision

    def _deny(self, req: ToolCallRequest, reason: str, checks: dict, start: float) -> ProxyDecision:
        self.stats["denied"] += 1
        decision = ProxyDecision(
            decision=Decision.DENY,
            reason=reason,
            checks=checks,
            latency_ms=(time.time() - start) * 1000,
        )
        decision.audit_hash = self.audit.log(req.skill, req.tool, "DENY", reason, checks)
        return decision

    def get_status(self) -> SystemStatus:
        return SystemStatus(
            uptime_seconds=time.time() - self.start_time,
            total_evaluations=self.stats["total"],
            allowed=self.stats["allowed"],
            denied=self.stats["denied"],
            human_gated=self.stats["human_gated"],
            cer_current=round(self.cer_current, 3),
            layers={
                "layer1_scanner": True,
                "layer2_clawdsign": True,
                "layer3_sandbox": True,
                "layer4_proxy": True,
                "layer5_recorder": True,
                "layer6_snapshot": False,
            },
        )


# ─── FastAPI App ──────────────────────────────────────────────────

app = FastAPI(
    title="ClawdContext OS — AgentProxy",
    description="Layer 4 Reference Monitor: complete mediation of all tool calls",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

proxy = AgentProxy()


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "agent-proxy", "layer": 4}


@app.get("/api/v1/status")
async def status():
    return proxy.get_status()


@app.post("/api/v1/evaluate", response_model=ProxyDecision)
async def evaluate(req: ToolCallRequest):
    decision = proxy.evaluate(req)
    # Broadcast to WebSocket clients
    event = {
        "type": "evaluation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skill": req.skill,
        "tool": req.tool,
        "decision": decision.decision.value,
        "reason": decision.reason,
        "latency_ms": decision.latency_ms,
    }
    for ws in proxy.websocket_clients[:]:
        try:
            await ws.send_json(event)
        except Exception:
            proxy.websocket_clients.remove(ws)
    return decision


@app.get("/api/v1/audit")
async def audit(limit: int = 50):
    return {"entries": [e.model_dump() for e in proxy.audit.recent(limit)]}


@app.get("/api/v1/audit/verify")
async def verify_audit():
    valid, count = proxy.audit.verify_chain()
    return {"valid": valid, "verified_entries": count}


@app.post("/api/v1/scan")
async def scan(content: str = ""):
    findings = proxy.scanner.scan(content)
    return {"findings": findings, "count": len(findings)}


@app.get("/api/v1/skills")
async def list_skills():
    return {
        "skills": {
            name: list(caps)
            for name, caps in proxy.capability_checker.skill_caps.items()
        }
    }


@app.post("/api/v1/skills/reload")
async def reload_skills():
    proxy.capability_checker.reload()
    return {"status": "reloaded", "count": len(proxy.capability_checker.skill_caps)}


@app.get("/api/v1/patterns")
async def list_patterns():
    return {
        "patterns": {
            ttp_id: {"pattern": p, "name": n, "severity": s}
            for ttp_id, (p, n, s) in TTP_PATTERNS.items()
        }
    }


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    proxy.websocket_clients.append(websocket)
    try:
        # Send initial status
        await websocket.send_json({
            "type": "connected",
            "status": proxy.get_status().model_dump(),
        })
        # Keep alive
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
            elif data == "status":
                await websocket.send_json({
                    "type": "status",
                    "status": proxy.get_status().model_dump(),
                })
    except WebSocketDisconnect:
        proxy.websocket_clients.remove(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8400)
