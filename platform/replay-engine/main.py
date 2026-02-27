"""
ClawdContext OS — ReplayEngine (Layer 5→6 Bridge)
A VCR for AI agents: replay, branch, diff, and rollback agent sessions.

Every agent action is captured as a timeline node. Sessions become replayable,
branchable timelines — like git for agent behavior.

Architecture:
  OpenClaw → FlightRecorder → ReplayEngine
                                    ↓
                              Dashboard "Replay" tab

Port: 8404
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx
import uvicorn


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

FLIGHT_RECORDER_URL = os.getenv("FLIGHT_RECORDER_URL", "http://flight-recorder:8402")
DATA_DIR = Path(os.getenv("REPLAY_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TIMELINES_FILE = DATA_DIR / "timelines.jsonl"
SNAPSHOTS_FILE = DATA_DIR / "snapshots.jsonl"


# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════

class TimelineNode(BaseModel):
    """A single point on the agent timeline."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timeline_id: str
    sequence: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str  # chat | tool_call | terminal_cmd | branch_point
    actor: str = "openclaw"  # which agent
    data: dict = Field(default_factory=dict)
    # Snapshot of kernel state at this point
    kernel_snapshot: Optional[dict] = None
    # AgentProxy decision if tool call
    proxy_decision: Optional[str] = None  # ALLOW | DENY | HUMAN_GATE
    # Parent node (for branching)
    parent_id: Optional[str] = None
    # Hash for integrity
    node_hash: str = ""


class Timeline(BaseModel):
    """A complete agent session timeline."""
    id: str = Field(default_factory=lambda: f"tl-{uuid.uuid4().hex[:8]}")
    name: str = ""
    session_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""
    node_count: int = 0
    branch_count: int = 0
    parent_timeline: Optional[str] = None
    branch_point_node: Optional[str] = None
    status: str = "active"  # active | completed | archived


class KernelSnapshot(BaseModel):
    """Frozen kernel state at a point in time."""
    id: str = Field(default_factory=lambda: f"snap-{uuid.uuid4().hex[:8]}")
    timeline_id: str
    node_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cer: float = 0.0
    skills: list[str] = []
    claude_md_hash: str = ""
    todo_md_hash: str = ""
    lessons_md_hash: str = ""
    claude_md_size: int = 0
    todo_md_size: int = 0
    lessons_md_size: int = 0
    # Session context at this point
    message_count: int = 0
    token_estimate: int = 0


class BranchRequest(BaseModel):
    """Request to branch from a node."""
    source_timeline_id: str
    branch_from_node_id: str
    name: str = ""


class DiffResult(BaseModel):
    """Diff between two timeline branches."""
    timeline_a: str
    timeline_b: str
    divergence_node: Optional[str] = None
    shared_nodes: int = 0
    unique_a: int = 0
    unique_b: int = 0
    decision_changes: list[dict] = []  # nodes where proxy decisions differed
    cer_comparison: list[dict] = []


class ReplayStep(BaseModel):
    """A step in replay playback."""
    node: TimelineNode
    snapshot: Optional[KernelSnapshot] = None
    step_number: int
    total_steps: int
    is_branch_point: bool = False


# ═══════════════════════════════════════════════════════════════
# ReplayEngine Core
# ═══════════════════════════════════════════════════════════════

class ReplayEngine:
    def __init__(self):
        self.timelines: dict[str, Timeline] = {}
        self.nodes: dict[str, list[TimelineNode]] = defaultdict(list)  # timeline_id -> nodes
        self.snapshots: dict[str, KernelSnapshot] = {}  # node_id -> snapshot
        self.ws_clients: list[WebSocket] = []
        self.prev_hash = "GENESIS"
        self._load()

    def _load(self):
        """Load persisted timelines and snapshots."""
        if TIMELINES_FILE.exists():
            for line in TIMELINES_FILE.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("_type") == "timeline":
                        tl = Timeline(**{k: v for k, v in d.items() if k != "_type"})
                        self.timelines[tl.id] = tl
                    elif d.get("_type") == "node":
                        node = TimelineNode(**{k: v for k, v in d.items() if k != "_type"})
                        self.nodes[node.timeline_id].append(node)
                        self.prev_hash = node.node_hash or self.prev_hash
                except (json.JSONDecodeError, Exception):
                    pass

        if SNAPSHOTS_FILE.exists():
            for line in SNAPSHOTS_FILE.read_text().strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    snap = KernelSnapshot(**d)
                    self.snapshots[snap.node_id] = snap
                except (json.JSONDecodeError, Exception):
                    pass

    def _persist_timeline(self, tl: Timeline):
        with open(TIMELINES_FILE, "a") as f:
            f.write(json.dumps({"_type": "timeline", **tl.model_dump()}) + "\n")

    def _persist_node(self, node: TimelineNode):
        with open(TIMELINES_FILE, "a") as f:
            f.write(json.dumps({"_type": "node", **node.model_dump()}) + "\n")

    def _persist_snapshot(self, snap: KernelSnapshot):
        with open(SNAPSHOTS_FILE, "a") as f:
            f.write(json.dumps(snap.model_dump()) + "\n")

    def _hash_node(self, node: TimelineNode) -> str:
        payload = f"{node.id}|{node.timestamp}|{node.event_type}|{self.prev_hash}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    # ─── Timeline Management ──────────────────────────

    def create_timeline(self, session_id: str, name: str = "") -> Timeline:
        tl = Timeline(
            session_id=session_id,
            name=name or f"Session {session_id}",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.timelines[tl.id] = tl
        self._persist_timeline(tl)
        return tl

    def get_or_create_timeline(self, session_id: str) -> Timeline:
        """Find active timeline for session, or create one."""
        for tl in self.timelines.values():
            if tl.session_id == session_id and tl.status == "active":
                return tl
        return self.create_timeline(session_id)

    def list_timelines(self, limit: int = 50) -> list[Timeline]:
        tls = sorted(self.timelines.values(), key=lambda t: t.updated_at, reverse=True)
        return tls[:limit]

    # ─── Node Recording ───────────────────────────────

    def record_node(
        self,
        session_id: str,
        event_type: str,
        data: dict,
        kernel_snapshot: Optional[dict] = None,
        proxy_decision: Optional[str] = None,
        actor: str = "openclaw",
    ) -> TimelineNode:
        """Record an event as a timeline node."""
        tl = self.get_or_create_timeline(session_id)
        nodes = self.nodes[tl.id]
        seq = len(nodes)

        node = TimelineNode(
            timeline_id=tl.id,
            sequence=seq,
            event_type=event_type,
            actor=actor,
            data=data,
            kernel_snapshot=kernel_snapshot,
            proxy_decision=proxy_decision,
            parent_id=nodes[-1].id if nodes else None,
        )
        node.node_hash = self._hash_node(node)
        self.prev_hash = node.node_hash

        nodes.append(node)
        tl.node_count = len(nodes)
        tl.updated_at = node.timestamp

        self._persist_node(node)
        return node

    def record_snapshot(
        self,
        timeline_id: str,
        node_id: str,
        cer: float,
        skills: list[str],
        claude_md_hash: str = "",
        todo_md_hash: str = "",
        lessons_md_hash: str = "",
        claude_md_size: int = 0,
        todo_md_size: int = 0,
        lessons_md_size: int = 0,
        message_count: int = 0,
        token_estimate: int = 0,
    ) -> KernelSnapshot:
        snap = KernelSnapshot(
            timeline_id=timeline_id,
            node_id=node_id,
            cer=cer,
            skills=skills,
            claude_md_hash=claude_md_hash,
            todo_md_hash=todo_md_hash,
            lessons_md_hash=lessons_md_hash,
            claude_md_size=claude_md_size,
            todo_md_size=todo_md_size,
            lessons_md_size=lessons_md_size,
            message_count=message_count,
            token_estimate=token_estimate,
        )
        self.snapshots[node_id] = snap
        self._persist_snapshot(snap)
        return snap

    # ─── Replay ───────────────────────────────────────

    def get_timeline_nodes(self, timeline_id: str, start: int = 0, limit: int = 200) -> list[TimelineNode]:
        nodes = self.nodes.get(timeline_id, [])
        return nodes[start:start + limit]

    def get_replay_steps(self, timeline_id: str) -> list[ReplayStep]:
        """Get full replay sequence with snapshots."""
        nodes = self.nodes.get(timeline_id, [])
        total = len(nodes)
        steps = []
        for i, node in enumerate(nodes):
            snap = self.snapshots.get(node.id)
            # Check if this node has branches
            is_branch = any(
                tl.branch_point_node == node.id
                for tl in self.timelines.values()
            )
            steps.append(ReplayStep(
                node=node,
                snapshot=snap,
                step_number=i,
                total_steps=total,
                is_branch_point=is_branch,
            ))
        return steps

    # ─── Branching ────────────────────────────────────

    def branch_from(self, source_timeline_id: str, branch_node_id: str, name: str = "") -> Timeline:
        """Create a new timeline branching from a specific node."""
        source_tl = self.timelines.get(source_timeline_id)
        if not source_tl:
            raise ValueError(f"Timeline {source_timeline_id} not found")

        source_nodes = self.nodes.get(source_timeline_id, [])
        branch_idx = None
        for i, node in enumerate(source_nodes):
            if node.id == branch_node_id:
                branch_idx = i
                break
        if branch_idx is None:
            raise ValueError(f"Node {branch_node_id} not found in timeline {source_timeline_id}")

        # Create new timeline
        branch_tl = Timeline(
            session_id=f"branch-{uuid.uuid4().hex[:6]}",
            name=name or f"Branch from {source_tl.name} @ step {branch_idx}",
            parent_timeline=source_timeline_id,
            branch_point_node=branch_node_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.timelines[branch_tl.id] = branch_tl
        self._persist_timeline(branch_tl)

        # Copy nodes up to branch point
        for node in source_nodes[:branch_idx + 1]:
            copied = TimelineNode(
                timeline_id=branch_tl.id,
                sequence=node.sequence,
                timestamp=node.timestamp,
                event_type=node.event_type,
                actor=node.actor,
                data=node.data,
                kernel_snapshot=node.kernel_snapshot,
                proxy_decision=node.proxy_decision,
                parent_id=node.parent_id,
            )
            copied.node_hash = self._hash_node(copied)
            self.nodes[branch_tl.id].append(copied)
            self._persist_node(copied)

            # Copy snapshot if exists
            if node.id in self.snapshots:
                orig_snap = self.snapshots[node.id]
                new_snap = KernelSnapshot(
                    timeline_id=branch_tl.id,
                    node_id=copied.id,
                    cer=orig_snap.cer,
                    skills=orig_snap.skills,
                    claude_md_hash=orig_snap.claude_md_hash,
                    todo_md_hash=orig_snap.todo_md_hash,
                    lessons_md_hash=orig_snap.lessons_md_hash,
                    claude_md_size=orig_snap.claude_md_size,
                    todo_md_size=orig_snap.todo_md_size,
                    lessons_md_size=orig_snap.lessons_md_size,
                    message_count=orig_snap.message_count,
                    token_estimate=orig_snap.token_estimate,
                )
                self.snapshots[copied.id] = new_snap
                self._persist_snapshot(new_snap)

        branch_tl.node_count = len(self.nodes[branch_tl.id])
        source_tl.branch_count += 1

        return branch_tl

    # ─── Diff ─────────────────────────────────────────

    def diff_timelines(self, timeline_a_id: str, timeline_b_id: str) -> DiffResult:
        """Compare two timelines — find divergence and decision differences."""
        nodes_a = self.nodes.get(timeline_a_id, [])
        nodes_b = self.nodes.get(timeline_b_id, [])

        # Find divergence point (where data stops matching)
        shared = 0
        divergence_node = None
        for na, nb in zip(nodes_a, nodes_b):
            if na.event_type == nb.event_type and na.data == nb.data:
                shared += 1
            else:
                divergence_node = na.id
                break

        unique_a = len(nodes_a) - shared
        unique_b = len(nodes_b) - shared

        # Find decision changes
        decision_changes = []
        for na, nb in zip(nodes_a, nodes_b):
            if na.proxy_decision and nb.proxy_decision and na.proxy_decision != nb.proxy_decision:
                decision_changes.append({
                    "sequence": na.sequence,
                    "event": na.event_type,
                    "tool": na.data.get("tool", ""),
                    "decision_a": na.proxy_decision,
                    "decision_b": nb.proxy_decision,
                })

        # CER comparison
        cer_comparison = []
        for na, nb in zip(nodes_a, nodes_b):
            snap_a = self.snapshots.get(na.id)
            snap_b = self.snapshots.get(nb.id)
            if snap_a and snap_b:
                cer_comparison.append({
                    "sequence": na.sequence,
                    "cer_a": snap_a.cer,
                    "cer_b": snap_b.cer,
                    "delta": round(snap_a.cer - snap_b.cer, 4),
                })

        return DiffResult(
            timeline_a=timeline_a_id,
            timeline_b=timeline_b_id,
            divergence_node=divergence_node,
            shared_nodes=shared,
            unique_a=unique_a,
            unique_b=unique_b,
            decision_changes=decision_changes,
            cer_comparison=cer_comparison,
        )

    # ─── Stats ────────────────────────────────────────

    def get_stats(self) -> dict:
        total_nodes = sum(len(nodes) for nodes in self.nodes.values())
        total_snapshots = len(self.snapshots)
        total_branches = sum(1 for tl in self.timelines.values() if tl.parent_timeline)
        return {
            "total_timelines": len(self.timelines),
            "total_nodes": total_nodes,
            "total_snapshots": total_snapshots,
            "total_branches": total_branches,
            "active_timelines": sum(1 for tl in self.timelines.values() if tl.status == "active"),
        }


# ═══════════════════════════════════════════════════════════════
# FastAPI Application
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="ClawdContext OS — ReplayEngine",
    description="Layer 5→6 Bridge: Replay, branch, and diff agent sessions",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ReplayEngine()


@app.get("/healthz")
async def health():
    stats = engine.get_stats()
    return {
        "status": "ok",
        "service": "replay-engine",
        "layer": "5-6",
        "timelines": stats["total_timelines"],
        "nodes": stats["total_nodes"],
    }


# ─── Ingest (called by OpenClaw) ─────────────────────────────

@app.post("/api/v1/record")
async def record_event(
    session_id: str = "default",
    event_type: str = "chat",
    actor: str = "openclaw",
    data: dict = {},
    kernel_snapshot: Optional[dict] = None,
    proxy_decision: Optional[str] = None,
):
    """Record an agent event as a timeline node. Called by OpenClaw on every action."""
    node = engine.record_node(
        session_id=session_id,
        event_type=event_type,
        data=data,
        kernel_snapshot=kernel_snapshot,
        proxy_decision=proxy_decision,
        actor=actor,
    )

    # Broadcast to WebSocket clients
    for ws in engine.ws_clients[:]:
        try:
            await ws.send_json({
                "type": "node",
                "timeline_id": node.timeline_id,
                "node": node.model_dump(),
            })
        except Exception:
            engine.ws_clients.remove(ws)

    return {"node_id": node.id, "timeline_id": node.timeline_id, "sequence": node.sequence}


class RecordRequest(BaseModel):
    session_id: str = "default"
    event_type: str = "chat"
    actor: str = "openclaw"
    data: dict = Field(default_factory=dict)
    kernel_snapshot: Optional[dict] = None
    proxy_decision: Optional[str] = None


@app.post("/api/v1/record/event")
async def record_event_body(req: RecordRequest):
    """Record via JSON body (preferred)."""
    node = engine.record_node(
        session_id=req.session_id,
        event_type=req.event_type,
        data=req.data,
        kernel_snapshot=req.kernel_snapshot,
        proxy_decision=req.proxy_decision,
        actor=req.actor,
    )

    for ws in engine.ws_clients[:]:
        try:
            await ws.send_json({
                "type": "node",
                "timeline_id": node.timeline_id,
                "node": node.model_dump(),
            })
        except Exception:
            engine.ws_clients.remove(ws)

    return {"node_id": node.id, "timeline_id": node.timeline_id, "sequence": node.sequence}


@app.post("/api/v1/snapshot")
async def record_snapshot_endpoint(
    timeline_id: str,
    node_id: str,
    cer: float = 0.0,
    skills: list[str] = [],
    claude_md_hash: str = "",
    todo_md_hash: str = "",
    lessons_md_hash: str = "",
    claude_md_size: int = 0,
    todo_md_size: int = 0,
    lessons_md_size: int = 0,
    message_count: int = 0,
    token_estimate: int = 0,
):
    """Record a kernel snapshot for a specific node."""
    snap = engine.record_snapshot(
        timeline_id=timeline_id,
        node_id=node_id,
        cer=cer,
        skills=skills,
        claude_md_hash=claude_md_hash,
        todo_md_hash=todo_md_hash,
        lessons_md_hash=lessons_md_hash,
        claude_md_size=claude_md_size,
        todo_md_size=todo_md_size,
        lessons_md_size=lessons_md_size,
        message_count=message_count,
        token_estimate=token_estimate,
    )
    return snap.model_dump()


class SnapshotRequest(BaseModel):
    timeline_id: str
    node_id: str
    cer: float = 0.0
    skills: list[str] = []
    claude_md_hash: str = ""
    todo_md_hash: str = ""
    lessons_md_hash: str = ""
    claude_md_size: int = 0
    todo_md_size: int = 0
    lessons_md_size: int = 0
    message_count: int = 0
    token_estimate: int = 0


@app.post("/api/v1/snapshot/record")
async def record_snapshot_body(req: SnapshotRequest):
    """Record snapshot via JSON body (preferred)."""
    snap = engine.record_snapshot(**req.model_dump())
    return snap.model_dump()


# ─── Timelines ────────────────────────────────────────────────

@app.get("/api/v1/timelines")
async def list_timelines(limit: int = 50):
    tls = engine.list_timelines(limit)
    return {"timelines": [tl.model_dump() for tl in tls]}


@app.get("/api/v1/timelines/{timeline_id}")
async def get_timeline(timeline_id: str):
    tl = engine.timelines.get(timeline_id)
    if not tl:
        raise HTTPException(404, f"Timeline {timeline_id} not found")
    nodes = engine.get_timeline_nodes(timeline_id)
    return {
        "timeline": tl.model_dump(),
        "nodes": [n.model_dump() for n in nodes],
    }


# ─── Replay ──────────────────────────────────────────────────

@app.get("/api/v1/replay/{timeline_id}")
async def replay_timeline(timeline_id: str):
    """Get full replay sequence with snapshots and branch points."""
    tl = engine.timelines.get(timeline_id)
    if not tl:
        raise HTTPException(404, f"Timeline {timeline_id} not found")
    steps = engine.get_replay_steps(timeline_id)
    return {
        "timeline": tl.model_dump(),
        "steps": [s.model_dump() for s in steps],
        "total_steps": len(steps),
    }


@app.get("/api/v1/replay/{timeline_id}/step/{step_number}")
async def replay_step(timeline_id: str, step_number: int):
    """Get a single replay step."""
    steps = engine.get_replay_steps(timeline_id)
    if step_number < 0 or step_number >= len(steps):
        raise HTTPException(404, f"Step {step_number} out of range (0-{len(steps)-1})")
    return steps[step_number].model_dump()


# ─── Branching ────────────────────────────────────────────────

@app.post("/api/v1/branch")
async def branch_timeline(req: BranchRequest):
    """Create a new timeline branch from a specific node."""
    try:
        branch_tl = engine.branch_from(
            req.source_timeline_id,
            req.branch_from_node_id,
            req.name,
        )
        return {
            "branch": branch_tl.model_dump(),
            "message": f"Branch created with {branch_tl.node_count} inherited nodes",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── Diff ─────────────────────────────────────────────────────

@app.get("/api/v1/diff")
async def diff_timelines(
    timeline_a: str = Query(..., description="First timeline ID"),
    timeline_b: str = Query(..., description="Second timeline ID"),
):
    """Compare two timelines — find divergence, decision differences, CER trends."""
    if timeline_a not in engine.timelines:
        raise HTTPException(404, f"Timeline {timeline_a} not found")
    if timeline_b not in engine.timelines:
        raise HTTPException(404, f"Timeline {timeline_b} not found")

    result = engine.diff_timelines(timeline_a, timeline_b)
    return result.model_dump()


# ─── Stats ────────────────────────────────────────────────────

@app.get("/api/v1/stats")
async def stats():
    return engine.get_stats()


# ─── WebSocket (live timeline updates) ───────────────────────

@app.websocket("/ws/replay")
async def websocket_replay(websocket: WebSocket):
    """Stream live timeline node additions to the dashboard."""
    await websocket.accept()
    engine.ws_clients.append(websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "stats": engine.get_stats(),
        })
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        engine.ws_clients.remove(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8404)
