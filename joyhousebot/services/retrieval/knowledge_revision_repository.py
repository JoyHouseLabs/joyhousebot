"""Immutable Knowledge index revisions and atomic active-index promotion."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from joyhousebot.storage.json_codec import Jsonb

KNOWLEDGE_REVISION_DDL = r"""
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS source_system TEXT NOT NULL DEFAULT 'runtime';
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS source_id TEXT;
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS source_version TEXT NOT NULL DEFAULT '1';
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS source_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS source_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS content_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS active_revision_id TEXT;
ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'ready';
UPDATE knowledge_documents SET source_id=doc_id WHERE source_id IS NULL;
ALTER TABLE knowledge_documents ALTER COLUMN source_id SET NOT NULL;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS app_installation_id TEXT;
-- Identity is namespaced: personal documents keep app_installation_id NULL
-- and remain byte-for-byte equivalent to the previous unique index; App
-- installations own their own source-id space. An installation belongs to
-- exactly one user, so the app index needs no user column.
DROP INDEX IF EXISTS ux_knowledge_documents_source_ref;
CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_documents_source_ref
    ON knowledge_documents(user_id,source_system,source_id)
    WHERE app_installation_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_documents_app_source_ref
    ON knowledge_documents(app_installation_id,source_system,source_id)
    WHERE app_installation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_app
    ON knowledge_documents(app_installation_id,updated_at_ms DESC)
    WHERE app_installation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_index_revisions (
    revision_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    source_generation BIGINT NOT NULL DEFAULT 1,
    content_sha256 TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'note',
    source_url TEXT,
    title TEXT NOT NULL DEFAULT '',
    source_status TEXT NOT NULL DEFAULT 'active',
    document_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    index_profile_id TEXT NOT NULL DEFAULT 'lexical-v1',
    parser_id TEXT NOT NULL DEFAULT 'preparsed',
    parser_version TEXT NOT NULL DEFAULT '1',
    chunker_id TEXT NOT NULL DEFAULT 'provided-chunks',
    chunker_version TEXT NOT NULL DEFAULT '1',
    embedding_profile_id TEXT,
    status TEXT NOT NULL,
    run_id TEXT,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at_ms BIGINT NOT NULL,
    ready_at_ms BIGINT,
    activated_at_ms BIGINT,
    failed_at_ms BIGINT,
    CHECK (status IN ('staging','ready','active','superseded','failed')),
    CHECK (source_generation >= 0),
    CHECK (source_status IN ('inbox','active','archived')),
    UNIQUE(user_id,doc_id,revision_id)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_index_revisions_document
    ON knowledge_index_revisions(user_id,doc_id,created_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_index_revisions_run
    ON knowledge_index_revisions(user_id,run_id) WHERE run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_revision_chunks (
    revision_id TEXT NOT NULL REFERENCES knowledge_index_revisions(revision_id)
        ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page INTEGER,
    section_path TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    block_type TEXT NOT NULL DEFAULT 'text',
    char_start INTEGER,
    char_end INTEGER,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS
        (to_tsvector('simple', content)) STORED,
    created_at_ms BIGINT NOT NULL,
    PRIMARY KEY(revision_id,chunk_index),
    CHECK (char_start IS NULL OR char_start >= 0),
    CHECK (char_end IS NULL OR char_end >= COALESCE(char_start,0))
);
CREATE INDEX IF NOT EXISTS ix_knowledge_revision_chunks_document
    ON knowledge_revision_chunks(user_id,doc_id,revision_id,chunk_index);
CREATE INDEX IF NOT EXISTS ix_knowledge_revision_chunks_search
    ON knowledge_revision_chunks USING GIN(search_vector);

INSERT INTO knowledge_index_revisions (
    revision_id,doc_id,user_id,source_version,content_sha256,status,
    metadata,created_at_ms,ready_at_ms,activated_at_ms
)
SELECT 'legacy_'||md5(document.doc_id),document.doc_id,document.user_id,
       document.source_version,document.content_sha256,'active',
       jsonb_build_object('migration','legacy-active-projection'),
       document.created_at_ms,document.updated_at_ms,document.updated_at_ms
  FROM knowledge_documents document
 WHERE NOT EXISTS (
       SELECT 1 FROM knowledge_index_revisions revision
        WHERE revision.doc_id=document.doc_id AND revision.user_id=document.user_id
 );
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS source_generation BIGINT NOT NULL DEFAULT 1;
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'note';
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS source_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS document_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE knowledge_index_revisions
    ADD COLUMN IF NOT EXISTS embedding_profile_id TEXT;
UPDATE knowledge_index_revisions revision
   SET source_type=document.source_type,source_url=document.source_url,
       title=document.title,source_status=document.source_status,
       document_metadata=document.metadata
  FROM knowledge_documents document
 WHERE revision.doc_id=document.doc_id AND revision.user_id=document.user_id
   AND revision.title='';
INSERT INTO knowledge_revision_chunks (
    revision_id,doc_id,user_id,chunk_index,page,content,content_sha256,created_at_ms
)
SELECT 'legacy_'||md5(chunk.doc_id),chunk.doc_id,chunk.user_id,
       chunk.chunk_index,chunk.page,chunk.content,
       md5(chunk.content),chunk.created_at_ms
  FROM knowledge_chunks chunk
  JOIN knowledge_index_revisions revision
    ON revision.revision_id='legacy_'||md5(chunk.doc_id)
ON CONFLICT(revision_id,chunk_index) DO NOTHING;
UPDATE knowledge_documents document
   SET active_revision_id='legacy_'||md5(document.doc_id),index_status='ready'
 WHERE document.active_revision_id IS NULL
   AND EXISTS (
       SELECT 1 FROM knowledge_index_revisions revision
        WHERE revision.revision_id='legacy_'||md5(document.doc_id)
   );
"""


class KnowledgeRevisionRepositoryMixin:
    """Build immutable revisions and promote only verified data to live search."""

    @staticmethod
    def _content_digest(chunks: list[dict[str, Any]]) -> str:
        digest = hashlib.sha256()
        for chunk in chunks:
            digest.update(str(chunk.get("text") or "").encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def stage_index_revision(
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
        source_system: str = "runtime",
        source_id: str | None = None,
        source_version: str = "1",
        source_generation: int = 1,
        source_status: str = "active",
        index_profile_id: str = "lexical-v1",
        parser_id: str = "preparsed",
        parser_version: str = "1",
        chunker_id: str = "provided-chunks",
        chunker_version: str = "1",
        embedding_profile_id: str | None = None,
        run_id: str | None = None,
        app_installation_id: str | None = None,
    ) -> str:
        now_ms = int(time.time() * 1000)
        revision_id = f"krev_{uuid.uuid4().hex}"
        resolved_source_id = source_id or doc_id
        resolved_generation = int(source_generation)
        if resolved_generation < 1:
            raise ValueError("knowledge source_generation must be positive")
        if source_status not in {"inbox", "active", "archived"}:
            raise ValueError("invalid knowledge source_status")
        content_sha256 = self._content_digest(chunks)
        with self._connection() as connection:
            conflict = connection.execute(
                """SELECT doc_id FROM knowledge_documents
                    WHERE user_id=%s AND source_system=%s AND source_id=%s
                      AND app_installation_id IS NOT DISTINCT FROM %s
                      AND doc_id<>%s""",
                (
                    user_id,
                    source_system,
                    resolved_source_id,
                    app_installation_id,
                    doc_id,
                ),
            ).fetchone()
            if conflict:
                raise ValueError("knowledge source reference already belongs to another document")
            connection.execute(
                """INSERT INTO knowledge_documents
                       (doc_id,user_id,agent_id,source_type,source_url,title,metadata,
                        source_system,source_id,source_version,source_generation,
                        source_status,content_sha256,index_status,app_installation_id,
                        created_at_ms,updated_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'0',0,'active','',
                           'indexing',%s,%s,%s)
                   ON CONFLICT(doc_id) DO NOTHING""",
                (
                    doc_id,
                    user_id,
                    agent_id,
                    source_type,
                    source_url,
                    title,
                    Jsonb(metadata or {}),
                    source_system,
                    resolved_source_id,
                    app_installation_id,
                    now_ms,
                    now_ms,
                ),
            )
            document = connection.execute(
                "SELECT 1 FROM knowledge_documents WHERE doc_id=%s AND user_id=%s",
                (doc_id, user_id),
            ).fetchone()
            if document is None:
                raise PermissionError("knowledge document belongs to another user")
            connection.execute(
                """UPDATE knowledge_documents AS document
                      SET index_status='indexing',
                          source_generation=GREATEST(source_generation,%s),
                          updated_at_ms=%s
                    WHERE doc_id=%s AND user_id=%s AND source_generation<=%s""",
                (
                    resolved_generation,
                    now_ms,
                    doc_id,
                    user_id,
                    resolved_generation,
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_index_revisions
                       (revision_id,doc_id,user_id,source_version,source_generation,
                        content_sha256,source_type,source_url,title,source_status,
                        document_metadata,
                        index_profile_id,parser_id,parser_version,chunker_id,
                        chunker_version,embedding_profile_id,status,run_id,metadata,
                        created_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           'staging',%s,%s,%s)""",
                (
                    revision_id,
                    doc_id,
                    user_id,
                    str(source_version),
                    resolved_generation,
                    content_sha256,
                    source_type,
                    source_url,
                    title,
                    source_status,
                    Jsonb(metadata or {}),
                    index_profile_id,
                    parser_id,
                    parser_version,
                    chunker_id,
                    chunker_version,
                    embedding_profile_id,
                    run_id,
                    Jsonb(metadata or {}),
                    now_ms,
                ),
            )
            for index, chunk in enumerate(chunks):
                content = str(chunk.get("text") or "")
                connection.execute(
                    """INSERT INTO knowledge_revision_chunks
                           (revision_id,doc_id,user_id,chunk_index,page,section_path,
                            block_type,char_start,char_end,content,content_sha256,
                            created_at_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        revision_id,
                        doc_id,
                        user_id,
                        index,
                        chunk.get("page"),
                        list(chunk.get("section_path") or []),
                        str(chunk.get("block_type") or "text"),
                        chunk.get("char_start"),
                        chunk.get("char_end"),
                        content,
                        hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        now_ms,
                    ),
                )
            connection.execute(
                """INSERT INTO knowledge_asset_events
                       (event_id,user_id,doc_id,event_type,actor_id,data,created_at_ms)
                   VALUES (%s,%s,%s,'revision_staged',%s,%s,%s)""",
                (
                    f"kae_{uuid.uuid4().hex}",
                    user_id,
                    doc_id,
                    f"runtime:{agent_id or 'shared'}",
                    Jsonb(
                        {"revision_id": revision_id, "chunk_count": len(chunks), "run_id": run_id}
                    ),
                    now_ms,
                ),
            )
        return revision_id

    def mark_index_revision_ready(
        self, *, user_id: str, doc_id: str, revision_id: str, actor_id: str
    ) -> bool:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE knowledge_index_revisions revision
                      SET status='ready',ready_at_ms=%s
                    WHERE revision.user_id=%s AND revision.doc_id=%s
                      AND revision.revision_id=%s AND revision.status='staging'
                      AND (
                          revision.embedding_profile_id IS NULL OR
                          (SELECT COUNT(*) FROM knowledge_revision_embeddings embedding
                            WHERE embedding.revision_id=revision.revision_id
                              AND embedding.embedding_profile_id=
                                  revision.embedding_profile_id)=
                          (SELECT COUNT(*) FROM knowledge_revision_chunks chunk
                            WHERE chunk.revision_id=revision.revision_id)
                      )
                    RETURNING revision_id""",
                (now_ms, user_id, doc_id, revision_id),
            ).fetchone()
            if row is None:
                raise ValueError("knowledge revision is not staging or embeddings are incomplete")
            self._record_revision_event(
                connection, user_id, doc_id, revision_id, "revision_ready", actor_id, now_ms
            )
        return True

    def activate_index_revision(
        self, *, user_id: str, doc_id: str, revision_id: str, actor_id: str
    ) -> None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            document = connection.execute(
                """SELECT active_revision_id,source_generation FROM knowledge_documents
                    WHERE user_id=%s AND doc_id=%s FOR UPDATE""",
                (user_id, doc_id),
            ).fetchone()
            revision = connection.execute(
                """SELECT * FROM knowledge_index_revisions
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                      AND status='ready' FOR UPDATE""",
                (user_id, doc_id, revision_id),
            ).fetchone()
            if document is None or revision is None:
                raise ValueError("ready knowledge revision not found")
            if int(revision["source_generation"]) < int(document["source_generation"]):
                connection.execute(
                    """UPDATE knowledge_index_revisions
                          SET status='superseded'
                        WHERE user_id=%s AND doc_id=%s AND revision_id=%s""",
                    (user_id, doc_id, revision_id),
                )
                self._record_revision_event(
                    connection,
                    user_id,
                    doc_id,
                    revision_id,
                    "revision_skipped_stale",
                    actor_id,
                    now_ms,
                    {"active_source_generation": int(document["source_generation"])},
                )
                return False
            previous_revision_id = document["active_revision_id"]
            connection.execute(
                "DELETE FROM knowledge_chunks WHERE user_id=%s AND doc_id=%s",
                (user_id, doc_id),
            )
            connection.execute(
                """INSERT INTO knowledge_chunks
                       (doc_id,chunk_index,user_id,revision_id,page,section_path,
                        block_type,char_start,char_end,content,content_sha256,created_at_ms)
                   SELECT doc_id,chunk_index,user_id,revision_id,page,section_path,
                          block_type,char_start,char_end,content,content_sha256,%s
                     FROM knowledge_revision_chunks
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                    ORDER BY chunk_index""",
                (now_ms, user_id, doc_id, revision_id),
            )
            connection.execute(
                """DELETE FROM knowledge_document_scopes
                    WHERE user_id=%s AND doc_id=%s""",
                (user_id, doc_id),
            )
            collection_refs = revision["document_metadata"].get("collection_refs", [])
            if isinstance(collection_refs, list):
                for scope_ref in dict.fromkeys(
                    str(value).strip() for value in collection_refs if str(value).strip()
                ):
                    connection.execute(
                        """INSERT INTO knowledge_document_scopes
                               (user_id,doc_id,scope_type,scope_ref,revision_id,created_at_ms)
                           VALUES (%s,%s,'collection',%s,%s,%s)""",
                        (user_id, doc_id, scope_ref, revision_id, now_ms),
                    )
            if previous_revision_id:
                connection.execute(
                    """UPDATE knowledge_index_revisions SET status='superseded'
                        WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                          AND status='active'""",
                    (user_id, doc_id, previous_revision_id),
                )
            connection.execute(
                """UPDATE knowledge_index_revisions
                      SET status='active',activated_at_ms=%s
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s""",
                (now_ms, user_id, doc_id, revision_id),
            )
            connection.execute(
                """UPDATE knowledge_documents
                      SET source_type=%s,source_url=%s,title=%s,metadata=%s,
                          source_version=%s,source_generation=%s,source_status=%s,
                          content_sha256=%s,active_revision_id=%s,
                          index_status='ready',updated_at_ms=%s
                    WHERE user_id=%s AND doc_id=%s""",
                (
                    revision["source_type"],
                    revision["source_url"],
                    revision["title"],
                    Jsonb(dict(revision["document_metadata"] or {})),
                    revision["source_version"],
                    int(revision["source_generation"]),
                    revision["source_status"],
                    revision["content_sha256"],
                    revision_id,
                    now_ms,
                    user_id,
                    doc_id,
                ),
            )
            self._record_revision_event(
                connection,
                user_id,
                doc_id,
                revision_id,
                "revision_activated",
                actor_id,
                now_ms,
                {"previous_revision_id": previous_revision_id},
            )
        return True

    def fail_index_revision(
        self,
        *,
        user_id: str,
        doc_id: str,
        revision_id: str,
        actor_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE knowledge_index_revisions
                      SET status='failed',error_code=%s,error_message=%s,failed_at_ms=%s
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                      AND status IN ('staging','ready') RETURNING revision_id""",
                (error_code, error_message[:4000], now_ms, user_id, doc_id, revision_id),
            ).fetchone()
            if row is None:
                raise ValueError("knowledge revision cannot fail from its current state")
            connection.execute(
                """UPDATE knowledge_documents AS document
                      SET index_status=CASE WHEN active_revision_id IS NULL THEN 'failed' ELSE 'ready' END,
                          source_type=CASE WHEN active_revision_id IS NULL
                                           THEN revision.source_type ELSE document.source_type END,
                          source_url=CASE WHEN active_revision_id IS NULL
                                          THEN revision.source_url ELSE document.source_url END,
                          title=CASE WHEN active_revision_id IS NULL
                                     THEN revision.title ELSE document.title END,
                          metadata=CASE WHEN active_revision_id IS NULL
                                        THEN revision.document_metadata ELSE document.metadata END,
                          source_version=CASE WHEN active_revision_id IS NULL
                                              THEN revision.source_version
                                              ELSE document.source_version END,
                          source_status=CASE WHEN active_revision_id IS NULL
                                             THEN revision.source_status
                                             ELSE document.source_status END,
                          updated_at_ms=%s
                     FROM knowledge_index_revisions revision
                    WHERE document.user_id=%s AND document.doc_id=%s
                      AND revision.user_id=document.user_id
                      AND revision.doc_id=document.doc_id
                      AND revision.revision_id=%s""",
                (now_ms, user_id, doc_id, revision_id),
            )
            self._record_revision_event(
                connection,
                user_id,
                doc_id,
                revision_id,
                "revision_failed",
                actor_id,
                now_ms,
                {"error_code": error_code},
            )

    def list_index_revisions(self, *, user_id: str, doc_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT revision.*,
                          (SELECT COUNT(*) FROM knowledge_revision_chunks chunk
                            WHERE chunk.revision_id=revision.revision_id) AS chunk_count
                     FROM knowledge_index_revisions revision
                    WHERE revision.user_id=%s AND revision.doc_id=%s
                    ORDER BY revision.created_at_ms DESC""",
                (user_id, doc_id),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _record_revision_event(
        connection: Any,
        user_id: str,
        doc_id: str,
        revision_id: str,
        event_type: str,
        actor_id: str,
        now_ms: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO knowledge_asset_events
                   (event_id,user_id,doc_id,event_type,actor_id,data,created_at_ms)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                f"kae_{uuid.uuid4().hex}",
                user_id,
                doc_id,
                event_type,
                actor_id,
                Jsonb({"revision_id": revision_id, **(extra or {})}),
                now_ms,
            ),
        )


__all__ = ["KNOWLEDGE_REVISION_DDL", "KnowledgeRevisionRepositoryMixin"]
