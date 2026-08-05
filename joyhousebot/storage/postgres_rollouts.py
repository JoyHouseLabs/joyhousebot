"""PostgreSQL configuration audit and per-worker rollout acknowledgements."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.platform_records import (
    ConfigurationEventRecord,
    ConfigurationRolloutRecord,
)


class PostgresRolloutStoreMixin:
    def list_configuration_events(self, *, limit: int = 200) -> list[ConfigurationEventRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM configuration_events ORDER BY sequence DESC LIMIT %s",
                (max(1, min(2000, limit)),),
            ).fetchall()
        from joyhousebot.storage.postgres_store import _iso

        return [
            ConfigurationEventRecord(
                sequence=int(row["sequence"]),
                aggregate_type=str(row["aggregate_type"]),
                aggregate_id=str(row["aggregate_id"]),
                revision_id=str(row["revision_id"]),
                event_type=str(row["event_type"]),
                actor_id=str(row["actor_id"]),
                created_at=_iso(row["created_at"]) or "",
            )
            for row in rows
        ]

    def list_configuration_rollouts(
        self, *, limit: int = 100
    ) -> list[ConfigurationRolloutRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM configuration_rollouts
                   ORDER BY created_at DESC LIMIT %s""",
                (max(1, min(1000, limit)),),
            ).fetchall()
        return [self._configuration_rollout(row) for row in rows]

    def list_configuration_rollout_targets(self, rollout_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT worker_id,status,error,acknowledged_at
                   FROM configuration_rollout_targets WHERE rollout_id=%s
                   ORDER BY worker_id""",
                (rollout_id,),
            ).fetchall()
        from joyhousebot.storage.postgres_store import _iso, _json

        return [
            {
                "worker_id": str(row["worker_id"]),
                "status": str(row["status"]),
                "error": _json(row["error"]),
                "acknowledged_at": _iso(row["acknowledged_at"]),
            }
            for row in rows
        ]

    def list_pending_agent_revisions(self, worker_id: str) -> list[dict[str, str]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT r.rollout_id,r.aggregate_id AS agent_id,r.revision_id
                   FROM configuration_rollout_targets t
                   JOIN configuration_rollouts r ON r.rollout_id=t.rollout_id
                   WHERE t.worker_id=%s AND t.status='pending'
                     AND r.aggregate_type='agent' AND r.status='rolling_out'
                   ORDER BY r.created_at""",
                (worker_id,),
            ).fetchall()
        return [
            {key: str(row[key]) for key in ("rollout_id", "agent_id", "revision_id")}
            for row in rows
        ]

    def acknowledge_agent_revision(
        self,
        *,
        worker_id: str,
        agent_id: str,
        revision_id: str,
        status: str = "loaded",
        error: dict[str, Any] | None = None,
    ) -> bool:
        if status not in {"loaded", "failed"}:
            raise ValueError("invalid Agent rollout acknowledgement status")
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """UPDATE configuration_rollout_targets t SET status=%s,error=%s,
                       acknowledged_at=clock_timestamp()
                   FROM configuration_rollouts r
                   WHERE r.rollout_id=t.rollout_id AND t.worker_id=%s
                     AND t.status='pending' AND r.aggregate_type='agent'
                     AND r.aggregate_id=%s AND r.revision_id=%s
                   RETURNING t.rollout_id""",
                (
                    status,
                    Jsonb(error) if error is not None else None,
                    worker_id,
                    agent_id,
                    revision_id,
                ),
            ).fetchall()
            for row in rows:
                self._refresh_rollout_state(conn, str(row["rollout_id"]))
        return bool(rows)

    def _refresh_rollout_state(self, conn: Any, rollout_id: str) -> None:
        counts = conn.execute(
            """SELECT count(*) FILTER (WHERE status<>'pending') AS acknowledged,
                      count(*) FILTER (WHERE status='failed') AS failed,count(*) AS total
               FROM configuration_rollout_targets WHERE rollout_id=%s""",
            (rollout_id,),
        ).fetchone()
        acknowledged = int(counts["acknowledged"])
        failed = int(counts["failed"])
        complete = acknowledged >= int(counts["total"])
        status = "failed" if complete and failed else "completed" if complete else "rolling_out"
        conn.execute(
            """UPDATE configuration_rollouts SET status=%s,
                   acknowledged_worker_count=%s,failed_worker_count=%s,
                   updated_at=clock_timestamp(),
                   completed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END
               WHERE rollout_id=%s""",
            (status, acknowledged, failed, complete, rollout_id),
        )
        if complete:
            rollout = conn.execute(
                """SELECT aggregate_id,revision_id,created_by
                   FROM configuration_rollouts WHERE rollout_id=%s""",
                (rollout_id,),
            ).fetchone()
            event_type = "rollout.failed"
            if not failed:
                conn.execute(
                    """UPDATE agent_definitions SET current_revision_id=%s,
                           updated_at=clock_timestamp() WHERE agent_id=%s""",
                    (rollout["revision_id"], rollout["aggregate_id"]),
                )
                event_type = "activated"
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('agent',%s,%s,%s,%s)""",
                (
                    rollout["aggregate_id"],
                    rollout["revision_id"],
                    event_type,
                    rollout["created_by"],
                ),
            )
            self._notify(conn, f"config:agent:{rollout['aggregate_id']}")

    @staticmethod
    def _configuration_rollout(row: dict[str, Any]) -> ConfigurationRolloutRecord:
        from joyhousebot.storage.postgres_store import _iso

        return ConfigurationRolloutRecord(
            rollout_id=str(row["rollout_id"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            revision_id=str(row["revision_id"]),
            status=str(row["status"]),
            created_by=str(row["created_by"]),
            target_worker_count=int(row["target_worker_count"]),
            acknowledged_worker_count=int(row["acknowledged_worker_count"]),
            failed_worker_count=int(row["failed_worker_count"]),
            created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",
            completed_at=_iso(row["completed_at"]),
        )
