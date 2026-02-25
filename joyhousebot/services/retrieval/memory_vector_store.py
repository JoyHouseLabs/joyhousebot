"""Memory vector index: SQLite + embeddings for semantic memory search (OpenClaw-aligned)."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.memory import safe_scope_key

MEMORY_REL = "memory"
CHUNK_CHARS = 1600
CHUNK_OVERLAP = 320
INDEX_DB_NAME = ".memory_index.db"
SNIPPET_MAX_CHARS = 700


def _chunk_text(text: str, chunk_size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count."""
    text = (text or "").strip()
    if not text:
        return []
    step = max(1, chunk_size - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                last = text.rfind(sep, start, end + 1)
                if last >= start:
                    end = last + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end if end > start else start + chunk_size
    return chunks


def _embedding_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _memory_candidates(memory_dir: Path, rel_prefix: str) -> list[tuple[str, Path]]:
    """Same file set as memory_search: MEMORY.md, HISTORY.md, .abstract, *.md, insights/*, lessons/*."""
    candidates: list[tuple[str, Path]] = []
    if (memory_dir / "MEMORY.md").exists():
        candidates.append((f"{rel_prefix}MEMORY.md", memory_dir / "MEMORY.md"))
    if (memory_dir / "HISTORY.md").exists():
        candidates.append((f"{rel_prefix}HISTORY.md", memory_dir / "HISTORY.md"))
    if (memory_dir / ".abstract").exists():
        candidates.append((f"{rel_prefix}.abstract", memory_dir / ".abstract"))
    for p in memory_dir.glob("*.md"):
        if p.name not in ("MEMORY.md", "HISTORY.md"):
            candidates.append((f"{rel_prefix}{p.name}", p))
    for sub in ("insights", "lessons"):
        subdir = memory_dir / sub
        if subdir.is_dir():
            for p in subdir.glob("*.md"):
                candidates.append((f"{rel_prefix}{sub}/{p.name}", p))
    return candidates


class MemoryVectorStore:
    """SQLite-backed vector index over memory files for semantic search."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.db_path = self.workspace / MEMORY_REL / INDEX_DB_NAME

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_index (
                id INTEGER PRIMARY KEY,
                scope_key TEXT NOT NULL,
                file_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                file_mtime REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_index(scope_key)")
        conn.execute("CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()

    def _scope_dir(self, scope_key: str | None) -> tuple[Path, str]:
        base = self.workspace / MEMORY_REL
        safe = safe_scope_key(scope_key) if scope_key else ""
        memory_dir = (base / safe) if safe else base
        rel_prefix = f"{MEMORY_REL}/{safe}/" if safe else f"{MEMORY_REL}/"
        return memory_dir, rel_prefix

    def _is_stale(self, conn: sqlite3.Connection, scope_key: str | None, memory_dir: Path, rel_prefix: str) -> bool:
        """True if any source file is newer than what we have in index_meta."""
        try:
            row = conn.execute(
                "SELECT value FROM index_meta WHERE key = ?",
                (f"mtime_{scope_key or 'shared'}",),
            ).fetchone()
            last = float(row[0]) if row else 0.0
        except Exception:
            return True
        candidates = _memory_candidates(memory_dir, rel_prefix)
        for _rel, path in candidates:
            if path.exists():
                try:
                    if path.stat().st_mtime > last:
                        return True
                except OSError:
                    pass
        return False

    def _build_index(
        self,
        scope_key: str | None,
        memory_dir: Path,
        rel_prefix: str,
        embed_fn: Any,
        conn: sqlite3.Connection,
    ) -> int:
        scope_val = scope_key or "shared"
        conn.execute("DELETE FROM memory_index WHERE scope_key = ?", (scope_val,))
        candidates = _memory_candidates(memory_dir, rel_prefix)
        max_mtime = 0.0
        count = 0
        for rel_path, path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
                mtime = path.stat().st_mtime
                if mtime > max_mtime:
                    max_mtime = mtime
            except Exception:
                continue
            chunks = _chunk_text(text)
            if not chunks:
                continue
            try:
                vectors = embed_fn(chunks)
            except Exception as e:
                logger.warning(f"Memory vector embed failed for {path}: {e}")
                continue
            if len(vectors) != len(chunks):
                continue
            for i, (content, vec) in enumerate(zip(chunks, vectors)):
                if not vec:
                    continue
                blob = _embedding_to_blob(vec)
                conn.execute(
                    "INSERT INTO memory_index (scope_key, file_path, chunk_index, content, embedding, file_mtime) VALUES (?, ?, ?, ?, ?, ?)",
                    (scope_val, rel_path, i, content[:SNIPPET_MAX_CHARS * 2], blob, mtime),
                )
                count += 1
        conn.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)",
            (f"mtime_{scope_val}", str(max_mtime)),
        )
        conn.commit()
        return count

    def ensure_index(
        self,
        scope_key: str | None,
        config: Any,
        embed_fn: Any,
        force_rebuild: bool = False,
    ) -> bool:
        """Build or refresh index for scope; returns True if index is available."""
        memory_dir, rel_prefix = self._scope_dir(scope_key)
        if not memory_dir.is_dir():
            return False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            self._init_schema(conn)
            if force_rebuild or self._is_stale(conn, scope_key, memory_dir, rel_prefix):
                n = self._build_index(scope_key, memory_dir, rel_prefix, embed_fn, conn)
                if n > 0:
                    logger.debug(f"Memory vector index built for scope {scope_key or 'shared'}: {n} chunks")
            return True
        except Exception as e:
            logger.warning(f"Memory vector index ensure failed: {e}")
            return False
        finally:
            conn.close()

    def search(
        self,
        scope_key: str | None,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Return top_k hits by cosine similarity. Hit shape compatible with search_memory_files."""
        scope_val = scope_key or "shared"
        if not query_embedding:
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT file_path, chunk_index, content FROM memory_index WHERE scope_key = ?",
                (scope_val,),
            )
            rows = cur.fetchall()
            if not rows:
                return []
            cur2 = conn.execute(
                "SELECT embedding FROM memory_index WHERE scope_key = ? ORDER BY id",
                (scope_val,),
            )
            blobs = [r[0] for r in cur2.fetchall()]
            if len(blobs) != len(rows):
                return []
            scored: list[tuple[float, tuple[str, int, str]]] = []
            for (fp, ci, content), blob in zip(rows, blobs):
                vec = _blob_to_embedding(blob)
                sim = _cosine_sim(query_embedding, vec)
                scored.append((sim, (fp, ci, content)))
            scored.sort(key=lambda x: -x[0])
            hits = []
            for _sim, (file_path, chunk_index, content) in scored[:top_k]:
                content_str = content if len(content) <= SNIPPET_MAX_CHARS else content[: SNIPPET_MAX_CHARS - 3] + "..."
                hits.append({
                    "doc_id": file_path,
                    "source_type": "memory",
                    "source_url": "",
                    "file_path": file_path,
                    "title": Path(file_path).name,
                    "chunk_index": chunk_index,
                    "page": None,
                    "content": content_str,
                    "trace": {"doc_id": file_path, "source": file_path, "page": None},
                })
            return hits
        finally:
            conn.close()


async def search_memory_sqlite_vector(
    workspace: Path,
    config: Any,
    query: str,
    top_k: int = 10,
    scope_key: str | None = None,
) -> list[dict[str, Any]] | None:
    """
    Semantic memory search via SQLite vector index. Returns None if index unavailable or embedding not configured (caller should fallback to builtin grep).
    """
    from joyhousebot.services.retrieval.vector_optional import get_memory_embedding_provider

    provider = get_memory_embedding_provider(config)
    if provider is None:
        return None  # type: ignore[return-value]  # signal fallback

    store = MemoryVectorStore(workspace)

    def embed_sync(texts: list[str]) -> list[list[float]]:
        return provider.embed(texts)

    if not store.ensure_index(scope_key, config, embed_sync, force_rebuild=False):
        return None  # type: ignore[return-value]

    try:
        vectors = await provider.aembed([query])
    except Exception as e:
        logger.warning(f"Memory vector query embed failed: {e}")
        return None  # type: ignore[return-value]
    if not vectors:
        return None  # type: ignore[return-value]
    return store.search(scope_key, vectors[0], top_k)
