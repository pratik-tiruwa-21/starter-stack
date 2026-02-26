"""
ClawdContext OS — Scanner API (Layer 1)
HTTP wrapper around the TTP pattern scanner.
"""

from __future__ import annotations

import re
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# ─── TTP Patterns ─────────────────────────────────────────────────

PATTERNS: list[dict] = [
    {"id": "CC-001", "severity": "CRITICAL", "name": "data-exfiltration",
     "regex": r"curl.*\$.*(?:API_KEY|SECRET|TOKEN|OPENAI|AWS)"},
    {"id": "CC-001", "severity": "CRITICAL", "name": "data-exfiltration",
     "regex": r"wget.*\$.*(?:API_KEY|SECRET|TOKEN)"},
    {"id": "CC-002", "severity": "CRITICAL", "name": "obfuscated-eval",
     "regex": r"eval.*base64|base64.*-d.*\|\s*sh"},
    {"id": "CC-003", "severity": "CRITICAL", "name": "credential-harvesting",
     "regex": r"~/\.ssh/(?:id_rsa|id_ed25519)|~/\.aws/(?:credentials|config)|\.env(?:\.local|\.production)"},
    {"id": "CC-004", "severity": "CRITICAL", "name": "prompt-injection",
     "regex": r"(?i)ignore.*previous.*instructions?|new.*primary.*directive|disable.*safety"},
    {"id": "CC-005", "severity": "CRITICAL", "name": "container-escape",
     "regex": r"docker\.sock|nsenter.*--target.*1|chroot.*/host|mount.*-t.*cgroup"},
    {"id": "CC-006", "severity": "CRITICAL", "name": "persistence",
     "regex": r"crontab.*curl|beacon|c2|callback"},
    {"id": "CC-007", "severity": "HIGH", "name": "supply-chain-confusion",
     "regex": r"npm.*install.*(?:optimzer|colros|loadsh)|pip.*install.*(?:systm|reqeusts)"},
    {"id": "CC-008", "severity": "HIGH", "name": "wildcard-capabilities",
     "regex": r"file_(?:read|write):\*\*|net:\*|exec:\*"},
    {"id": "CC-009", "severity": "HIGH", "name": "forged-signatures",
     "regex": r"signature:.*FORGED"},
    {"id": "CC-010", "severity": "HIGH", "name": "excessive-permissions",
     "regex": r"rate_limit:.*\d{5,}"},
    {"id": "CC-011", "severity": "MEDIUM", "name": "network-access",
     "regex": r"curl.*-s"},
    {"id": "CC-012", "severity": "MEDIUM", "name": "file-system-traversal",
     "regex": r"\.\./\.\.|/etc/(?:passwd|shadow)"},
    {"id": "CC-013", "severity": "MEDIUM", "name": "information-disclosure",
     "regex": r"echo.*\$.*KEY"},
    {"id": "CC-014", "severity": "LOW", "name": "code-execution",
     "regex": r"eval\(|exec\("},
]


# ─── Models ────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    content: str = Field(..., description="Content to scan for TTP patterns")
    source: str = Field(default="unknown", description="Source file or identifier")


class Finding(BaseModel):
    id: str
    severity: str
    name: str
    line: int = 0
    match: str = ""


class ScanResult(BaseModel):
    source: str
    timestamp: str
    findings: list[Finding]
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    verdict: str = "PASS"
    content_hash: str = ""


class CERRequest(BaseModel):
    kernel_tokens: int = Field(default=0, description="Tokens in CLAUDE.md + AGENTS.md")
    skill_tokens: int = Field(default=0, description="Tokens in loaded skills")
    lesson_tokens: int = Field(default=0, description="Tokens in lessons.md")
    todo_tokens: int = Field(default=0, description="Tokens in todo.md")


class CERResult(BaseModel):
    total_tokens: int
    useful_tokens: int
    governance_tokens: int
    cer: float
    status: str
    recommendation: str


# ─── Scanner Engine ───────────────────────────────────────────────

def scan_content(content: str, source: str = "unknown") -> ScanResult:
    findings: list[Finding] = []
    lines = content.split("\n")

    for pattern in PATTERNS:
        for i, line in enumerate(lines, 1):
            matches = re.findall(pattern["regex"], line, re.IGNORECASE)
            if matches:
                findings.append(Finding(
                    id=pattern["id"],
                    severity=pattern["severity"],
                    name=pattern["name"],
                    line=i,
                    match=line.strip()[:120],
                ))

    critical = sum(1 for f in findings if f.severity == "CRITICAL")
    high = sum(1 for f in findings if f.severity == "HIGH")
    medium = sum(1 for f in findings if f.severity == "MEDIUM")
    low = sum(1 for f in findings if f.severity == "LOW")

    verdict = "FAIL" if critical > 0 or high > 3 else "PASS"
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    return ScanResult(
        source=source,
        timestamp=datetime.now(timezone.utc).isoformat(),
        findings=findings,
        critical=critical,
        high=high,
        medium=medium,
        low=low,
        verdict=verdict,
        content_hash=content_hash,
    )


def calculate_cer(req: CERRequest) -> CERResult:
    governance = req.kernel_tokens + req.lesson_tokens + req.todo_tokens
    useful = req.skill_tokens
    total = governance + useful

    if total == 0:
        cer = 0.0
    else:
        cer = useful / total

    if cer >= 0.6:
        status = "HEALTHY"
        rec = "Context budget is well-balanced."
    elif cer >= 0.3:
        status = "WARNING"
        rec = "Consider unloading idle skills or pruning lessons.md."
    else:
        status = "CRITICAL"
        rec = "Governance overhead dominates. Prune lessons, compress todo, unload skills."

    return CERResult(
        total_tokens=total,
        useful_tokens=useful,
        governance_tokens=governance,
        cer=round(cer, 3),
        status=status,
        recommendation=rec,
    )


# ─── FastAPI App ──────────────────────────────────────────────────

app = FastAPI(
    title="ClawdContext OS — Scanner API",
    description="Layer 1: Design-Time TTP Detection as a Service",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "scanner-api", "layer": 1}


@app.post("/api/v1/scan", response_model=ScanResult)
async def scan(req: ScanRequest):
    return scan_content(req.content, req.source)


@app.post("/api/v1/scan/file")
async def scan_file(file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8", errors="replace")
    return scan_content(content, file.filename or "upload")


@app.get("/api/v1/patterns")
async def get_patterns():
    return {
        "count": len(PATTERNS),
        "patterns": [
            {"id": p["id"], "severity": p["severity"], "name": p["name"]}
            for p in PATTERNS
        ],
    }


@app.post("/api/v1/cer", response_model=CERResult)
async def cer(req: CERRequest):
    return calculate_cer(req)


@app.post("/api/v1/scan/workspace")
async def scan_workspace():
    """Scan all agent files in the mounted workspace."""
    workspace = Path(os.getenv("CCOS_WORKSPACE", "/workspace"))
    results = []
    for ext in ("*.md", "*.yaml", "*.yml", "*.json", "*.sh", "*.py"):
        for f in workspace.rglob(ext):
            try:
                content = f.read_text(errors="replace")
                result = scan_content(content, str(f.relative_to(workspace)))
                if result.findings:
                    results.append(result.model_dump())
            except Exception:
                pass
    total_findings = sum(r["critical"] + r["high"] + r["medium"] + r["low"] for r in results)
    return {
        "files_scanned": len(results),
        "total_findings": total_findings,
        "results": results,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8401)
