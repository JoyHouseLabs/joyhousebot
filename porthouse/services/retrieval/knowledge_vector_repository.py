"""Versioned Knowledge embeddings and hybrid retrieval operations."""

from __future__ import annotations

import math
import time
from typing import Any

import psycopg
from psycopg import sql

from porthouse.storage.json_codec import Jsonb

KNOWLEDGE_VECTOR_DDL = r"""
CREATE TABLE IF NOT EXISTS knowledge_revision_embeddings (
    revision_id TEXT NOT NULL REFERENCES knowledge_index_revisions(revision_id)
        ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    embedding_profile_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding DOUBLE PRECISION[] NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at_ms BIGINT NOT NULL,
    PRIMARY KEY(revision_id,chunk_index,embedding_profile_id),
    CHECK (dimensions > 0),
    CHECK (array_length(embedding,1)=dimensions)
);
DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid='knowledge_revision_embeddings'::regclass
           AND conname='knowledge_revision_embeddings_pkey'
           AND pg_get_constraintdef(oid) NOT LIKE '%embedding_profile_id%'
    ) THEN
        ALTER TABLE knowledge_revision_embeddings
            DROP CONSTRAINT knowledge_revision_embeddings_pkey;
        ALTER TABLE knowledge_revision_embeddings
            ADD PRIMARY KEY(revision_id,chunk_index,embedding_profile_id);
    END IF;
END
$migration$;
CREATE INDEX IF NOT EXISTS ix_knowledge_revision_embeddings_document
    ON knowledge_revision_embeddings(user_id,doc_id,revision_id,chunk_index);
CREATE INDEX IF NOT EXISTS ix_knowledge_revision_embeddings_profile
    ON knowledge_revision_embeddings(embedding_profile_id,dimensions,revision_id);
CREATE TABLE IF NOT EXISTS knowledge_embedding_operations (
    operation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    doc_id TEXT,
    revision_id TEXT,
    run_id TEXT,
    task_id TEXT,
    eval_run_id TEXT,
    eval_case_id TEXT,
    embedding_profile_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at_ms BIGINT NOT NULL,
    CHECK (status IN ('succeeded','failed')),
    CHECK (operation_type IN ('index','reembed','query','eval'))
);
CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_operations_profile
    ON knowledge_embedding_operations(embedding_profile_id,created_at_ms DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_operations_user
    ON knowledge_embedding_operations(user_id,created_at_ms DESC);
ALTER TABLE knowledge_embedding_operations ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE knowledge_embedding_operations ADD COLUMN IF NOT EXISTS task_id TEXT;
ALTER TABLE knowledge_embedding_operations ADD COLUMN IF NOT EXISTS eval_run_id TEXT;
ALTER TABLE knowledge_embedding_operations ADD COLUMN IF NOT EXISTS eval_case_id TEXT;
CREATE INDEX IF NOT EXISTS ix_knowledge_embedding_operations_eval
    ON knowledge_embedding_operations(eval_run_id,eval_case_id,created_at_ms DESC)
    WHERE eval_run_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS knowledge_vector_indexes (
    embedding_profile_id TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    status TEXT NOT NULL,
    index_name TEXT,
    row_count BIGINT NOT NULL DEFAULT 0,
    min_rows BIGINT NOT NULL,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    updated_at_ms BIGINT NOT NULL,
    CHECK (algorithm IN ('exact','hnsw')),
    CHECK (status IN ('not_required','queued','building','ready','failed'))
);
"""


class KnowledgeVectorRepositoryMixin:
    """Keep vector lifecycle separate from lexical revision persistence."""

    def stage_revision_embeddings(
        self,
        *,
        user_id: str,
        doc_id: str,
        revision_id: str,
        embedding_profile_id: str,
        embeddings: list[list[float]],
        actor_id: str,
        allow_draft_evaluation: bool = False,
    ) -> None:
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            revision = connection.execute(
                """SELECT embedding_profile_id FROM knowledge_index_revisions
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                      AND status='staging' FOR UPDATE""",
                (user_id, doc_id, revision_id),
            ).fetchone()
            if revision is None:
                raise ValueError("staging knowledge revision not found")
            if str(revision["embedding_profile_id"] or "") != embedding_profile_id:
                raise ValueError("embedding profile does not match the staged revision")
            profile = connection.execute(
                """SELECT configuration FROM embedding_profile_revisions
                    WHERE revision_id=%s AND (
                        status IN ('published','retired') OR
                        (%s AND status='draft')
                    )""",
                (embedding_profile_id, allow_draft_evaluation),
            ).fetchone()
            if profile is None:
                raise ValueError("published embedding profile revision not found")
            chunks = connection.execute(
                """SELECT chunk_index,content_sha256 FROM knowledge_revision_chunks
                    WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                    ORDER BY chunk_index""",
                (user_id, doc_id, revision_id),
            ).fetchall()
            if len(chunks) != len(embeddings):
                raise ValueError("embedding count does not match revision chunk count")
            dimensions = len(embeddings[0]) if embeddings else 0
            if not dimensions or any(len(item) != dimensions for item in embeddings):
                raise ValueError("embedding dimensions are empty or inconsistent")
            if any(not math.isfinite(float(value)) for item in embeddings for value in item):
                raise ValueError("embedding values must be finite numbers")
            if dimensions != int(profile["configuration"]["dimensions"]):
                raise ValueError("embedding dimensions do not match the profile revision")
            connection.execute(
                """DELETE FROM knowledge_revision_embeddings
                   WHERE revision_id=%s AND embedding_profile_id=%s""",
                (revision_id, embedding_profile_id),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO knowledge_revision_embeddings
                           (revision_id,doc_id,user_id,chunk_index,embedding_profile_id,
                            dimensions,embedding,content_sha256,created_at_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            revision_id,
                            doc_id,
                            user_id,
                            int(chunk["chunk_index"]),
                            embedding_profile_id,
                            dimensions,
                            list(vector),
                            str(chunk["content_sha256"]),
                            now_ms,
                        )
                        for chunk, vector in zip(chunks, embeddings, strict=True)
                    ],
                )
            self._record_revision_event(
                connection,
                user_id,
                doc_id,
                revision_id,
                "revision_embeddings_staged",
                actor_id,
                now_ms,
                {
                    "embedding_profile_id": embedding_profile_id,
                    "embedding_count": len(embeddings),
                    "dimensions": dimensions,
                },
            )

    def store_reembedded_revision(
        self,
        *,
        job_id: str,
        user_id: str,
        doc_id: str,
        revision_id: str,
        embedding_profile_id: str,
        embeddings: list[list[float]],
        actor_id: str,
        worker_id: str,
        lease_version: int,
    ) -> None:
        """Attach a new Profile projection without mutating the immutable chunks."""
        now_ms = int(time.time() * 1000)
        with self._connection() as connection:
            lease = connection.execute(
                """SELECT 1 FROM knowledge_reembedding_items
                   WHERE job_id=%s AND doc_id=%s AND revision_id=%s
                     AND status='running' AND lease_owner=%s AND lease_version=%s
                     AND lease_expires_at>clock_timestamp() FOR UPDATE""",
                (job_id, doc_id, revision_id, worker_id, lease_version),
            ).fetchone()
            if lease is None:
                raise RuntimeError("Knowledge re-embedding write was fenced")
            revision = connection.execute(
                """SELECT 1 FROM knowledge_index_revisions
                   WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                     AND status IN ('active','superseded') FOR UPDATE""",
                (user_id, doc_id, revision_id),
            ).fetchone()
            if revision is None:
                raise ValueError("active or superseded Knowledge revision not found")
            profile = connection.execute(
                """SELECT configuration FROM embedding_profile_revisions
                   WHERE revision_id=%s AND status IN ('published','retired')""",
                (embedding_profile_id,),
            ).fetchone()
            if profile is None:
                raise ValueError("published embedding profile revision not found")
            chunks = connection.execute(
                """SELECT chunk_index,content_sha256 FROM knowledge_revision_chunks
                   WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                   ORDER BY chunk_index""",
                (user_id, doc_id, revision_id),
            ).fetchall()
            dimensions = len(embeddings[0]) if embeddings else 0
            if len(chunks) != len(embeddings):
                raise ValueError("embedding count does not match revision chunk count")
            if not dimensions or any(len(item) != dimensions for item in embeddings):
                raise ValueError("embedding dimensions are empty or inconsistent")
            if dimensions != int(profile["configuration"]["dimensions"]):
                raise ValueError("embedding dimensions do not match the profile revision")
            if any(not math.isfinite(float(value)) for item in embeddings for value in item):
                raise ValueError("embedding values must be finite numbers")
            connection.execute(
                """DELETE FROM knowledge_revision_embeddings
                   WHERE revision_id=%s AND embedding_profile_id=%s""",
                (revision_id, embedding_profile_id),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO knowledge_revision_embeddings
                           (revision_id,doc_id,user_id,chunk_index,embedding_profile_id,
                            dimensions,embedding,content_sha256,created_at_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            revision_id,
                            doc_id,
                            user_id,
                            int(chunk["chunk_index"]),
                            embedding_profile_id,
                            dimensions,
                            list(vector),
                            str(chunk["content_sha256"]),
                            now_ms,
                        )
                        for chunk, vector in zip(chunks, embeddings, strict=True)
                    ],
                )
            self._record_revision_event(
                connection,
                user_id,
                doc_id,
                revision_id,
                "revision_reembedded",
                actor_id,
                now_ms,
                {
                    "embedding_profile_id": embedding_profile_id,
                    "embedding_count": len(embeddings),
                    "dimensions": dimensions,
                },
            )

    def record_embedding_usage(
        self,
        *,
        operation_id: str,
        user_id: str,
        doc_id: str | None,
        revision_id: str | None,
        run_id: str | None,
        task_id: str | None,
        eval_run_id: str | None,
        eval_case_id: str | None,
        embedding_profile_id: str,
        operation_type: str,
        status: str,
        request_count: int,
        input_tokens: int,
        cost_usd: float,
        error_code: str | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO knowledge_embedding_operations
                       (operation_id,user_id,doc_id,revision_id,run_id,task_id,
                        eval_run_id,eval_case_id,embedding_profile_id,
                        operation_type,status,request_count,input_tokens,cost_usd,
                        error_code,created_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(operation_id) DO NOTHING""",
                (
                    operation_id,
                    user_id,
                    doc_id,
                    revision_id,
                    run_id,
                    task_id,
                    eval_run_id,
                    eval_case_id,
                    embedding_profile_id,
                    operation_type,
                    status,
                    max(0, int(request_count)),
                    max(0, int(input_tokens)),
                    max(0.0, float(cost_usd)),
                    error_code,
                    int(time.time() * 1000),
                ),
            )

    def get_vector_index_state(self, embedding_profile_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM knowledge_vector_indexes
                   WHERE embedding_profile_id=%s""",
                (embedding_profile_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "embedding_profile_id": str(row["embedding_profile_id"]),
            "dimensions": int(row["dimensions"]),
            "algorithm": str(row["algorithm"]),
            "status": str(row["status"]),
            "index_name": str(row["index_name"]) if row["index_name"] else None,
            "row_count": int(row["row_count"]),
            "min_rows": int(row["min_rows"]),
            "configuration": dict(row["configuration"] or {}),
            "error": dict(row["error"] or {}) if row["error"] else None,
            "updated_at_ms": int(row["updated_at_ms"]),
        }

    def embedding_eval_cost(self, *, eval_run_id: str, eval_case_id: str) -> float:
        """Sum governed embedding spend attributed to one validated Eval case."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT COALESCE(sum(cost_usd),0) AS cost
                   FROM knowledge_embedding_operations
                   WHERE eval_run_id=%s AND eval_case_id=%s AND status='succeeded'""",
                (eval_run_id, eval_case_id),
            ).fetchone()
        return float(row["cost"] or 0)

    def reconcile_vector_indexes(self, *, limit: int = 1) -> int:
        """Select exact search for small profiles and materialize HNSW when warranted."""
        with self._connection() as connection:
            profiles = connection.execute(
                """SELECT revision_id,configuration
                   FROM embedding_profile_revisions
                   WHERE status IN ('published','retired') ORDER BY published_at DESC NULLS LAST
                   LIMIT %s""",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        processed = 0
        for profile in profiles:
            configuration = dict(profile["configuration"])
            profile_id = str(profile["revision_id"])
            dimensions = int(configuration["dimensions"])
            min_rows = int(configuration.get("ann_min_rows") or 10_000)
            with self._connection() as connection:
                row_count = int(
                    connection.execute(
                        """SELECT count(*) AS count FROM knowledge_revision_embeddings
                           WHERE embedding_profile_id=%s AND dimensions=%s""",
                        (profile_id, dimensions),
                    ).fetchone()["count"]
                )
                now_ms = int(time.time() * 1000)
                if row_count < min_rows:
                    connection.execute(
                        """INSERT INTO knowledge_vector_indexes
                               (embedding_profile_id,dimensions,algorithm,status,index_name,
                                row_count,min_rows,configuration,updated_at_ms)
                           VALUES (%s,%s,'exact','not_required',NULL,%s,%s,%s,%s)
                           ON CONFLICT(embedding_profile_id) DO UPDATE SET
                               dimensions=excluded.dimensions,algorithm='exact',
                               status='not_required',row_count=excluded.row_count,
                               min_rows=excluded.min_rows,configuration=excluded.configuration,
                               error=NULL,updated_at_ms=excluded.updated_at_ms""",
                        (
                            profile_id,
                            dimensions,
                            row_count,
                            min_rows,
                            Jsonb(configuration),
                            now_ms,
                        ),
                    )
                    processed += 1
                    continue
                index_name = "ix_kemb_hnsw_" + __import__("hashlib").sha256(
                    profile_id.encode()
                ).hexdigest()[:20]
                connection.execute(
                    """INSERT INTO knowledge_vector_indexes
                           (embedding_profile_id,dimensions,algorithm,status,index_name,
                            row_count,min_rows,configuration,updated_at_ms)
                       VALUES (%s,%s,'hnsw','building',%s,%s,%s,%s,%s)
                       ON CONFLICT(embedding_profile_id) DO UPDATE SET
                           dimensions=excluded.dimensions,algorithm='hnsw',status='building',
                           index_name=excluded.index_name,row_count=excluded.row_count,
                           min_rows=excluded.min_rows,configuration=excluded.configuration,
                           error=NULL,updated_at_ms=excluded.updated_at_ms""",
                    (
                        profile_id,
                        dimensions,
                        index_name,
                        row_count,
                        min_rows,
                        Jsonb(configuration),
                        now_ms,
                    ),
                )
            statement = sql.SQL(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS {index} "
                "ON knowledge_revision_embeddings "
                "USING hnsw (((embedding::real[])::vector({dimensions})) vector_cosine_ops) "
                "WITH (m={m},ef_construction={ef}) WHERE embedding_profile_id={profile}"
            ).format(
                index=sql.Identifier(index_name),
                dimensions=sql.Literal(dimensions),
                m=sql.Literal(int(configuration.get("hnsw_m") or 16)),
                ef=sql.Literal(int(configuration.get("hnsw_ef_construction") or 64)),
                profile=sql.Literal(profile_id),
            )
            try:
                # Concurrent index DDL cannot run inside a transaction. A session
                # advisory lock elects one builder across all Worker replicas;
                # an interrupted invalid index is discarded before retry.
                with psycopg.connect(self.store.database_url, autocommit=True) as connection:
                    acquired = bool(
                        connection.execute(
                            "SELECT pg_try_advisory_lock(hashtext(%s),%s)",
                            (profile_id, 711_903),
                        ).fetchone()[0]
                    )
                    if not acquired:
                        continue
                    try:
                        valid = connection.execute(
                            """SELECT index.indisvalid FROM pg_index index
                               JOIN pg_class relation ON relation.oid=index.indexrelid
                               WHERE relation.relname=%s""",
                            (index_name,),
                        ).fetchone()
                        if valid is not None and not bool(valid[0]):
                            connection.execute(
                                sql.SQL("DROP INDEX CONCURRENTLY {}").format(
                                    sql.Identifier(index_name)
                                )
                            )
                        connection.execute(statement)
                        valid = connection.execute(
                            """SELECT index.indisvalid FROM pg_index index
                               JOIN pg_class relation ON relation.oid=index.indexrelid
                               WHERE relation.relname=%s""",
                            (index_name,),
                        ).fetchone()
                        if valid is None or not bool(valid[0]):
                            raise RuntimeError("HNSW index build did not become valid")
                    finally:
                        connection.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s),%s)",
                            (profile_id, 711_903),
                        )
                with self._connection() as connection:
                    connection.execute(
                        """UPDATE knowledge_vector_indexes SET status='ready',error=NULL,
                           updated_at_ms=%s WHERE embedding_profile_id=%s""",
                        (int(time.time() * 1000), profile_id),
                    )
            except Exception as exc:
                with self._connection() as connection:
                    connection.execute(
                        """UPDATE knowledge_vector_indexes SET status='failed',error=%s,
                           updated_at_ms=%s WHERE embedding_profile_id=%s""",
                        (
                            Jsonb({"type": type(exc).__name__, "message": str(exc)[:1000]}),
                            int(time.time() * 1000),
                            profile_id,
                        ),
                    )
                raise
            processed += 1
        return processed

    def search_hybrid(
        self,
        *,
        user_id: str,
        query: str,
        query_embedding: list[float],
        embedding_profile_id: str,
        top_k: int,
        source_type: str | None = None,
        doc_id: str | None = None,
        knowledge_base_id: str | None = None,
        collection_ref: str | None = None,
        app_installation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fuse lexical and vector ranks while preserving active evidence identity."""
        lexical = self.search(
            user_id=user_id,
            query=query,
            top_k=max(top_k * 4, 20),
            source_type=source_type,
            doc_id=doc_id,
            knowledge_base_id=knowledge_base_id,
            collection_ref=collection_ref,
            app_installation_id=app_installation_id,
        )
        clauses: list[Any] = [
            "c.user_id=%s",
            "d.source_status<>'archived'",
            sql.SQL("embedding.embedding_profile_id={}").format(
                sql.Literal(embedding_profile_id)
            ),
            "embedding.dimensions=%s",
        ]
        vector_literal = "[" + ",".join(str(float(value)) for value in query_embedding) + "]"
        params: list[Any] = [vector_literal, user_id, len(query_embedding)]
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
        params.extend((vector_literal, max(top_k * 4, 20)))
        typed_vector = sql.SQL("(embedding.embedding::real[])::vector({})").format(
            sql.Literal(len(query_embedding))
        )
        where_clause = sql.SQL(" AND ").join(
            item if isinstance(item, sql.Composable) else sql.SQL(item) for item in clauses
        )
        statement = sql.SQL("""SELECT c.doc_id,c.chunk_index,c.revision_id,c.page,c.section_path,
                          c.block_type,c.char_start,c.char_end,c.content,c.content_sha256,
                          d.source_type,d.source_url,d.title,d.source_system,d.source_id,
                          d.source_version,d.source_generation,d.active_revision_id,
                          1-({typed_vector} <=> %s::vector) AS rank
                     FROM knowledge_chunks c
                     JOIN knowledge_documents d USING(doc_id)
                     JOIN knowledge_revision_embeddings embedding
                       ON embedding.user_id=c.user_id AND embedding.doc_id=c.doc_id
                      AND embedding.revision_id=c.revision_id
                      AND embedding.chunk_index=c.chunk_index
                    WHERE {where_clause}
                    ORDER BY {typed_vector} <=> %s::vector
                    LIMIT %s""").format(
            typed_vector=typed_vector,
            where_clause=where_clause,
        )
        index_state = self.get_vector_index_state(embedding_profile_id)
        with self._connection() as connection:
            if index_state and index_state["status"] == "ready":
                ef_search = int(index_state["configuration"].get("hnsw_ef_search") or 40)
                connection.execute(
                    sql.SQL("SET LOCAL hnsw.ef_search={}").format(sql.Literal(ef_search))
                )
            rows = connection.execute(statement, tuple(params)).fetchall()
        vector = [self._search_row(row) for row in rows]
        fused: dict[tuple[str, int], dict[str, Any]] = {}
        for source, mode in ((lexical, "lexical"), (vector, "vector")):
            for rank, item in enumerate(source, start=1):
                key = (item["doc_id"], item["chunk_index"])
                current = fused.setdefault(
                    key, {**item, "rank": 0.0, "retrieval_modes": []}
                )
                current["rank"] += 1.0 / (60 + rank)
                current["retrieval_modes"].append(mode)
        values = sorted(
            fused.values(),
            key=lambda item: (-float(item["rank"]), item["doc_id"], item["chunk_index"]),
        )[:top_k]
        for item in values:
            item["trace"]["retrieval_modes"] = item.pop("retrieval_modes")
            item["trace"]["embedding_profile_id"] = embedding_profile_id
            item["trace"]["vector_strategy"] = (
                "hnsw"
                if index_state and index_state["status"] == "ready"
                else "exact"
            )
        return values

    @staticmethod
    def _search_row(row: Any) -> dict[str, Any]:
        return {
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
