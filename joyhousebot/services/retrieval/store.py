"""SQLite FTS5-backed retrieval store for knowledge chunks with metadata and evidence trace."""

import sqlite3
from pathlib import Path
from typing import Any

from joyhousebot.utils.helpers import ensure_dir


class RetrievalStore:
    """Full-text search over ingested knowledge using SQLite FTS5. Supports metadata filter and BM25-style ranking."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        ensure_dir(self.workspace / "knowledge")
        self.db_path = self.workspace / "knowledge" / "retrieval.db"
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT,
                    file_path TEXT,
                    title TEXT,
                    chunk_index INTEGER NOT NULL,
                    page INTEGER,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_doc_id ON knowledge(doc_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_source_type ON knowledge(source_type)")
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    doc_id, source_type, source_url, title, content,
                    tokenize='unicode61'
                )
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
                    INSERT INTO knowledge_fts(rowid, doc_id, source_type, source_url, title, content)
                    VALUES (new.id, new.doc_id, new.source_type, new.source_url, new.title, new.content);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
                    INSERT INTO knowledge_fts(knowledge_fts, rowid, doc_id, source_type, source_url, title, content)
                    VALUES ('delete', old.id, old.doc_id, old.source_type, old.source_url, old.title, old.content);
                END
            """)
            conn.commit()

    def index_chunk(
        self,
        doc_id: str,
        source_type: str,
        source_url: str,
        title: str,
        chunk_index: int,
        page: int | None,
        content: str,
        file_path: str = "",
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO knowledge (doc_id, source_type, source_url, file_path, title, chunk_index, page, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, source_type, source_url or "", file_path or "", title, chunk_index, page or 0, content),
            )
            conn.commit()

    def index_doc(self, doc_id: str, source_type: str, source_url: str, title: str, file_path: str, chunks: list[dict]) -> None:
        """Index all chunks of a document. chunks: list of {text, page}."""
        for i, c in enumerate(chunks):
            self.index_chunk(
                doc_id=doc_id,
                source_type=source_type,
                source_url=source_url,
                title=title,
                chunk_index=i,
                page=c.get("page"),
                content=c.get("text", ""),
                file_path=file_path,
            )

    def search(
        self,
        query: str,
        top_k: int = 10,
        source_type: str | None = None,
        doc_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search with optional metadata filter. Returns list of hits with evidence trace."""
        if not query or not query.strip():
            return []

        query_clean = query.strip().replace('"', '""')
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = """
                SELECT k.id, k.doc_id, k.source_type, k.source_url, k.file_path, k.title, k.chunk_index, k.page, k.content
                FROM knowledge_fts f
                JOIN knowledge k ON k.id = f.rowid
                WHERE knowledge_fts MATCH ?
            """
            params: list[Any] = [query_clean]
            if source_type:
                sql += " AND k.source_type = ?"
                params.append(source_type)
            if doc_id:
                sql += " AND k.doc_id = ?"
                params.append(doc_id)
            sql += " ORDER BY bm25(knowledge_fts) LIMIT ?"
            params.append(top_k)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

        return [
            {
                "doc_id": r["doc_id"],
                "source_type": r["source_type"],
                "source_url": r["source_url"] or "",
                "file_path": r["file_path"] or "",
                "title": r["title"] or "",
                "chunk_index": r["chunk_index"],
                "page": r["page"] if r["page"] else None,
                "content": r["content"],
                "trace": {
                    "doc_id": r["doc_id"],
                    "source": r["source_url"] or r["file_path"],
                    "page": r["page"],
                },
            }
            for r in rows
        ]
