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
from joyhousebot.services.retrieval.knowledge_health_repository import (
    KnowledgeHealthRepositoryMixin,
)
from joyhousebot.services.retrieval.knowledge_maintenance_repository import (
    KNOWLEDGE_MAINTENANCE_DDL,
    KnowledgeMaintenanceRepositoryMixin,
)
from joyhousebot.services.retrieval.knowledge_revision_repository import (
    KNOWLEDGE_REVISION_DDL,
    KnowledgeRevisionRepositoryMixin,
)
from joyhousebot.services.retrieval.knowledge_vector_repository import (
    KNOWLEDGE_VECTOR_DDL,
    KnowledgeVectorRepositoryMixin,
)
from joyhousebot.storage.json_codec import Jsonb


class KnowledgeRepository(
    KnowledgeMaintenanceRepositoryMixin,
    KnowledgeVectorRepositoryMixin,
    KnowledgeRevisionRepositoryMixin,
    KnowledgeHealthRepositoryMixin,
    KnowledgeBaseRepositoryMixin,
):
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
                revision_id TEXT,
                page INTEGER,
                section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                block_type TEXT NOT NULL DEFAULT 'text',
                char_start INTEGER,
                char_end INTEGER,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL DEFAULT '',
                search_vector TSVECTOR GENERATED ALWAYS AS
                    (to_tsvector('simple', content)) STORED,
                created_at_ms BIGINT NOT NULL,
                PRIMARY KEY(doc_id, chunk_index),
                CHECK (char_start IS NULL OR char_start >= 0),
                CHECK (char_end IS NULL OR char_end >= COALESCE(char_start,0))
            );
            ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS revision_id TEXT;
            ALTER TABLE knowledge_chunks
                ADD COLUMN IF NOT EXISTS section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
            ALTER TABLE knowledge_chunks
                ADD COLUMN IF NOT EXISTS block_type TEXT NOT NULL DEFAULT 'text';
            ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS char_start INTEGER;
            ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS char_end INTEGER;
            ALTER TABLE knowledge_chunks
                ADD COLUMN IF NOT EXISTS content_sha256 TEXT NOT NULL DEFAULT '';
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
            CREATE TABLE IF NOT EXISTS knowledge_document_scopes (
                user_id TEXT NOT NULL,
                doc_id TEXT NOT NULL REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE,
                scope_type TEXT NOT NULL,
                scope_ref TEXT NOT NULL,
                revision_id TEXT,
                created_at_ms BIGINT NOT NULL,
                PRIMARY KEY(user_id,doc_id,scope_type,scope_ref)
            );
            CREATE INDEX IF NOT EXISTS ix_knowledge_document_scopes_lookup
                ON knowledge_document_scopes(user_id,scope_type,scope_ref,doc_id);
            """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341915,))
                connection.execute(ddl)
                connection.execute(KNOWLEDGE_BASE_DDL)
                connection.execute(KNOWLEDGE_REVISION_DDL)
                connection.execute(KNOWLEDGE_VECTOR_DDL)
                connection.execute(KNOWLEDGE_MAINTENANCE_DDL)
                connection.execute(
                    """UPDATE knowledge_chunks active
                          SET revision_id=document.active_revision_id,
                              section_path=revision.section_path,
                              block_type=revision.block_type,
                              char_start=revision.char_start,
                              char_end=revision.char_end,
                              content_sha256=revision.content_sha256
                         FROM knowledge_documents document,
                              knowledge_revision_chunks revision
                        WHERE active.user_id=document.user_id
                          AND active.doc_id=document.doc_id
                          AND revision.user_id=active.user_id
                          AND revision.doc_id=active.doc_id
                          AND revision.revision_id=document.active_revision_id
                          AND revision.chunk_index=active.chunk_index
                          AND (active.revision_id IS NULL OR active.content_sha256='')"""
                )
                connection.execute(
                    """INSERT INTO knowledge_document_scopes
                           (user_id,doc_id,scope_type,scope_ref,revision_id,created_at_ms)
                       SELECT document.user_id,document.doc_id,'collection',scope.value,
                              document.active_revision_id,document.updated_at_ms
                         FROM knowledge_documents document
                         CROSS JOIN LATERAL jsonb_array_elements_text(
                             CASE WHEN jsonb_typeof(document.metadata->'collection_refs')='array'
                                  THEN document.metadata->'collection_refs'
                                  ELSE '[]'::jsonb END
                         ) AS scope(value)
                       ON CONFLICT(user_id,doc_id,scope_type,scope_ref) DO NOTHING"""
                )

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
        app_installation_id: str | None = None,
    ) -> None:
        revision_id = self.stage_index_revision(
            doc_id=doc_id,
            user_id=user_id,
            agent_id=agent_id,
            source_type=source_type,
            source_url=source_url,
            title=title,
            chunks=chunks,
            metadata=metadata,
            app_installation_id=app_installation_id,
        )
        actor_id = f"runtime:{agent_id or 'shared'}"
        self.mark_index_revision_ready(
            user_id=user_id,
            doc_id=doc_id,
            revision_id=revision_id,
            actor_id=actor_id,
        )
        self.activate_index_revision(
            user_id=user_id,
            doc_id=doc_id,
            revision_id=revision_id,
            actor_id=actor_id,
        )
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
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
                            "revision_id": revision_id,
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
        app_installation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List one owner's indexed sources without exposing chunk bodies."""
        clauses = ["d.user_id=%s", "d.app_installation_id IS NOT DISTINCT FROM %s"]
        params: list[Any] = [user_id, app_installation_id]
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
                           d.source_system,d.source_id,d.source_version,
                           d.source_generation,d.source_status,d.content_sha256,
                           d.active_revision_id,d.index_status,
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
                           d.source_system,d.source_id,d.source_version,
                           d.source_generation,d.source_status,d.content_sha256,
                           d.active_revision_id,d.index_status,
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

    def get_document(
        self,
        *,
        user_id: str,
        doc_id: str,
        app_installation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Read one source and its indexed chunks after matching the owner."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                          d.source_system,d.source_id,d.source_version,
                          d.source_generation,d.source_status,d.content_sha256,
                          d.active_revision_id,d.index_status,
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
                      AND d.app_installation_id IS NOT DISTINCT FROM %s
                 GROUP BY d.doc_id,d.agent_id,d.source_type,d.source_url,d.title,
                          d.source_system,d.source_id,d.source_version,
                          d.source_generation,d.source_status,d.content_sha256,
                          d.active_revision_id,d.index_status,
                          d.metadata,d.created_at_ms,d.updated_at_ms""",
                (user_id, doc_id, app_installation_id),
            ).fetchone()
            if row is None:
                return None
            chunk_rows = connection.execute(
                """SELECT chunk_index,revision_id,page,section_path,block_type,
                          char_start,char_end,content,content_sha256,created_at_ms
                     FROM knowledge_chunks
                    WHERE user_id=%s AND doc_id=%s ORDER BY chunk_index""",
                (user_id, doc_id),
            ).fetchall()
        return {
            **self._document_summary(row),
            "chunks": [
                {
                    "chunk_index": int(chunk["chunk_index"]),
                    "revision_id": str(chunk["revision_id"] or ""),
                    "page": chunk["page"],
                    "section_path": [str(value) for value in chunk["section_path"]],
                    "block_type": str(chunk["block_type"]),
                    "char_start": chunk["char_start"],
                    "char_end": chunk["char_end"],
                    "content": str(chunk["content"]),
                    "content_sha256": str(chunk["content_sha256"]),
                    "created_at_ms": int(chunk["created_at_ms"]),
                }
                for chunk in chunk_rows
            ],
        }

    def get_document_by_source(
        self,
        *,
        user_id: str,
        source_system: str,
        source_id: str,
        app_installation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve an indexed document through the source system's stable identity."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT doc_id FROM knowledge_documents
                    WHERE user_id=%s AND source_system=%s AND source_id=%s
                      AND app_installation_id IS NOT DISTINCT FROM %s""",
                (user_id, source_system, source_id, app_installation_id),
            ).fetchone()
        if row is None:
            return None
        document = self.get_document(
            user_id=user_id,
            doc_id=str(row["doc_id"]),
            app_installation_id=app_installation_id,
        )
        if document is not None:
            document.pop("chunks", None)
        return document

    def delete_document(
        self,
        *,
        user_id: str,
        doc_id: str,
        actor_id: str,
        app_installation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Remove one owner-scoped source and retain an immutable audit event."""
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """SELECT doc_id,title,source_type,source_url
                     FROM knowledge_documents
                    WHERE user_id=%s AND doc_id=%s
                      AND app_installation_id IS NOT DISTINCT FROM %s FOR UPDATE""",
                (user_id, doc_id, app_installation_id),
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
            "source_system": str(row["source_system"]),
            "source_id": str(row["source_id"]),
            "source_version": str(row["source_version"]),
            "source_generation": int(row["source_generation"]),
            "source_status": str(row["source_status"]),
            "content_sha256": str(row["content_sha256"]),
            "active_revision_id": (
                str(row["active_revision_id"]) if row["active_revision_id"] else None
            ),
            "index_status": str(row["index_status"]),
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
        knowledge_base_id: str | None = None,
        collection_ref: str | None = None,
        app_installation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.user_id=%s", "d.source_status<>'archived'"]
        params: list[Any] = [user_id]
        # Namespace filter rides the documents join: NULL means the user's
        # personal library, a non-NULL installation id means that App's
        # library. Personal searches never see App documents and vice versa.
        clauses.append("d.app_installation_id IS NOT DISTINCT FROM %s")
        params.append(app_installation_id)
        if source_type:
            clauses.append("d.source_type=%s")
            params.append(source_type)
        if doc_id:
            clauses.append("c.doc_id=%s")
            params.append(doc_id)
        if knowledge_base_id:
            clauses.append(
                """EXISTS (SELECT 1 FROM knowledge_base_documents membership
                            WHERE membership.user_id=d.user_id
                              AND membership.doc_id=d.doc_id
                              AND membership.knowledge_base_id=%s)"""
            )
            params.append(knowledge_base_id)
        if collection_ref:
            clauses.append(
                """EXISTS (SELECT 1 FROM knowledge_document_scopes scope
                            WHERE scope.user_id=d.user_id AND scope.doc_id=d.doc_id
                              AND scope.scope_type='collection' AND scope.scope_ref=%s)"""
            )
            params.append(collection_ref)
        params = [query, *params, query, f"%{query}%", f"%{query}%", top_k]
        sql = f"""SELECT c.doc_id,c.chunk_index,c.revision_id,c.page,c.section_path,
                       c.block_type,c.char_start,c.char_end,c.content,c.content_sha256,
                       d.source_type,d.source_url,d.title,d.source_system,d.source_id,
                       d.source_version,d.source_generation,d.active_revision_id,
                       ts_rank(c.search_vector,websearch_to_tsquery('simple',%s)) AS rank
                FROM knowledge_chunks c JOIN knowledge_documents d USING(doc_id)
                WHERE {" AND ".join(clauses)}
                  AND (c.search_vector @@ websearch_to_tsquery('simple',%s)
                       OR c.content ILIKE %s OR d.title ILIKE %s)
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
                "source_system": str(row["source_system"]),
                "source_id": str(row["source_id"]),
                "source_version": str(row["source_version"]),
                "source_generation": int(row["source_generation"]),
                "revision_id": str(row["revision_id"] or row["active_revision_id"] or ""),
                "chunk_index": int(row["chunk_index"]),
                "page": row["page"],
                "section_path": [str(value) for value in row["section_path"]],
                "block_type": str(row["block_type"]),
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "content_sha256": str(row["content_sha256"]),
                "content": str(row["content"]),
                "rank": float(row["rank"] or 0),
                "trace": {
                    "doc_id": str(row["doc_id"]),
                    "revision_id": str(row["revision_id"] or row["active_revision_id"] or ""),
                    "source": str(row["source_url"] or ""),
                    "page": row["page"],
                    "section_path": [str(value) for value in row["section_path"]],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                },
            }
            for row in rows
        ]
