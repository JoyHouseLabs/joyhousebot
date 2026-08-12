"""Versioned Knowledge embeddings and hybrid retrieval operations."""

from __future__ import annotations

import math
import time
from typing import Any

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
    PRIMARY KEY(revision_id,chunk_index),
    CHECK (dimensions > 0),
    CHECK (array_length(embedding,1)=dimensions)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_revision_embeddings_document
    ON knowledge_revision_embeddings(user_id,doc_id,revision_id,chunk_index);
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
                "DELETE FROM knowledge_revision_embeddings WHERE revision_id=%s",
                (revision_id,),
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
        )
        clauses = [
            "c.user_id=%s",
            "d.source_status<>'archived'",
            "embedding.embedding_profile_id=%s",
            "embedding.dimensions=%s",
        ]
        vector_literal = "[" + ",".join(str(float(value)) for value in query_embedding) + "]"
        params: list[Any] = [vector_literal, user_id, embedding_profile_id, len(query_embedding)]
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
        sql = f"""SELECT c.doc_id,c.chunk_index,c.revision_id,c.page,c.section_path,
                          c.block_type,c.char_start,c.char_end,c.content,c.content_sha256,
                          d.source_type,d.source_url,d.title,d.source_system,d.source_id,
                          d.source_version,d.source_generation,d.active_revision_id,
                          1-(embedding.embedding::real[]::vector <=> %s::vector) AS rank
                     FROM knowledge_chunks c
                     JOIN knowledge_documents d USING(doc_id)
                     JOIN knowledge_revision_embeddings embedding
                       ON embedding.user_id=c.user_id AND embedding.doc_id=c.doc_id
                      AND embedding.revision_id=c.revision_id
                      AND embedding.chunk_index=c.chunk_index
                    WHERE {" AND ".join(clauses)}
                    ORDER BY embedding.embedding::real[]::vector <=> %s::vector
                    LIMIT %s"""
        with self._connection() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
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
