"""PostgreSQL-backed multi-user knowledge document and chunk repository."""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from joyhousebot.services.retrieval.knowledge_base_repository import (
    KNOWLEDGE_BASE_DDL,
    KnowledgeBaseRepositoryMixin,
)
from joyhousebot.storage.json_codec import Jsonb


class KnowledgeRepository(KnowledgeBaseRepositoryMixin):
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
            CREATE TABLE IF NOT EXISTS knowledge_asset_events (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                data JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_ms BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_knowledge_asset_events_user
                ON knowledge_asset_events(user_id, created_at_ms DESC);
            """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341915,))
                connection.execute(ddl)
                connection.execute(KNOWLEDGE_BASE_DDL)

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
            connection.execute(
                """INSERT INTO knowledge_asset_events
                   (event_id,user_id,doc_id,event_type,actor_id,data,created_at_ms)
                   VALUES (%s,%s,%s,'indexed',%s,%s,%s)""",
                (
                    f"kae_{uuid.uuid4().hex}",
                    user_id,
                    doc_id,
                    f"runtime:{agent_id or 'shared'}",
                    Jsonb(
                        {
                            "title": title,
                            "source_type": source_type,
                            "source_url": source_url,
                            "chunk_count": len(chunks),
                        }
                    ),
                    now_ms,
                ),
            )

    def list_documents(
        self,
        *,
        user_id: str,
        knowledge_base_id: str | None = None,
        source_type: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List one owner's indexed sources without exposing chunk bodies."""
        clauses = ["d.user_id=%s"]
        params: list[Any] = [user_id]
        if knowledge_base_id:
            clauses.append(
                """EXISTS (SELECT 1 FROM knowledge_base_documents m
                            WHERE m.user_id=d.user_id AND m.doc_id=d.doc_id
                              AND m.knowledge_base_id=%s)"""
            )
            params.append(knowledge_base_id)
        if source_type:
            clauses.append("d.source_type=%s")
            params.append(source_type)
        if search and search.strip():
            clauses.append("(d.title ILIKE %s OR COALESCE(d.source_url,'') ILIKE %s)")
            pattern = f"%{search.strip()}%"
            params.extend((pattern, pattern))
        params.append(max(1, min(500, int(limit))))
        with self._connection() as connection:
            rows = connection.execute(
                f"""SELECT d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                           d.metadata,d.created_at_ms,d.updated_at_ms,
                           COUNT(c.doc_id) AS chunk_count,
                           COALESCE(SUM(octet_length(c.content)),0) AS size_bytes,
                           ARRAY(SELECT m.knowledge_base_id
                                   FROM knowledge_base_documents m
                                  WHERE m.user_id=d.user_id AND m.doc_id=d.doc_id
                                  ORDER BY m.created_at_ms) AS knowledge_base_ids
                      FROM knowledge_documents d
                 LEFT JOIN knowledge_chunks c ON c.doc_id=d.doc_id
                     WHERE {" AND ".join(clauses)}
                  GROUP BY d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                           d.metadata,d.created_at_ms,d.updated_at_ms
                  ORDER BY d.updated_at_ms DESC,d.title
                     LIMIT %s""",
                tuple(params),
            ).fetchall()
        return [self._document_summary(row) for row in rows]

    def summarize_documents(self, *, user_id: str) -> dict[str, Any]:
        """Return compact owner-scoped totals for the asset overview."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT COUNT(DISTINCT d.doc_id) AS total,
                          COUNT(c.doc_id) AS chunks,
                          COALESCE(SUM(octet_length(c.content)),0) AS size_bytes
                     FROM knowledge_documents d
                LEFT JOIN knowledge_chunks c ON c.doc_id=d.doc_id
                    WHERE d.user_id=%s""",
                (user_id,),
            ).fetchone()
            source_rows = connection.execute(
                """SELECT source_type,COUNT(*) AS count
                     FROM knowledge_documents WHERE user_id=%s GROUP BY source_type""",
                (user_id,),
            ).fetchall()
            base_row = connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_bases WHERE user_id=%s",
                (user_id,),
            ).fetchone()
        return {
            "bases": int(base_row["count"] or 0),
            "total": int(row["total"] or 0),
            "chunks": int(row["chunks"] or 0),
            "size_bytes": int(row["size_bytes"] or 0),
            "by_source": {
                str(source_row["source_type"]): int(source_row["count"])
                for source_row in source_rows
            },
        }

    def get_document(self, *, user_id: str, doc_id: str) -> dict[str, Any] | None:
        """Read one source and its indexed chunks after matching the owner."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                          d.metadata,d.created_at_ms,d.updated_at_ms,
                          COUNT(c.doc_id) AS chunk_count,
                          COALESCE(SUM(octet_length(c.content)),0) AS size_bytes,
                          ARRAY(SELECT m.knowledge_base_id
                                  FROM knowledge_base_documents m
                                 WHERE m.user_id=d.user_id AND m.doc_id=d.doc_id
                                 ORDER BY m.created_at_ms) AS knowledge_base_ids
                     FROM knowledge_documents d
                LEFT JOIN knowledge_chunks c ON c.doc_id=d.doc_id
                    WHERE d.user_id=%s AND d.doc_id=%s
                 GROUP BY d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                          d.metadata,d.created_at_ms,d.updated_at_ms""",
                (user_id, doc_id),
            ).fetchone()
            if row is None:
                return None
            chunk_rows = connection.execute(
                """SELECT chunk_index,page,content,created_at_ms
                     FROM knowledge_chunks
                    WHERE user_id=%s AND doc_id=%s ORDER BY chunk_index""",
                (user_id, doc_id),
            ).fetchall()
        return {
            **self._document_summary(row),
            "chunks": [
                {
                    "chunk_index": int(chunk["chunk_index"]),
                    "page": chunk["page"],
                    "content": str(chunk["content"]),
                    "created_at_ms": int(chunk["created_at_ms"]),
                }
                for chunk in chunk_rows
            ],
        }

    def delete_document(
        self, *, user_id: str, doc_id: str, actor_id: str
    ) -> dict[str, Any] | None:
        """Remove one owner-scoped source and retain an immutable audit event."""
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT doc_id,title,source_type,source_url
                     FROM knowledge_documents
                    WHERE user_id=%s AND doc_id=%s FOR UPDATE""",
                (user_id, doc_id),
            ).fetchone()
            if row is None:
                return None
            snapshot = {
                "title": str(row["title"]),
                "source_type": str(row["source_type"]),
                "source_url": str(row["source_url"] or ""),
            }
            connection.execute(
                "DELETE FROM knowledge_documents WHERE user_id=%s AND doc_id=%s",
                (user_id, doc_id),
            )
            connection.execute(
                """INSERT INTO knowledge_asset_events
                   (event_id,user_id,doc_id,event_type,actor_id,data,created_at_ms)
                   VALUES (%s,%s,%s,'deleted',%s,%s,%s)""",
                (
                    f"kae_{uuid.uuid4().hex}",
                    user_id,
                    doc_id,
                    actor_id,
                    Jsonb(snapshot),
                    now_ms,
                ),
            )
        return snapshot

    @staticmethod
    def _document_summary(row: Any) -> dict[str, Any]:
        metadata = row["metadata"]
        return {
            "doc_id": str(row["doc_id"]),
            "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
            "source_type": str(row["source_type"]),
            "source_url": str(row["source_url"] or ""),
            "title": str(row["title"]),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            "knowledge_base_ids": [str(value) for value in row["knowledge_base_ids"]],
            "chunk_count": int(row["chunk_count"] or 0),
            "size_bytes": int(row["size_bytes"] or 0),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
        }

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
