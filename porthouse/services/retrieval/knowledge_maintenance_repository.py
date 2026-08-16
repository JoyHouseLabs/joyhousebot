"""Durable leased Knowledge maintenance jobs."""

from __future__ import annotations

import uuid
from typing import Any

from porthouse.storage.json_codec import Jsonb

KNOWLEDGE_MAINTENANCE_DDL = r"""
CREATE TABLE IF NOT EXISTS knowledge_reembedding_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    embedding_profile_id TEXT NOT NULL,
    knowledge_base_id TEXT,
    doc_id TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    requested_by TEXT NOT NULL,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (status IN ('queued','running','completed','failed','cancelled')),
    UNIQUE(user_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_reembedding_jobs_user
    ON knowledge_reembedding_jobs(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS knowledge_reembedding_items (
    job_id TEXT NOT NULL REFERENCES knowledge_reembedding_jobs(job_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_owner TEXT,
    lease_version BIGINT NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(job_id,doc_id,revision_id),
    CHECK (status IN ('queued','running','completed','failed','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_knowledge_reembedding_items_claim
    ON knowledge_reembedding_items(status,available_at,created_at)
    WHERE status IN ('queued','running');
"""


class KnowledgeMaintenanceRepositoryMixin:
    """Queue and fence long-running Knowledge projection maintenance."""

    def enqueue_reembedding_job(
        self,
        *,
        user_id: str,
        embedding_profile_id: str,
        requested_by: str,
        idempotency_key: str,
        knowledge_base_id: str | None = None,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            profile = connection.execute(
                """SELECT 1 FROM embedding_profile_revisions
                   WHERE revision_id=%s AND status='published'""",
                (embedding_profile_id,),
            ).fetchone()
            if profile is None:
                raise ValueError("published embedding profile revision not found")
            if knowledge_base_id:
                base = connection.execute(
                    """SELECT 1 FROM knowledge_bases
                       WHERE user_id=%s AND knowledge_base_id=%s""",
                    (user_id, knowledge_base_id),
                ).fetchone()
                if base is None:
                    raise ValueError("Knowledge base not found")
            existing = connection.execute(
                """SELECT job_id FROM knowledge_reembedding_jobs
                   WHERE user_id=%s AND idempotency_key=%s""",
                (user_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                result = self._reembedding_job(connection, str(existing["job_id"]))
                assert result is not None
                return result
            clauses = [
                "document.user_id=%s",
                "document.active_revision_id IS NOT NULL",
                "document.source_status<>'archived'",
            ]
            params: list[Any] = [user_id]
            if doc_id:
                clauses.append("document.doc_id=%s")
                params.append(doc_id)
            if knowledge_base_id:
                clauses.append(
                    """EXISTS (SELECT 1 FROM knowledge_base_documents membership
                                WHERE membership.user_id=document.user_id
                                  AND membership.doc_id=document.doc_id
                                  AND membership.knowledge_base_id=%s)"""
                )
                params.append(knowledge_base_id)
            clauses.append(
                """NOT EXISTS (SELECT 1 FROM knowledge_revision_embeddings embedding
                                WHERE embedding.revision_id=document.active_revision_id
                                  AND embedding.embedding_profile_id=%s)"""
            )
            params.append(embedding_profile_id)
            total_items = int(
                connection.execute(
                    f"""SELECT count(*) AS count FROM knowledge_documents document
                       WHERE {' AND '.join(clauses)}""",
                    tuple(params),
                ).fetchone()["count"]
            )
            if doc_id and total_items == 0:
                document = connection.execute(
                    "SELECT 1 FROM knowledge_documents WHERE user_id=%s AND doc_id=%s",
                    (user_id, doc_id),
                ).fetchone()
                if document is None:
                    raise ValueError("Knowledge document not found")
            job_id = f"krejob_{uuid.uuid4().hex}"
            status = "queued" if total_items else "completed"
            connection.execute(
                """INSERT INTO knowledge_reembedding_jobs
                       (job_id,user_id,embedding_profile_id,knowledge_base_id,doc_id,
                        idempotency_key,status,total_items,completed_items,requested_by,
                        finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s='completed' THEN clock_timestamp() END)""",
                (
                    job_id,
                    user_id,
                    embedding_profile_id,
                    knowledge_base_id,
                    doc_id,
                    idempotency_key,
                    status,
                    total_items,
                    0,
                    requested_by,
                    status,
                ),
            )
            if total_items:
                connection.execute(
                    f"""INSERT INTO knowledge_reembedding_items
                            (job_id,doc_id,revision_id)
                        SELECT %s,document.doc_id,document.active_revision_id
                          FROM knowledge_documents document
                         WHERE {' AND '.join(clauses)}""",
                    (job_id, *params),
                )
                self.store._notify(connection, "knowledge:reembed")
            result = self._reembedding_job(connection, job_id)
            assert result is not None
            return result

    def list_reembedding_jobs(
        self, *, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT job_id FROM knowledge_reembedding_jobs
                   WHERE user_id=%s ORDER BY created_at DESC LIMIT %s""",
                (user_id, max(1, min(int(limit), 500))),
            ).fetchall()
            return [
                item
                for row in rows
                if (item := self._reembedding_job(connection, str(row["job_id"])))
                is not None
            ]

    def get_reembedding_job(
        self, *, user_id: str, job_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            owner = connection.execute(
                """SELECT 1 FROM knowledge_reembedding_jobs
                   WHERE user_id=%s AND job_id=%s""",
                (user_id, job_id),
            ).fetchone()
            if owner is None:
                return None
            return self._reembedding_job(connection, job_id)

    def cancel_reembedding_job(
        self, *, user_id: str, job_id: str, actor_id: str
    ) -> bool:
        del actor_id  # the authenticated actor is retained in API request audit logs
        with self._connection() as connection:
            changed = connection.execute(
                """UPDATE knowledge_reembedding_jobs
                   SET status='cancelled',finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND job_id=%s AND status IN ('queued','running')""",
                (user_id, job_id),
            ).rowcount
            if changed:
                connection.execute(
                    """UPDATE knowledge_reembedding_items
                       SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,
                           finished_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE job_id=%s AND status IN ('queued','running')""",
                    (job_id,),
                )
        return changed == 1

    def claim_reembedding_item(
        self, *, worker_id: str, lease_seconds: int = 120
    ) -> dict[str, Any] | None:
        lease = max(30, min(int(lease_seconds), 3600))
        with self._connection() as connection:
            row = connection.execute(
                """WITH candidate AS (
                       SELECT item.job_id,item.doc_id,item.revision_id
                       FROM knowledge_reembedding_items item
                       JOIN knowledge_reembedding_jobs job USING(job_id)
                       WHERE job.status IN ('queued','running')
                         AND item.attempt<item.max_attempts AND (
                           (item.status='queued' AND item.available_at<=clock_timestamp()) OR
                           (item.status='running' AND
                            item.lease_expires_at<=clock_timestamp())
                         )
                       ORDER BY item.available_at,item.created_at
                       FOR UPDATE OF item SKIP LOCKED LIMIT 1
                   )
                   UPDATE knowledge_reembedding_items item SET status='running',
                       lease_owner=%s,lease_version=item.lease_version+1,
                       lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       attempt=item.attempt+1,
                       started_at=COALESCE(item.started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   FROM candidate WHERE item.job_id=candidate.job_id
                     AND item.doc_id=candidate.doc_id
                     AND item.revision_id=candidate.revision_id
                   RETURNING item.*""",
                (worker_id, lease),
            ).fetchone()
            if row is None:
                return None
            job = connection.execute(
                """UPDATE knowledge_reembedding_jobs
                   SET status='running',started_at=COALESCE(started_at,clock_timestamp()),
                       updated_at=clock_timestamp() WHERE job_id=%s
                   RETURNING user_id,embedding_profile_id,requested_by""",
                (row["job_id"],),
            ).fetchone()
            chunks = connection.execute(
                """SELECT chunk_index,content FROM knowledge_revision_chunks
                   WHERE user_id=%s AND doc_id=%s AND revision_id=%s
                   ORDER BY chunk_index""",
                (job["user_id"], row["doc_id"], row["revision_id"]),
            ).fetchall()
        return {
            "job_id": str(row["job_id"]),
            "doc_id": str(row["doc_id"]),
            "revision_id": str(row["revision_id"]),
            "user_id": str(job["user_id"]),
            "embedding_profile_id": str(job["embedding_profile_id"]),
            "requested_by": str(job["requested_by"]),
            "attempt": int(row["attempt"]),
            "max_attempts": int(row["max_attempts"]),
            "lease_version": int(row["lease_version"]),
            "chunks": [str(chunk["content"]) for chunk in chunks],
        }

    def heartbeat_reembedding_item(
        self,
        job_id: str,
        doc_id: str,
        revision_id: str,
        *,
        worker_id: str,
        lease_version: int,
        lease_seconds: int = 120,
    ) -> bool:
        lease = max(30, min(int(lease_seconds), 3600))
        with self._connection() as connection:
            changed = connection.execute(
                """UPDATE knowledge_reembedding_items
                   SET lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE job_id=%s AND doc_id=%s AND revision_id=%s
                     AND status='running' AND lease_owner=%s AND lease_version=%s""",
                (lease, job_id, doc_id, revision_id, worker_id, lease_version),
            ).rowcount
        return changed == 1

    def complete_reembedding_item(
        self,
        job_id: str,
        doc_id: str,
        revision_id: str,
        *,
        worker_id: str,
        lease_version: int,
    ) -> bool:
        with self._connection() as connection:
            changed = connection.execute(
                """UPDATE knowledge_reembedding_items SET status='completed',
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE job_id=%s AND doc_id=%s AND revision_id=%s
                     AND status='running' AND lease_owner=%s AND lease_version=%s""",
                (job_id, doc_id, revision_id, worker_id, lease_version),
            ).rowcount
            if changed:
                self._refresh_reembedding_job(connection, job_id)
        return changed == 1

    def fail_reembedding_item(
        self,
        job_id: str,
        doc_id: str,
        revision_id: str,
        *,
        worker_id: str,
        lease_version: int,
        error: dict[str, Any],
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM knowledge_reembedding_items
                   WHERE job_id=%s AND doc_id=%s AND revision_id=%s FOR UPDATE""",
                (job_id, doc_id, revision_id),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != "running"
                or str(row["lease_owner"]) != worker_id
                or int(row["lease_version"]) != int(lease_version)
            ):
                return False
            retry = int(row["attempt"]) < int(row["max_attempts"])
            delay = min(900, 2 ** max(0, int(row["attempt"]) - 1))
            connection.execute(
                """UPDATE knowledge_reembedding_items SET status=%s,error=%s,
                       available_at=CASE WHEN %s THEN
                           clock_timestamp()+(%s*interval '1 second') ELSE available_at END,
                       lease_owner=NULL,lease_expires_at=NULL,
                       finished_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
                       updated_at=clock_timestamp()
                   WHERE job_id=%s AND doc_id=%s AND revision_id=%s""",
                (
                    "queued" if retry else "failed",
                    Jsonb(error),
                    retry,
                    delay,
                    retry,
                    job_id,
                    doc_id,
                    revision_id,
                ),
            )
            self._refresh_reembedding_job(connection, job_id)
            if retry:
                self.store._notify(connection, "knowledge:reembed:retry")
        return True

    @staticmethod
    def _refresh_reembedding_job(connection: Any, job_id: str) -> None:
        totals = connection.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE status='completed') AS completed,
                      count(*) FILTER (WHERE status='failed') AS failed,
                      count(*) FILTER (WHERE status IN ('queued','running')) AS pending
               FROM knowledge_reembedding_items WHERE job_id=%s""",
            (job_id,),
        ).fetchone()
        pending = int(totals["pending"] or 0)
        failed = int(totals["failed"] or 0)
        connection.execute(
            """UPDATE knowledge_reembedding_jobs
               SET total_items=%s,completed_items=%s,failed_items=%s,
                   status=CASE WHEN status='cancelled' THEN status
                               WHEN %s>0 THEN 'running'
                               WHEN %s>0 THEN 'failed' ELSE 'completed' END,
                   finished_at=CASE WHEN status='cancelled' OR %s>0 THEN finished_at
                                    ELSE clock_timestamp() END,
                   updated_at=clock_timestamp() WHERE job_id=%s""",
            (
                int(totals["total"] or 0),
                int(totals["completed"] or 0),
                failed,
                pending,
                failed,
                pending,
                job_id,
            ),
        )

    @staticmethod
    def _reembedding_job(connection: Any, job_id: str) -> dict[str, Any] | None:
        from porthouse.storage.postgres_store import _iso

        row = connection.execute(
            "SELECT * FROM knowledge_reembedding_jobs WHERE job_id=%s", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "job_id": str(row["job_id"]),
            "user_id": str(row["user_id"]),
            "embedding_profile_id": str(row["embedding_profile_id"]),
            "knowledge_base_id": (
                str(row["knowledge_base_id"]) if row["knowledge_base_id"] else None
            ),
            "doc_id": str(row["doc_id"]) if row["doc_id"] else None,
            "status": str(row["status"]),
            "total_items": int(row["total_items"]),
            "completed_items": int(row["completed_items"]),
            "failed_items": int(row["failed_items"]),
            "requested_by": str(row["requested_by"]),
            "created_at": _iso(row["created_at"]),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
            "updated_at": _iso(row["updated_at"]),
        }


__all__ = ["KNOWLEDGE_MAINTENANCE_DDL", "KnowledgeMaintenanceRepositoryMixin"]
