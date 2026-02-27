"""
ClawdContext OS — Memory Service (Qdrant Semantic Memory)
=========================================================
Provides semantic memory for the AI agent runtime using Qdrant vector DB
and local sentence-transformers embeddings (all-MiniLM-L6-v2, 384-dim).

Architecture:
  OpenClaw ──▶ Memory Service ──▶ Qdrant (vector DB)
                     │
               FlightRecorder (auto-ingest events)

Collections:
  - conversations: Chat messages and responses
  - tool_results:  Tool execution outputs
  - lessons:       Agent lessons learned (from lessons.md)
  - knowledge:     Arbitrary knowledge chunks (skills, docs)

Port: 8405
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
FLIGHT_RECORDER_URL = os.getenv("FLIGHT_RECORDER_URL", "http://flight-recorder:8402")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 dimension
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "10"))
AUTO_INGEST = os.getenv("AUTO_INGEST", "true").lower() == "true"

# ═══════════════════════════════════════════════════════════════
# Collections
# ═══════════════════════════════════════════════════════════════

COLLECTIONS = {
    "conversations": "Chat messages and agent responses",
    "tool_results": "Tool execution outputs and decisions",
    "lessons": "Agent lessons learned",
    "knowledge": "Skills, documentation, and knowledge chunks",
}

# ═══════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════


class StoreRequest(BaseModel):
    collection: str = Field(..., pattern=r"^(conversations|tool_results|lessons|knowledge)$")
    text: str = Field(..., min_length=1, max_length=50000)
    metadata: dict[str, Any] = {}
    session_id: str = "default"
    deduplicate: bool = True


class StoreResponse(BaseModel):
    id: str
    collection: str
    text_hash: str
    stored: bool
    duplicate: bool = False


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    collection: Optional[str] = None  # None = search all collections
    session_id: Optional[str] = None  # Filter by session
    limit: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class SearchResult(BaseModel):
    id: str
    collection: str
    text: str
    score: float
    metadata: dict[str, Any] = {}


class SearchResponse(BaseModel):
    results: list[SearchResult]
    query: str
    total: int
    latency_ms: float


class IngestEvent(BaseModel):
    event_type: str
    source: str
    text: str
    metadata: dict[str, Any] = {}
    session_id: str = "default"


class MemoryStats(BaseModel):
    collections: dict[str, int]  # collection -> point count
    total_points: int
    embedding_model: str
    embedding_dim: int
    qdrant_connected: bool


# ═══════════════════════════════════════════════════════════════
# Embedding Engine
# ═══════════════════════════════════════════════════════════════


class EmbeddingEngine:
    """Local sentence-transformers embeddings — no API key needed."""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer
        print(f"[memory] Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"[memory] Model loaded — dimension: {self.dim}")

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to embedding vectors."""
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def encode_one(self, text: str) -> list[float]:
        """Encode a single text."""
        return self.encode([text])[0]


# ═══════════════════════════════════════════════════════════════
# Memory Store
# ═══════════════════════════════════════════════════════════════


class MemoryStore:
    """Qdrant-backed semantic memory with deduplication."""

    def __init__(self, qdrant_url: str, embedder: EmbeddingEngine):
        self.client = QdrantClient(url=qdrant_url, timeout=10)
        self.embedder = embedder
        self._ensure_collections()

    def _ensure_collections(self):
        """Create collections if they don't exist."""
        existing = {c.name for c in self.client.get_collections().collections}
        for name in COLLECTIONS:
            if name not in existing:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=self.embedder.dim,
                        distance=Distance.COSINE,
                    ),
                )
                print(f"[memory] Created collection: {name}")
            else:
                print(f"[memory] Collection exists: {name}")

    def _text_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def store(self, collection: str, text: str, metadata: dict,
              session_id: str, deduplicate: bool = True) -> StoreResponse:
        """Store a text with its embedding, with optional deduplication."""
        text_hash = self._text_hash(text)

        # Deduplication: check if exact text already stored
        if deduplicate:
            existing = self.client.scroll(
                collection_name=collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key="text_hash", match=MatchValue(value=text_hash))]
                ),
                limit=1,
            )
            if existing[0]:
                return StoreResponse(
                    id=str(existing[0][0].id),
                    collection=collection,
                    text_hash=text_hash,
                    stored=False,
                    duplicate=True,
                )

        # Encode and store
        vector = self.embedder.encode_one(text)
        point_id = str(uuid.uuid4())

        payload = {
            "text": text,
            "text_hash": text_hash,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }

        self.client.upsert(
            collection_name=collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

        return StoreResponse(
            id=point_id,
            collection=collection,
            text_hash=text_hash,
            stored=True,
        )

    def search(self, query: str, collection: Optional[str] = None,
               session_id: Optional[str] = None, limit: int = 5,
               score_threshold: float = 0.3) -> list[SearchResult]:
        """Semantic search across one or all collections."""
        vector = self.embedder.encode_one(query)
        results: list[SearchResult] = []

        # Build optional filter
        conditions = []
        if session_id:
            conditions.append(
                FieldCondition(key="session_id", match=MatchValue(value=session_id))
            )
        search_filter = Filter(must=conditions) if conditions else None

        collections_to_search = [collection] if collection else list(COLLECTIONS.keys())

        for coll in collections_to_search:
            try:
                hits = self.client.query_points(
                    collection_name=coll,
                    query=vector,
                    query_filter=search_filter,
                    limit=limit,
                    score_threshold=score_threshold,
                )
                for hit in hits.points:
                    payload = hit.payload or {}
                    results.append(SearchResult(
                        id=str(hit.id),
                        collection=coll,
                        text=payload.get("text", ""),
                        score=hit.score,
                        metadata={k: v for k, v in payload.items()
                                  if k not in ("text", "text_hash")},
                    ))
            except Exception as e:
                print(f"[memory] Search error in {coll}: {e}")

        # Sort by score descending, limit
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def stats(self) -> dict[str, int]:
        """Get point counts per collection."""
        counts = {}
        for name in COLLECTIONS:
            try:
                info = self.client.get_collection(name)
                counts[name] = info.points_count or 0
            except Exception:
                counts[name] = 0
        return counts

    def delete(self, collection: str, point_id: str) -> bool:
        """Delete a specific point."""
        try:
            self.client.delete(
                collection_name=collection,
                points_selector=[point_id],
            )
            return True
        except Exception:
            return False

    def clear_collection(self, collection: str) -> int:
        """Clear all points in a collection. Returns count deleted."""
        try:
            info = self.client.get_collection(collection)
            count = info.points_count or 0
            self.client.delete_collection(collection)
            self.client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=self.embedder.dim,
                    distance=Distance.COSINE,
                ),
            )
            return count
        except Exception:
            return 0


# ═══════════════════════════════════════════════════════════════
# FastAPI Application
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="ClawdContext OS — Memory Service",
    version="0.1.0",
    description="Semantic memory layer powered by Qdrant + sentence-transformers",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Globals (initialized on startup)
embedder: Optional[EmbeddingEngine] = None
memory: Optional[MemoryStore] = None
stats_data = {
    "stores": 0,
    "searches": 0,
    "ingests": 0,
    "errors": 0,
    "start_time": None,
}


# ─── Health ───────────────────────────────────────────────────


@app.get("/healthz")
async def healthz():
    """Health check — verifies Qdrant connection."""
    qdrant_ok = False
    try:
        if memory:
            memory.client.get_collections()
            qdrant_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if qdrant_ok else "degraded",
        "service": "memory-service",
        "qdrant": "connected" if qdrant_ok else "disconnected",
        "embedding_model": EMBEDDING_MODEL,
        "uptime_s": round(time.time() - stats_data["start_time"], 1)
        if stats_data["start_time"]
        else 0,
    }


# ─── Store ────────────────────────────────────────────────────


@app.post("/api/v1/memory/store", response_model=StoreResponse)
async def store_memory(req: StoreRequest):
    """Store a text chunk with its embedding in the specified collection."""
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    try:
        result = memory.store(
            collection=req.collection,
            text=req.text,
            metadata=req.metadata,
            session_id=req.session_id,
            deduplicate=req.deduplicate,
        )
        stats_data["stores"] += 1
        return result
    except Exception as e:
        stats_data["errors"] += 1
        raise HTTPException(500, f"Store failed: {e}")


# ─── Search ───────────────────────────────────────────────────


@app.post("/api/v1/memory/search", response_model=SearchResponse)
async def search_memory(req: SearchRequest):
    """Semantic search across memory collections."""
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    start = time.monotonic()
    try:
        results = memory.search(
            query=req.query,
            collection=req.collection,
            session_id=req.session_id,
            limit=req.limit,
            score_threshold=req.score_threshold,
        )
        latency = (time.monotonic() - start) * 1000
        stats_data["searches"] += 1

        return SearchResponse(
            results=results,
            query=req.query,
            total=len(results),
            latency_ms=round(latency, 2),
        )
    except Exception as e:
        stats_data["errors"] += 1
        raise HTTPException(500, f"Search failed: {e}")


# ─── Recall (Simple GET for OpenClaw) ────────────────────────


@app.get("/api/v1/memory/recall")
async def recall_memory(
    q: str,
    collection: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 5,
):
    """Simple GET endpoint for quick recall — used by OpenClaw before LLM calls."""
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    start = time.monotonic()
    results = memory.search(
        query=q,
        collection=collection,
        session_id=session_id,
        limit=limit,
        score_threshold=0.35,
    )
    latency = (time.monotonic() - start) * 1000
    stats_data["searches"] += 1

    return {
        "memories": [
            {
                "text": r.text,
                "score": round(r.score, 4),
                "collection": r.collection,
                "metadata": r.metadata,
            }
            for r in results
        ],
        "count": len(results),
        "latency_ms": round(latency, 2),
    }


# ─── Ingest (from FlightRecorder / external) ─────────────────


@app.post("/api/v1/memory/ingest")
async def ingest_event(event: IngestEvent):
    """Ingest an event into the appropriate collection.

    Mapping:
      - chat → conversations
      - tool_call → tool_results
      - lesson → lessons
      - * → knowledge
    """
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    collection_map = {
        "chat": "conversations",
        "tool_call": "tool_results",
        "lesson": "lessons",
    }
    collection = collection_map.get(event.event_type, "knowledge")

    result = memory.store(
        collection=collection,
        text=event.text,
        metadata={"event_type": event.event_type, "source": event.source, **event.metadata},
        session_id=event.session_id,
    )
    stats_data["ingests"] += 1

    return {"ingested": result.stored, "collection": collection, "id": result.id}


# ─── Stats ────────────────────────────────────────────────────


@app.get("/api/v1/memory/stats", response_model=MemoryStats)
async def memory_stats():
    """Return memory store statistics."""
    qdrant_ok = False
    counts: dict[str, int] = {}

    if memory:
        try:
            counts = memory.stats()
            qdrant_ok = True
        except Exception:
            pass

    return MemoryStats(
        collections=counts,
        total_points=sum(counts.values()),
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=EMBEDDING_DIM,
        qdrant_connected=qdrant_ok,
    )


# ─── Admin: Clear / Delete ───────────────────────────────────


@app.delete("/api/v1/memory/{collection}")
async def clear_collection(collection: str):
    """Clear all points in a collection."""
    if collection not in COLLECTIONS:
        raise HTTPException(404, f"Unknown collection: {collection}")
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    count = memory.clear_collection(collection)
    return {"cleared": collection, "points_deleted": count}


@app.delete("/api/v1/memory/{collection}/{point_id}")
async def delete_point(collection: str, point_id: str):
    """Delete a specific memory point."""
    if collection not in COLLECTIONS:
        raise HTTPException(404, f"Unknown collection: {collection}")
    if not memory:
        raise HTTPException(503, "Memory store not initialized")

    ok = memory.delete(collection, point_id)
    return {"deleted": ok, "id": point_id}


# ─── Collections Info ─────────────────────────────────────────


@app.get("/api/v1/memory/collections")
async def list_collections():
    """List all memory collections with descriptions."""
    counts = memory.stats() if memory else {}
    return {
        "collections": [
            {
                "name": name,
                "description": desc,
                "points": counts.get(name, 0),
            }
            for name, desc in COLLECTIONS.items()
        ]
    }


# ─── Startup / Shutdown ──────────────────────────────────────


@app.on_event("startup")
async def startup():
    global embedder, memory
    stats_data["start_time"] = time.time()

    print("=" * 60)
    print("  ClawdContext OS — Memory Service v0.1.0")
    print("=" * 60)
    print(f"  Qdrant URL:       {QDRANT_URL}")
    print(f"  Embedding model:  {EMBEDDING_MODEL}")
    print(f"  Embedding dim:    {EMBEDDING_DIM}")
    print(f"  Auto-ingest:      {AUTO_INGEST}")
    print("=" * 60)

    # Load embedding model
    embedder = EmbeddingEngine(EMBEDDING_MODEL)

    # Connect to Qdrant
    retries = 5
    for attempt in range(retries):
        try:
            memory = MemoryStore(QDRANT_URL, embedder)
            print(f"[memory] Connected to Qdrant at {QDRANT_URL}")
            break
        except Exception as e:
            print(f"[memory] Qdrant connection attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                import asyncio
                await asyncio.sleep(3)
            else:
                print("[memory] WARNING: Starting without Qdrant — endpoints will return 503")

    # Report collection stats
    if memory:
        for name, count in memory.stats().items():
            print(f"  {name}: {count} points")


@app.on_event("shutdown")
async def shutdown():
    print("[memory] Shutting down...")
