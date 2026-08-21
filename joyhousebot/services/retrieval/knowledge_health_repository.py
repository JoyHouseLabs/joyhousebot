"""Owner-scoped health projections for Knowledge indexing."""

from __future__ import annotations

from typing import Any


class KnowledgeHealthRepositoryMixin:
    """Aggregate index readiness without exposing private document content."""

    def index_health(self, *, user_id: str, since_ms: int) -> dict[str, Any]:
        with self._connection() as connection:
            document = connection.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (WHERE index_status='ready') AS ready,
                          COUNT(*) FILTER (WHERE index_status='indexing') AS indexing,
                          COUNT(*) FILTER (WHERE index_status='failed') AS failed,
                          COUNT(*) FILTER (WHERE source_status='archived') AS archived,
                          MAX(updated_at_ms) FILTER (WHERE index_status='ready') AS last_ready_at_ms
                     FROM knowledge_documents
                    WHERE user_id=%s""",
                (user_id,),
            ).fetchone()
            revision = connection.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (WHERE activated_at_ms IS NOT NULL) AS succeeded,
                          COUNT(*) FILTER (WHERE failed_at_ms IS NOT NULL) AS failed,
                          COUNT(*) FILTER (WHERE status IN ('staging','ready')) AS queue_depth
                     FROM knowledge_index_revisions
                    WHERE user_id=%s""",
                (user_id,),
            ).fetchone()
            window = connection.execute(
                """SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (WHERE activated_at_ms IS NOT NULL) AS succeeded,
                          COUNT(*) FILTER (WHERE failed_at_ms IS NOT NULL) AS failed,
                          AVG(COALESCE(activated_at_ms,failed_at_ms)-created_at_ms)
                              FILTER (WHERE activated_at_ms IS NOT NULL
                                           OR failed_at_ms IS NOT NULL) AS avg_duration_ms,
                          percentile_cont(0.95) WITHIN GROUP (
                              ORDER BY COALESCE(activated_at_ms,failed_at_ms)-created_at_ms
                          ) FILTER (WHERE activated_at_ms IS NOT NULL
                                         OR failed_at_ms IS NOT NULL) AS p95_duration_ms
                     FROM knowledge_index_revisions
                    WHERE user_id=%s AND created_at_ms>=%s""",
                (user_id, since_ms),
            ).fetchone()
            failure_rows = connection.execute(
                """SELECT COALESCE(NULLIF(error_code,''),'UNKNOWN') AS error_code,
                          COUNT(*) AS count,
                          MAX(failed_at_ms) AS last_failed_at_ms
                     FROM knowledge_index_revisions
                    WHERE user_id=%s AND failed_at_ms IS NOT NULL
                      AND created_at_ms>=%s
                 GROUP BY COALESCE(NULLIF(error_code,''),'UNKNOWN')
                 ORDER BY count DESC,error_code
                    LIMIT 20""",
                (user_id, since_ms),
            ).fetchall()
        completed = int(window["succeeded"] or 0) + int(window["failed"] or 0)
        return {
            "since_ms": int(since_ms),
            "documents": {
                "total": int(document["total"] or 0),
                "ready": int(document["ready"] or 0),
                "indexing": int(document["indexing"] or 0),
                "failed": int(document["failed"] or 0),
                "archived": int(document["archived"] or 0),
                "last_ready_at_ms": (
                    int(document["last_ready_at_ms"])
                    if document["last_ready_at_ms"] is not None
                    else None
                ),
            },
            "revisions": {
                "total": int(revision["total"] or 0),
                "succeeded": int(revision["succeeded"] or 0),
                "failed": int(revision["failed"] or 0),
                "queue_depth": int(revision["queue_depth"] or 0),
            },
            "window": {
                "total": int(window["total"] or 0),
                "succeeded": int(window["succeeded"] or 0),
                "failed": int(window["failed"] or 0),
                "success_rate": (
                    round(int(window["succeeded"] or 0) / completed, 4)
                    if completed
                    else None
                ),
                "avg_duration_ms": (
                    round(float(window["avg_duration_ms"]))
                    if window["avg_duration_ms"] is not None
                    else None
                ),
                "p95_duration_ms": (
                    round(float(window["p95_duration_ms"]))
                    if window["p95_duration_ms"] is not None
                    else None
                ),
            },
            "failure_codes": [
                {
                    "error_code": str(row["error_code"]),
                    "count": int(row["count"]),
                    "last_failed_at_ms": int(row["last_failed_at_ms"]),
                }
                for row in failure_rows
            ],
        }
