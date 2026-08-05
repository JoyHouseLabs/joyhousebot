"""PostgreSQL-backed multi-user knowledge document and chunk repository."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from joyhousebot.storage.json_codec import Jsonb


class KnowledgeRepository:
    """Persist user-scoped knowledge; PostgreSQL provides indexed full-text search."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("KnowledgeRepository requires PostgreSQL runtime store")
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def migrate(self) -> None:
        ddl = """
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                doc_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                agent_id TEXT,
                source_type TEXT NOT NULL,
                source_url TEXT,
                title TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_ms BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_knowledge_documents_user
                ON knowledge_documents(user_id, updated_at_ms DESC);
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                doc_id TEXT NOT NULL REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                page INTEGER,
                content TEXT NOT NULL,
                search_vector TSVECTOR GENERATED ALWAYS AS
                    (to_tsvector('simple', content)) STORED,
                created_at_ms BIGINT NOT NULL,
                PRIMARY KEY(doc_id, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_user
                ON knowledge_chunks(user_id, doc_id, chunk_index);
            CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_search
                ON knowledge_chunks USING GIN(search_vector);
            """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341915,))
                connection.execute(ddl)

    def index_document(
        self,
        *,
        doc_id: str,
        user_id: str,
        agent_id: str | None,
        source_type: str,
        source_url: str | None,
        title: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM knowledge_documents WHERE doc_id=%s AND user_id=%s",
                (doc_id, user_id),
            )
            connection.execute(
                """INSERT INTO knowledge_documents
                   (doc_id,user_id,agent_id,source_type,source_url,title,metadata,created_at_ms,updated_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (doc_id, user_id, agent_id, source_type, source_url, title, Jsonb(metadata or {}), now_ms, now_ms),
            )
            chunk_query = """INSERT INTO knowledge_chunks
                (doc_id,chunk_index,user_id,page,content,created_at_ms)
                VALUES (%s,%s,%s,%s,%s,%s)"""
            for index, chunk in enumerate(chunks):
                connection.execute(
                    chunk_query,
                    (
                        doc_id,
                        index,
                        user_id,
                        chunk.get("page"),
                        str(chunk.get("text") or ""),
                        now_ms,
                    ),
                )

    def search(
        self,
        *,
        user_id: str,
        query: str,
        top_k: int,
        source_type: str | None = None,
        doc_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.user_id=%s"]
        params: list[Any] = [user_id]
        if source_type:
            clauses.append("d.source_type=%s")
            params.append(source_type)
        if doc_id:
            clauses.append("c.doc_id=%s")
            params.append(doc_id)
        params = [query, *params, query, f"%{query}%", top_k]
        sql = f"""SELECT c.doc_id,c.chunk_index,c.page,c.content,
                       d.source_type,d.source_url,d.title,
                       ts_rank(c.search_vector,websearch_to_tsquery('simple',%s)) AS rank
                FROM knowledge_chunks c JOIN knowledge_documents d USING(doc_id)
                WHERE {" AND ".join(clauses)}
                  AND (c.search_vector @@ websearch_to_tsquery('simple',%s)
                       OR c.content ILIKE %s)
                ORDER BY rank DESC,c.created_at_ms DESC LIMIT %s"""
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            {
                "doc_id": str(row["doc_id"]),
                "source_type": str(row["source_type"]),
                "source_url": str(row["source_url"] or ""),
                "file_path": "",
                "title": str(row["title"]),
                "chunk_index": int(row["chunk_index"]),
                "page": row["page"],
                "content": str(row["content"]),
                "trace": {
                    "doc_id": str(row["doc_id"]),
                    "source": str(row["source_url"] or ""),
                    "page": row["page"],
                },
            }
            for row in rows
        ]
