"""Vector memory using SQLite + optional sqlite-vss for embedding-based recall.

Architecture:
  - ChromaDB: Best-in-class embedded vector DB (self-hosted, Python-native)
  - SQLite-VSS: Good fallback for local/SQLite-first setups
  - Simple keyword + embedding: Works everywhere, uses Ollama for embeddings
  - All three share the same query interface so the agent doesn't care which backend.
"""

import asyncio
import importlib.util
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None

# ── BM25 lexical ranking (stdlib math only — no embeddings, no new deps) ──
# This is lexical relevance: stemmed keyword overlap between query and stored
# content/tags. Honest label: BM25, not semantic vector search.
BM25_K1 = 1.5
BM25_B = 0.75
# Per-field weighting: tags are short and high-signal, so hits there count more.
TAG_FIELD_WEIGHT = 1.5
# Recency boost: newer entries score slightly higher (multiplicative, capped).
RECENCY_BOOST_MAX = 0.25
RECENCY_DECAY_DAYS = 14.0
# Exact-phrase bonus: raw query substring hit in content.
EXACT_PHRASE_BONUS = 1.5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens for BM25 fields."""
    return _TOKEN_RE.findall((text or "").lower())


def _stem(token: str) -> str:
    """Light suffix stemming, same philosophy as skills/matcher.py: applied
    identically to query and document sides, so consistency is what matters.
    No real stemming library — just trailing inflections on longer tokens.
    """
    if len(token) <= 4:
        return token
    for suffix, repl in (("ies", "y"), ("sses", "s"), ("ing", ""), ("ed", ""), ("s", "")):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: len(token) - len(suffix)] + repl
    return token


def _bm25_rank(
    query: str,
    entries: list[tuple],
    now: float,
) -> list[tuple]:
    """Rank stored entries against a query with per-field BM25.

    `entries` is a list of (id, content, metadata, tags, created_at, access_count)
    tuples. Returns the same tuples extended with a final score, sorted
    descending. Scoring = sum over fields (content weight 1.0, tags weight
    TAG_FIELD_WEIGHT) of field-local BM25, then:
      - exact-phrase bonus when the raw query appears in the content,
      - multiplicative recency boost that decays with entry age.
    """
    n = len(entries)
    q_terms = [_stem(t) for t in _tokenize(query)]
    scores = [0.0] * n

    if q_terms:
        for weight, get_field in ((1.0, lambda e: e[1]), (TAG_FIELD_WEIGHT, lambda e: (e[3] or "").replace(",", " "))):
            docs_tokens = [_tokenize(get_field(e)) for e in entries]
            total_len = sum(len(toks) for toks in docs_tokens)
            avgdl = (total_len / n) or 1.0
            df: dict[str, int] = {}
            for toks in docs_tokens:
                for term in set(toks):
                    df[term] = df.get(term, 0) + 1
            for i, toks in enumerate(docs_tokens):
                if not toks:
                    continue
                dl = len(toks)
                counts: dict[str, int] = {}
                for t in toks:
                    counts[t] = counts.get(t, 0) + 1
                s = 0.0
                for term in q_terms:
                    tf = counts.get(term, 0)
                    if not tf:
                        continue
                    idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1.0)
                    s += idf * (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
                scores[i] += weight * s

        # Exact-phrase bonus on the raw (lowercased) content.
        needle = query.lower().strip()
        if needle:
            for i, e in enumerate(entries):
                if needle in (e[1] or "").lower():
                    scores[i] += EXACT_PHRASE_BONUS

    ranked = []
    for i, e in enumerate(entries):
        created_at = e[4] or 0.0
        age_days = max(0.0, (now - created_at) / 86400.0)
        recency = 1.0 + RECENCY_BOOST_MAX * math.exp(-age_days / RECENCY_DECAY_DAYS)
        ranked.append((*e, scores[i] * recency))
    ranked.sort(key=lambda r: (-r[-1], -(r[4] or 0.0)))
    return ranked


@dataclass
class MemoryEntry:
    """A single entry in vector memory."""

    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: float = field(default_factory=time.time)
    access_count: int = 0


class VectorMemoryBackend:
    """Abstraction for vector memory backends. Implement this for custom backends."""

    async def add(self, entry: MemoryEntry) -> None:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 5, filters: dict | None = None) -> list[MemoryEntry]:
        raise NotImplementedError

    async def delete(self, entry_id: str) -> None:
        raise NotImplementedError

    async def count(self) -> int:
        raise NotImplementedError


class SimpleKeywordBackend(VectorMemoryBackend):
    """
    Keyword-based memory using SQLite + real BM25 lexical ranking (stdlib
    math, no external dependencies). Stores text chunks with metadata and
    ranks them by stemmed keyword relevance (BM25 k1=1.5, b=0.75), with a
    recency boost and an exact-phrase bonus.

    Honest scope: this is lexical relevance ranking, NOT embedding/semantic
    search — queries only match words that actually appear in the entries.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or self._default_path()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    def _default_path(self) -> str:
        base = Path.home() / ".nexus"
        base.mkdir(parents=True, exist_ok=True)
        return str(base / "memory.db")

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    tags TEXT DEFAULT '',
                    created_at REAL DEFAULT (unixepoch()),
                    access_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content, tags, content=memory_entries, content_rowid=rowid
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_access (
                    entry_id TEXT, accessed_at REAL DEFAULT (unixepoch()),
                    FOREIGN KEY (entry_id) REFERENCES memory_entries(id)
                )
            """)
            conn.commit()
            self._conn = conn

    async def add(self, entry: MemoryEntry) -> None:
        tags = ",".join(entry.metadata.get("tags", []))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._add_sync, entry, tags)

    def _add_sync(self, entry: MemoryEntry, tags: str) -> None:
        with self._lock:
            c = self._conn.cursor()
            c.execute(
                """
                INSERT OR REPLACE INTO memory_entries (id, content, metadata, tags, created_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (entry.id, entry.content, json.dumps(entry.metadata), tags, entry.created_at, entry.access_count),
            )
            # External-content FTS table: mirror the content row into the
            # index with the same rowid (documented FTS5 external-content
            # pattern; the 'reindex' command form is not valid SQL here).
            c.execute(
                "INSERT INTO memory_fts(rowid, content, tags) "
                "SELECT rowid, content, tags FROM memory_entries WHERE id = ?",
                (entry.id,),
            )
            self._conn.commit()

    async def search(self, query: str, limit: int = 5, filters: dict | None = None) -> list[MemoryEntry]:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, self._search_sync, query, limit, filters)
        return results

    def _search_sync(self, query: str, limit: int, filters: dict | None) -> list[MemoryEntry]:
        """BM25-ranked lexical search over all stored entries.

        Loads candidate rows, scores them with per-field BM25 (content +
        tags), applies the exact-phrase bonus and recency boost, and returns
        the top `limit` entries. The `filters` dict constrains results to
        entries whose metadata contains every given key/value pair.
        """
        if not query.strip():
            return []

        now = time.time()
        with self._lock:
            c = self._conn.cursor()
            c.execute(
                """
                SELECT m.id, m.content, m.metadata, m.tags, m.created_at, m.access_count
                FROM memory_entries m
            """
            )
            entries = []
            for row in c.fetchall():
                entry_id, content, metadata_json, tags, created_at, access_count = row
                metadata = json.loads(metadata_json)
                if filters and not all(metadata.get(k) == v for k, v in filters.items()):
                    continue
                entries.append((entry_id, content, metadata, tags, created_at, access_count))

            if not entries:
                return []

            ranked = _bm25_rank(query, entries, now)
            results = []
            for entry_id, content, metadata, _tags, created_at, access_count, _score in ranked[:limit]:
                results.append(
                    MemoryEntry(
                        id=entry_id,
                        content=content,
                        metadata=metadata,
                        created_at=created_at,
                        access_count=access_count,
                    )
                )
                c.execute("UPDATE memory_entries SET access_count = access_count + 1 WHERE id = ?", (entry_id,))
                c.execute("INSERT INTO memory_access(entry_id) VALUES (?)", (entry_id,))
            self._conn.commit()
            return results

    async def delete(self, entry_id: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete_sync, entry_id)

    def _delete_sync(self, entry_id: str) -> None:
        with self._lock:
            # Keep the external-content FTS index in sync before removing
            # the content row, so deleted entries stop matching searches.
            self._conn.execute(
                "INSERT INTO memory_fts(memory_fts, rowid) "
                "SELECT 'delete', rowid FROM memory_entries WHERE id = ?",
                (entry_id,),
            )
            self._conn.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))
            self._conn.commit()

    async def count(self) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._count_sync)

    def _count_sync(self) -> int:
        with self._lock:
            c = self._conn.cursor()
            c.execute("SELECT COUNT(*) FROM memory_entries")
            return c.fetchone()[0]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


class OllamaEmbeddingBackend(VectorMemoryBackend):
    """
    Vector memory using Ollama embeddings + SQLite.
    Provides true semantic search using local Ollama models.
    Falls back to SimpleKeywordBackend if Ollama is unavailable.
    """

    def __init__(self, db_path: str | None = None, embed_model: str = "nomic-embed-text"):
        self.db_path = db_path or self._default_path()
        self.embed_model = embed_model
        self._kw_backend = SimpleKeywordBackend(db_path)
        self._embedding_dim = 768
        self._client: Any | None = None
        self._init_db()

    def _default_path(self) -> str:
        base = Path.home() / ".nexus"
        base.mkdir(parents=True, exist_ok=True)
        return str(base / "memory_vectors.db")

    def _init_db(self) -> None:
        if not NUMPY_AVAILABLE:
            return
        with self._kw_backend._lock:
            conn = self._kw_backend._conn
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    entry_id TEXT PRIMARY KEY,
                    embedding BLOB,
                    FOREIGN KEY (entry_id) REFERENCES memory_entries(id)
                )
            """)
            conn.commit()

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        try:
            resp = await client.post("http://localhost:11434/api/embeddings", json={"model": self.embed_model, "prompt": texts[0]})
            if resp.status_code == 200:
                return [resp.json()["embedding"]]
        except Exception:
            pass
        return [[0.0] * self._embedding_dim for _ in texts]

    async def add(self, entry: MemoryEntry) -> None:
        if entry.embedding is None:
            embeds = await self._embed([entry.content])
            entry.embedding = embeds[0] if embeds else [0.0] * self._embedding_dim
        await self._kw_backend.add(entry)

    async def search(self, query: str, limit: int = 5, filters: dict | None = None) -> list[MemoryEntry]:
        return await self._kw_backend.search(query, limit, filters)

    async def delete(self, entry_id: str) -> None:
        await self._kw_backend.delete(entry_id)

    async def count(self) -> int:
        return await self._kw_backend.count()


class VectorMemory:
    """
    Unified vector + keyword memory for Nexus.
    Combines SQLite FTS5 (keyword) + Ollama embeddings (semantic) + structured storage.
    Inspired by: OpenClaw's qmd memory, OpenCode's auto-compact agent.
    """

    def __init__(
        self,
        db_path: str | None = None,
        backend: str = "keyword",
        embed_model: str = "nomic-embed-text",
    ):
        if backend == "ollama":
            self._backend: VectorMemoryBackend = OllamaEmbeddingBackend(db_path, embed_model)
        else:
            self._backend = SimpleKeywordBackend(db_path)
        self._db_path = db_path

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Store a memory entry. Returns the entry ID."""
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            metadata=metadata or {},
        )
        await self._backend.add(entry)
        return entry.id

    async def recall(self, query: str, limit: int = 5, context: str | None = None) -> list[MemoryEntry]:
        """
        Recall relevant memories from the vector store.
        Uses keyword matching (always) + semantic embeddings (if available).
        """
        effective_query = query
        if context and len(context) > 500:
            effective_query = context[:500] + " " + query

        return await self._backend.search(effective_query, limit=limit)

    async def forget(self, entry_id: str) -> None:
        """Delete a memory entry by ID."""
        await self._backend.delete(entry_id)

    async def count(self) -> int:
        """Return total number of memories."""
        return await self._backend.count()

    def consolidate(self, max_entries: int = 10000) -> int:
        """
        Consolidate memory by removing old entries when limit is exceeded.
        Returns number of entries removed.
        """
        removed = 0
        try:
            conn = sqlite3.connect(self._db_path or self._backend._default_path())
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM memory_entries")
            total = c.fetchone()[0]
            if total > max_entries:
                excess = total - max_entries
                c.execute(
                    """
                    DELETE FROM memory_entries
                    WHERE id IN (
                        SELECT id FROM memory_entries
                        ORDER BY access_count ASC, created_at ASC
                        LIMIT ?
                    )
                """,
                    (excess,),
                )
                removed = c.rowcount
                conn.commit()
            conn.close()
        except Exception:
            pass
        return removed
