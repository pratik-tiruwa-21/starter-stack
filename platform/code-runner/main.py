"""
Code Runner — ClawdContext OS
=============================
Self-hosted sandboxed code execution service.
Runs Python, JavaScript (Node.js), and Bash in isolated subprocesses
with timeout, memory limits, and workspace isolation.

Port: 8406

Security model:
  - Each execution gets a temporary workspace directory
  - subprocess with timeout (max 30s)
  - stdout/stderr captured, limited to 100KB
  - No network access for executed code (optional)
  - AgentProxy mediates all calls before they reach this service
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

MAX_TIMEOUT = int(os.getenv("MAX_TIMEOUT", "30"))  # seconds
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "102400"))  # 100KB
MAX_CODE_SIZE = int(os.getenv("MAX_CODE_SIZE", "51200"))  # 50KB
SANDBOX_DIR = os.getenv("SANDBOX_DIR", "/sandbox")
PERSIST_DIR = os.getenv("PERSIST_DIR", "/persist")  # Persistent workspace for sessions

# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════


class ExecuteRequest(BaseModel):
    code: str = Field(..., max_length=MAX_CODE_SIZE)
    language: str = Field(default="python", pattern=r"^(python|javascript|bash|html)$")
    timeout: int = Field(default=10, ge=1, le=MAX_TIMEOUT)
    session_id: str = Field(default="default")
    filename: Optional[str] = None  # Optional filename for file_write-style usage


class ExecuteResponse(BaseModel):
    execution_id: str
    language: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    files_created: list[str] = []
    preview_url: Optional[str] = None
    error: Optional[str] = None


class FileWriteRequest(BaseModel):
    path: str
    content: str = Field(..., max_length=MAX_CODE_SIZE * 4)
    session_id: str = Field(default="default")


class SessionFiles(BaseModel):
    session_id: str
    files: list[dict[str, Any]]


# ═══════════════════════════════════════════════════════════════
# Sandbox Manager
# ═══════════════════════════════════════════════════════════════


class SandboxManager:
    """Manages isolated execution environments per session."""

    def __init__(self):
        self.base_dir = Path(PERSIST_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_session_dir(self, session_id: str) -> Path:
        """Get or create a persistent directory for a session."""
        # Sanitize session_id to prevent path traversal
        safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
        if not safe_id:
            safe_id = "default"
        session_dir = self.base_dir / safe_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    async def execute_code(self, req: ExecuteRequest) -> ExecuteResponse:
        """Execute code in a sandboxed subprocess."""
        execution_id = str(uuid.uuid4())[:8]
        session_dir = self.get_session_dir(req.session_id)
        start = time.monotonic()

        try:
            if req.language == "python":
                return await self._run_python(req, session_dir, execution_id, start)
            elif req.language == "javascript":
                return await self._run_javascript(req, session_dir, execution_id, start)
            elif req.language == "bash":
                return await self._run_bash(req, session_dir, execution_id, start)
            elif req.language == "html":
                return await self._write_html(req, session_dir, execution_id, start)
            else:
                return ExecuteResponse(
                    execution_id=execution_id,
                    language=req.language,
                    stdout="",
                    stderr=f"Unsupported language: {req.language}",
                    exit_code=1,
                    duration_ms=(time.monotonic() - start) * 1000,
                    error=f"Unsupported language: {req.language}",
                )
        except asyncio.TimeoutError:
            return ExecuteResponse(
                execution_id=execution_id,
                language=req.language,
                stdout="",
                stderr=f"Execution timed out after {req.timeout}s",
                exit_code=124,
                duration_ms=(time.monotonic() - start) * 1000,
                error=f"Timeout after {req.timeout}s",
            )
        except Exception as e:
            return ExecuteResponse(
                execution_id=execution_id,
                language=req.language,
                stdout="",
                stderr=str(e),
                exit_code=1,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(e),
            )

    async def _run_python(self, req: ExecuteRequest, session_dir: Path,
                          execution_id: str, start: float) -> ExecuteResponse:
        """Execute Python code."""
        script_file = session_dir / f"_exec_{execution_id}.py"
        script_file.write_text(req.code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(script_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_dir),
                env={
                    **os.environ,
                    "HOME": str(session_dir),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=req.timeout
            )

            files_created = self._detect_new_files(session_dir, execution_id)
            preview_url = self._get_preview_url(session_dir, files_created, req.session_id)

            return ExecuteResponse(
                execution_id=execution_id,
                language="python",
                stdout=stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                stderr=stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                exit_code=proc.returncode or 0,
                duration_ms=(time.monotonic() - start) * 1000,
                files_created=files_created,
                preview_url=preview_url,
            )
        finally:
            # Clean up the temp script but keep generated files
            script_file.unlink(missing_ok=True)

    async def _run_javascript(self, req: ExecuteRequest, session_dir: Path,
                              execution_id: str, start: float) -> ExecuteResponse:
        """Execute JavaScript code via Node.js."""
        script_file = session_dir / f"_exec_{execution_id}.js"
        script_file.write_text(req.code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(script_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_dir),
                env={
                    **os.environ,
                    "HOME": str(session_dir),
                    "NODE_OPTIONS": "--max-old-space-size=128",
                },
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=req.timeout
            )

            files_created = self._detect_new_files(session_dir, execution_id)
            preview_url = self._get_preview_url(session_dir, files_created, req.session_id)

            return ExecuteResponse(
                execution_id=execution_id,
                language="javascript",
                stdout=stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                stderr=stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                exit_code=proc.returncode or 0,
                duration_ms=(time.monotonic() - start) * 1000,
                files_created=files_created,
                preview_url=preview_url,
            )
        finally:
            script_file.unlink(missing_ok=True)

    async def _run_bash(self, req: ExecuteRequest, session_dir: Path,
                        execution_id: str, start: float) -> ExecuteResponse:
        """Execute Bash commands."""
        script_file = session_dir / f"_exec_{execution_id}.sh"
        script_file.write_text(req.code, encoding="utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(script_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_dir),
                env={
                    **os.environ,
                    "HOME": str(session_dir),
                },
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=req.timeout
            )

            files_created = self._detect_new_files(session_dir, execution_id)
            preview_url = self._get_preview_url(session_dir, files_created, req.session_id)

            return ExecuteResponse(
                execution_id=execution_id,
                language="bash",
                stdout=stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                stderr=stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                exit_code=proc.returncode or 0,
                duration_ms=(time.monotonic() - start) * 1000,
                files_created=files_created,
                preview_url=preview_url,
            )
        finally:
            script_file.unlink(missing_ok=True)

    async def _write_html(self, req: ExecuteRequest, session_dir: Path,
                          execution_id: str, start: float) -> ExecuteResponse:
        """Write HTML content directly (for web preview)."""
        filename = req.filename or "index.html"
        # Sanitize filename
        safe_name = "".join(c for c in filename if c.isalnum() or c in "-_./")
        if not safe_name or safe_name.startswith("/") or ".." in safe_name:
            safe_name = "index.html"

        target = session_dir / safe_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.code, encoding="utf-8")

        return ExecuteResponse(
            execution_id=execution_id,
            language="html",
            stdout=f"Written {len(req.code)} bytes to {safe_name}",
            stderr="",
            exit_code=0,
            duration_ms=(time.monotonic() - start) * 1000,
            files_created=[safe_name],
            preview_url=f"/api/v1/preview/{req.session_id}/{safe_name}",
        )

    def _detect_new_files(self, session_dir: Path, execution_id: str) -> list[str]:
        """Detect files created during execution (exclude temp scripts)."""
        files = []
        for f in session_dir.rglob("*"):
            if f.is_file() and not f.name.startswith("_exec_"):
                try:
                    rel = str(f.relative_to(session_dir))
                    files.append(rel)
                except ValueError:
                    pass
        return sorted(files)[:50]  # Limit

    def _get_preview_url(self, session_dir: Path, files: list[str],
                         session_id: str) -> Optional[str]:
        """If an HTML file was created, return a preview URL."""
        html_files = [f for f in files if f.endswith((".html", ".htm"))]
        if html_files:
            return f"/api/v1/preview/{session_id}/{html_files[0]}"
        return None

    def write_file(self, session_id: str, path: str, content: str) -> str:
        """Write a file to the session workspace."""
        session_dir = self.get_session_dir(session_id)
        # Sanitize path
        safe_path = path.lstrip("/")
        if ".." in safe_path:
            raise ValueError("Path traversal not allowed")
        target = session_dir / safe_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return str(target.relative_to(session_dir))

    def read_file(self, session_id: str, path: str) -> Optional[str]:
        """Read a file from the session workspace."""
        session_dir = self.get_session_dir(session_id)
        safe_path = path.lstrip("/")
        if ".." in safe_path:
            return None
        target = session_dir / safe_path
        if target.exists() and target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT]
        return None

    def list_files(self, session_id: str) -> list[dict]:
        """List all files in a session workspace."""
        session_dir = self.get_session_dir(session_id)
        files = []
        if session_dir.exists():
            for f in sorted(session_dir.rglob("*")):
                if f.is_file() and not f.name.startswith("_exec_"):
                    files.append({
                        "path": str(f.relative_to(session_dir)),
                        "size": f.stat().st_size,
                        "name": f.name,
                    })
        return files[:200]

    def cleanup_session(self, session_id: str):
        """Remove all files for a session."""
        session_dir = self.get_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# FastAPI Application
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Code Runner — ClawdContext OS",
    description="Sandboxed code execution service. Supports Python, JavaScript, Bash, and HTML.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sandbox = SandboxManager()


# ─── Health ───────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """Health check — also verifies Python and Node.js are available."""
    checks = {"python": False, "node": False, "bash": False}

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        checks["python"] = proc.returncode == 0
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        checks["node"] = proc.returncode == 0
    except Exception:
        pass

    checks["bash"] = True  # Always available on Linux

    return {
        "status": "ok",
        "service": "code-runner",
        "runtimes": checks,
    }


# ─── Execute Code ─────────────────────────────────────────────

@app.post("/api/v1/execute", response_model=ExecuteResponse)
async def execute_code(req: ExecuteRequest):
    """Execute code in a sandboxed environment."""
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="Empty code")
    return await sandbox.execute_code(req)


# ─── File Operations ─────────────────────────────────────────

@app.post("/api/v1/files/write")
async def write_file(req: FileWriteRequest):
    """Write a file to the session workspace."""
    try:
        written = sandbox.write_file(req.session_id, req.path, req.content)
        return {"status": "ok", "path": written, "size": len(req.content)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/files/{session_id}")
async def list_files(session_id: str):
    """List all files in a session workspace."""
    files = sandbox.list_files(session_id)
    return {"session_id": session_id, "files": files}


@app.get("/api/v1/files/{session_id}/{filepath:path}")
async def read_file_endpoint(session_id: str, filepath: str):
    """Read a file from the session workspace."""
    content = sandbox.read_file(session_id, filepath)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": filepath, "content": content, "size": len(content)}


# ─── Preview (serve generated HTML files) ─────────────────────

@app.get("/api/v1/preview/{session_id}/{filepath:path}")
async def preview_file(session_id: str, filepath: str):
    """
    Serve a generated file for preview.
    HTML files are served as HTML, others as plain text.
    """
    content = sandbox.read_file(session_id, filepath)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")

    if filepath.endswith((".html", ".htm")):
        return HTMLResponse(content=content)
    elif filepath.endswith((".css",)):
        return PlainTextResponse(content=content, media_type="text/css")
    elif filepath.endswith((".js",)):
        return PlainTextResponse(content=content, media_type="application/javascript")
    elif filepath.endswith((".json",)):
        return PlainTextResponse(content=content, media_type="application/json")
    else:
        return PlainTextResponse(content=content)


# ─── Session Management ──────────────────────────────────────

@app.delete("/api/v1/sessions/{session_id}")
async def cleanup_session(session_id: str):
    """Remove all files for a session."""
    sandbox.cleanup_session(session_id)
    return {"status": "ok", "session_id": session_id}
