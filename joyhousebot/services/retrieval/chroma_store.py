"""Chroma-backed vector store for knowledge chunks (optional backend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joyhousebot.utils.helpers import ensure_dir

# Collection name shared between ingest and retrieve
KNOWLEDGE_COLLECTION = "joyhouse_knowledge"


def _chunk_id(doc_id: str, chunk_index: int) -> str:
    return f"{doc_id}:{chunk_index}"


class ChromaVectorStore:
    """Persist and search chunk vectors via Chroma (optional dependency)."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        ensure_dir(self.workspace / "knowledge" / "vector")
        self._path = str(self.workspace / "knowledge" / "vector")
        self._client = None
        self._collection = None

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
                self._client = chromadb.PersistentClient(path=self._path)
            except ImportError:
                raise RuntimeError("chromadb is not installed. Install with: pip install chromadb")
        return self._client

    def _get_collection(self):
        if self._collection is None:
            self._collection = self._get_client().get_or_create_collection(
                name=KNOWLEDGE_COLLECTION,
                metadata={"description": "joyhousebot knowledge chunks"},
            )
        return self._collection

    def index(
        self,
        doc_id: str,
        chunk_index: int,
        vector: list[float],
        meta: dict[str, Any],
    ) -> None:
        """Index one chunk. meta: doc_id, source_type, source_url, file_path, title, page, content (and optional fields)."""
        coll = self._get_collection()
        cid = _chunk_id(doc_id, chunk_index)
        # Chroma metadata values must be str, int, float or bool
        m = {
            "doc_id": str(meta.get("doc_id", doc_id)),
            "chunk_index": int(meta.get("chunk_index", chunk_index)),
            "source_type": str(meta.get("source_type", "")),
            "source_url": str(meta.get("source_url", "")),
            "file_path": str(meta.get("file_path", "")),
            "title": str(meta.get("title", "")),
            "page": int(meta.get("page") or 0),
            "content": str(meta.get("content", ""))[:100_000],  # cap size
        }
        coll.upsert(ids=[cid], embeddings=[vector], metadatas=[m])

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        source_type: str | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search by vector. Returns list of (chunk_id, score, meta). meta has doc_id, source_type, ..., content."""
        coll = self._get_collection()
        where = None
        if source_type:
            where = {"source_type": source_type}
        results = coll.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["metadatas", "distances"],
        )
        out: list[tuple[str, float, dict[str, Any]]] = []
        if not results or not results.get("ids"):
            return out
        ids = results["ids"][0]
        metadatas = results.get("metadatas") or [[]]
        distances = results.get("distances") or [[]]
        meta_list = metadatas[0] if metadatas else []
        dist_list = distances[0] if distances else []
        for i, cid in enumerate(ids):
            meta = (meta_list[i] if i < len(meta_list) else {}) or {}
            # Chroma returns distance (lower = closer); convert to similarity-like score (1 / (1 + d))
            d = dist_list[i] if i < len(dist_list) else 0.0
            score = 1.0 / (1.0 + float(d))
            out.append((cid, score, dict(meta)))
        return out
