"""
ClawdContext OS — FlightRecorder (Layer 5)
Immutable, hash-chained audit log with anomaly detection.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

AUDIT_FILE = Path(os.getenv("CCOS_AUDIT_FILE", "/data/flight-recorder.jsonl"))
AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)


# ─── Models ────────────────────────────────────────────────────────

class Event(BaseModel):
    source: str = Field(..., description="Service that generated the event")
    event_type: str = Field(..., description="Event type (evaluation, scan, alert, boot)")
    severity: str = Field(default="INFO", description="INFO, WARN, CRITICAL")
    data: dict = Field(default_factory=dict)


class StoredEvent(BaseModel):
    id: int
    timestamp: str
    source: str
    event_type: str
    severity: str
    data: dict
    prev_hash: str
    entry_hash: str


class ChainStatus(BaseModel):
    valid: bool
    total_entries: int
    verified: int
    first_entry: str = ""
    last_entry: str = ""


class AnomalyReport(BaseModel):
    period_seconds: int
    total_events: int
    deny_rate: float
    unusual_patterns: list[str]
    top_denied_tools: list[dict]
    top_denied_skills: list[dict]


# ─── Flight Recorder Engine ──────────────────────────────────────

class FlightRecorder:
    def __init__(self):
        self.entries: list[StoredEvent] = []
        self.prev_hash = "GENESIS"
        self.ws_clients: list[WebSocket] = []
        self._load()

    def _load(self):
        if AUDIT_FILE.exists():
            for line in AUDIT_FILE.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        d = json.loads(line)
                        entry = StoredEvent(**d)
                        self.entries.append(entry)
                        self.prev_hash = entry.entry_hash
                    except (json.JSONDecodeError, Exception):
                        pass

    def record(self, event: Event) -> StoredEvent:
        timestamp = datetime.now(timezone.utc).isoformat()
        entry_id = len(self.entries) + 1
        payload = f"{entry_id}|{timestamp}|{event.source}|{event.event_type}|{self.prev_hash}"
        entry_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]

        stored = StoredEvent(
            id=entry_id,
            timestamp=timestamp,
            source=event.source,
            event_type=event.event_type,
            severity=event.severity,
            data=event.data,
            prev_hash=self.prev_hash,
            entry_hash=entry_hash,
        )

        self.entries.append(stored)
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(stored.model_dump()) + "\n")
        self.prev_hash = entry_hash
        return stored

    def verify_chain(self) -> ChainStatus:
        if not self.entries:
            return ChainStatus(valid=True, total_entries=0, verified=0)

        prev = "GENESIS"
        for i, entry in enumerate(self.entries):
            if entry.prev_hash != prev:
                return ChainStatus(
                    valid=False,
                    total_entries=len(self.entries),
                    verified=i,
                    first_entry=self.entries[0].timestamp,
                    last_entry=self.entries[-1].timestamp,
                )
            prev = entry.entry_hash

        return ChainStatus(
            valid=True,
            total_entries=len(self.entries),
            verified=len(self.entries),
            first_entry=self.entries[0].timestamp,
            last_entry=self.entries[-1].timestamp,
        )

    def query(self, limit: int = 100, source: str = "", severity: str = "", event_type: str = "") -> list[StoredEvent]:
        results = self.entries[:]
        if source:
            results = [e for e in results if e.source == source]
        if severity:
            results = [e for e in results if e.severity == severity]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        return results[-limit:]

    def anomaly_report(self, period_seconds: int = 3600) -> AnomalyReport:
        cutoff = time.time() - period_seconds
        recent = [
            e for e in self.entries
            if e.timestamp > datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        ]

        total = len(recent)
        denials = [e for e in recent if e.data.get("decision") == "DENY"]
        deny_rate = len(denials) / max(total, 1)

        denied_tools = Counter(e.data.get("tool", "unknown") for e in denials)
        denied_skills = Counter(e.data.get("skill", "unknown") for e in denials)

        patterns: list[str] = []
        if deny_rate > 0.5:
            patterns.append(f"High denial rate: {deny_rate:.0%}")
        if len(denied_skills) == 1 and len(denials) > 5:
            patterns.append(f"Single skill generating all denials: {list(denied_skills.keys())[0]}")
        criticals = [e for e in recent if e.severity == "CRITICAL"]
        if len(criticals) > 3:
            patterns.append(f"Burst of {len(criticals)} CRITICAL events in {period_seconds}s")

        return AnomalyReport(
            period_seconds=period_seconds,
            total_events=total,
            deny_rate=round(deny_rate, 3),
            unusual_patterns=patterns,
            top_denied_tools=[{"tool": t, "count": c} for t, c in denied_tools.most_common(5)],
            top_denied_skills=[{"skill": s, "count": c} for s, c in denied_skills.most_common(5)],
        )


# ─── FastAPI App ──────────────────────────────────────────────────

app = FastAPI(
    title="ClawdContext OS — FlightRecorder",
    description="Layer 5: Immutable, hash-chained audit log with anomaly detection",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

recorder = FlightRecorder()

# Record boot event
recorder.record(Event(
    source="flight-recorder",
    event_type="boot",
    severity="INFO",
    data={"message": "FlightRecorder online", "chain": "GENESIS"},
))


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "flight-recorder", "layer": 5}


@app.post("/api/v1/events", response_model=StoredEvent)
async def record_event(event: Event):
    stored = recorder.record(event)
    # Broadcast to WebSocket clients
    for ws in recorder.ws_clients[:]:
        try:
            await ws.send_json(stored.model_dump())
        except Exception:
            recorder.ws_clients.remove(ws)
    return stored


@app.get("/api/v1/events")
async def query_events(
    limit: int = 100,
    source: str = "",
    severity: str = "",
    event_type: str = "",
):
    entries = recorder.query(limit, source, severity, event_type)
    return {"count": len(entries), "entries": [e.model_dump() for e in entries]}


@app.get("/api/v1/chain/verify", response_model=ChainStatus)
async def verify_chain():
    return recorder.verify_chain()


@app.get("/api/v1/anomalies", response_model=AnomalyReport)
async def anomalies(period: int = 3600):
    return recorder.anomaly_report(period)


@app.get("/api/v1/stats")
async def stats():
    total = len(recorder.entries)
    by_severity = Counter(e.severity for e in recorder.entries)
    by_source = Counter(e.source for e in recorder.entries)
    by_type = Counter(e.event_type for e in recorder.entries)
    return {
        "total_events": total,
        "by_severity": dict(by_severity),
        "by_source": dict(by_source),
        "by_type": dict(by_type),
    }


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await websocket.accept()
    recorder.ws_clients.append(websocket)
    try:
        await websocket.send_json({"type": "connected", "total_events": len(recorder.entries)})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        recorder.ws_clients.remove(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8402)
