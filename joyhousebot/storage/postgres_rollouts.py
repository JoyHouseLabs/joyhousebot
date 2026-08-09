"""PostgreSQL configuration rollout, approval, retry, timeout, and rollback."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.platform_records import (
    ConfigurationEventRecord,
    ConfigurationRolloutRecord,
)
from joyhousebot.storage.postgres_rollout_primitives import (
    PostgresRolloutPrimitiveStoreMixin,
)

_ACTIVE_ROLLOUT_STATUSES = ("rolling_out", "awaiting_approval")
_ROLLOUT_TYPES = ("agent", "capability", "scenario", "plugin")


class PostgresRolloutStoreMixin(PostgresRolloutPrimitiveStoreMixin):
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

    def list_configuration_rollouts(self, *, limit: int = 100) -> list[ConfigurationRolloutRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM configuration_rollouts
                   ORDER BY created_at DESC LIMIT %s""",
                (max(1, min(1000, limit)),),
            ).fetchall()
        return [self._configuration_rollout(row) for row in rows]

    def get_configuration_rollout(self, rollout_id: str) -> ConfigurationRolloutRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM configuration_rollouts WHERE rollout_id=%s",
                (rollout_id,),
            ).fetchone()
        return self._configuration_rollout(row) if row else None

    def list_configuration_rollout_targets(self, rollout_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT worker_id,status,error,acknowledged_at,attempt_count
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
                "attempt_count": int(row["attempt_count"]),
            }
            for row in rows
        ]

    def _create_configuration_rollout(
        self,
        conn: Any,
        *,
        aggregate_type: str,
        aggregate_id: str,
        revision_id: str,
        actor_id: str,
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
        rollback_of_rollout_id: str | None = None,
    ) -> str:
        if aggregate_type not in _ROLLOUT_TYPES:
            raise ValueError("unsupported configuration rollout type")
        if activation_mode not in {"automatic", "manual"}:
            raise ValueError("activation_mode must be automatic or manual")
        timeout = max(10, min(int(timeout_seconds), 86400))
        active = conn.execute(
            """SELECT rollout_id FROM configuration_rollouts
               WHERE aggregate_type=%s AND aggregate_id=%s
                 AND status IN ('rolling_out','awaiting_approval')
               FOR UPDATE""",
            (aggregate_type, aggregate_id),
        ).fetchone()
        if active is not None:
            raise ValueError(
                f"configuration rollout already active: {active['rollout_id']}"
            )
        targets = conn.execute(
            """SELECT worker_id FROM runtime_workers
               WHERE status='online'
                 AND last_heartbeat > clock_timestamp()-interval '2 minutes'
                 AND capabilities @> '{"agent": true}'::jsonb
               ORDER BY worker_id"""
        ).fetchall()
        if require_healthy_workers and not targets:
            raise ValueError("release requires at least one healthy Agent Worker")
        previous_revision_id = self._current_configuration_revision(
            conn, aggregate_type, aggregate_id
        )
        rollout_id = f"rollout_{uuid4().hex}"
        status = (
            "rolling_out"
            if targets
            else "awaiting_approval"
            if activation_mode == "manual"
            else "completed"
        )
        conn.execute(
            """INSERT INTO configuration_rollouts
                   (rollout_id,aggregate_type,aggregate_id,revision_id,status,
                    created_by,target_worker_count,previous_revision_id,
                    activation_mode,timeout_seconds,deadline_at,auto_rollback,
                    rollback_of_rollout_id,completed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       clock_timestamp()+(%s * interval '1 second'),%s,%s,
                       CASE WHEN %s='completed' THEN clock_timestamp() ELSE NULL END)""",
            (
                rollout_id,
                aggregate_type,
                aggregate_id,
                revision_id,
                status,
                actor_id,
                len(targets),
                previous_revision_id,
                activation_mode,
                timeout,
                timeout,
                auto_rollback,
                rollback_of_rollout_id,
                status,
            ),
        )
        if targets:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO configuration_rollout_targets(rollout_id,worker_id)
                       VALUES (%s,%s)""",
                    [(rollout_id, row["worker_id"]) for row in targets],
                )
        elif status == "completed":
            self._activate_configuration_revision(
                conn, aggregate_type, aggregate_id, revision_id
            )
            self._append_configuration_event(
                conn,
                aggregate_type,
                aggregate_id,
                revision_id,
                "activated",
                actor_id,
            )
        else:
            self._append_configuration_event(
                conn,
                aggregate_type,
                aggregate_id,
                revision_id,
                "rollout.awaiting_approval",
                actor_id,
            )
        return rollout_id

    def list_pending_configuration_revisions(self, worker_id: str) -> list[dict[str, str]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT r.rollout_id,r.aggregate_type,r.aggregate_id,r.revision_id
                   FROM configuration_rollout_targets t
                   JOIN configuration_rollouts r ON r.rollout_id=t.rollout_id
                   WHERE t.worker_id=%s AND t.status='pending'
                     AND r.status='rolling_out'
                   ORDER BY r.created_at""",
                (worker_id,),
            ).fetchall()
        return [
            {
                key: str(row[key])
                for key in (
                    "rollout_id",
                    "aggregate_type",
                    "aggregate_id",
                    "revision_id",
                )
            }
            for row in rows
        ]

    def list_pending_agent_revisions(self, worker_id: str) -> list[dict[str, str]]:
        return [
            {
                "rollout_id": item["rollout_id"],
                "agent_id": item["aggregate_id"],
                "revision_id": item["revision_id"],
            }
            for item in self.list_pending_configuration_revisions(worker_id)
            if item["aggregate_type"] == "agent"
        ]

    def acknowledge_configuration_revision(
        self,
        *,
        worker_id: str,
        aggregate_type: str,
        aggregate_id: str,
        revision_id: str,
        status: str = "loaded",
        error: dict[str, Any] | None = None,
    ) -> bool:
        if status not in {"loaded", "failed"}:
            raise ValueError("invalid rollout acknowledgement status")
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """UPDATE configuration_rollout_targets t SET status=%s,error=%s,
                       acknowledged_at=clock_timestamp()
                   FROM configuration_rollouts r
                   WHERE r.rollout_id=t.rollout_id AND t.worker_id=%s
                     AND t.status='pending' AND r.status='rolling_out'
                     AND r.aggregate_type=%s AND r.aggregate_id=%s AND r.revision_id=%s
                   RETURNING t.rollout_id""",
                (
                    status,
                    Jsonb(error) if error is not None else None,
                    worker_id,
                    aggregate_type,
                    aggregate_id,
                    revision_id,
                ),
            ).fetchall()
            for row in rows:
                self._refresh_rollout_state(conn, str(row["rollout_id"]))
        return bool(rows)

    def acknowledge_agent_revision(
        self,
        *,
        worker_id: str,
        agent_id: str,
        revision_id: str,
        status: str = "loaded",
        error: dict[str, Any] | None = None,
    ) -> bool:
        return self.acknowledge_configuration_revision(
            worker_id=worker_id,
            aggregate_type="agent",
            aggregate_id=agent_id,
            revision_id=revision_id,
            status=status,
            error=error,
        )

    def approve_configuration_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            rollout = conn.execute(
                """SELECT * FROM configuration_rollouts
                   WHERE rollout_id=%s FOR UPDATE""",
                (rollout_id,),
            ).fetchone()
            if rollout is None:
                return False
            if rollout["status"] != "awaiting_approval":
                raise ValueError("rollout is not awaiting activation approval")
            self._activate_configuration_revision(
                conn,
                str(rollout["aggregate_type"]),
                str(rollout["aggregate_id"]),
                str(rollout["revision_id"]),
            )
            conn.execute(
                """UPDATE configuration_rollouts SET status='completed',
                       approved_by=%s,approved_at=clock_timestamp(),
                       updated_at=clock_timestamp(),completed_at=clock_timestamp()
                   WHERE rollout_id=%s""",
                (actor_id, rollout_id),
            )
            self._append_configuration_event_from_rollout(
                conn, rollout, "activated", actor_id
            )
            self._notify_configuration(conn, rollout)
        return True

    def cancel_configuration_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            rollout = conn.execute(
                """SELECT * FROM configuration_rollouts
                   WHERE rollout_id=%s FOR UPDATE""",
                (rollout_id,),
            ).fetchone()
            if rollout is None:
                return False
            if rollout["status"] not in _ACTIVE_ROLLOUT_STATUSES:
                raise ValueError("only active rollouts can be cancelled")
            conn.execute(
                """UPDATE configuration_rollout_targets SET status='cancelled',
                       error='{"code":"ROLLOUT_CANCELLED"}'::jsonb,
                       acknowledged_at=clock_timestamp()
                   WHERE rollout_id=%s AND status='pending'""",
                (rollout_id,),
            )
            conn.execute(
                """UPDATE configuration_rollouts SET status='cancelled',
                       cancelled_by=%s,cancelled_at=clock_timestamp(),
                       updated_at=clock_timestamp(),completed_at=clock_timestamp(),
                       rollback_revision_id=CASE WHEN auto_rollback
                           THEN previous_revision_id ELSE rollback_revision_id END
                   WHERE rollout_id=%s""",
                (actor_id, rollout_id),
            )
            self._append_configuration_event_from_rollout(
                conn, rollout, "rollout.cancelled", actor_id
            )
            self._finish_parent_rollback(conn, rollout, succeeded=False, actor_id=actor_id)
        return True

    def retry_configuration_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            rollout = conn.execute(
                """SELECT * FROM configuration_rollouts
                   WHERE rollout_id=%s FOR UPDATE""",
                (rollout_id,),
            ).fetchone()
            if rollout is None:
                return False
            if rollout["status"] not in {"failed", "timed_out"}:
                raise ValueError("only failed or timed-out rollouts can be retried")
            reset = conn.execute(
                """UPDATE configuration_rollout_targets SET status='pending',error=NULL,
                       acknowledged_at=NULL,attempt_count=attempt_count+1
                   WHERE rollout_id=%s AND status IN ('failed','timed_out')""",
                (rollout_id,),
            )
            if reset.rowcount < 1:
                raise ValueError("rollout has no failed Worker targets to retry")
            timeout = int(rollout["timeout_seconds"])
            conn.execute(
                """UPDATE configuration_rollouts SET status='rolling_out',
                       failed_worker_count=0,
                       acknowledged_worker_count=(
                           SELECT count(*) FROM configuration_rollout_targets
                           WHERE rollout_id=%s AND status='loaded'
                       ),deadline_at=clock_timestamp()+(%s * interval '1 second'),
                       updated_at=clock_timestamp(),completed_at=NULL,
                       rollback_revision_id=NULL
                   WHERE rollout_id=%s""",
                (rollout_id, timeout, rollout_id),
            )
            self._append_configuration_event_from_rollout(
                conn, rollout, "rollout.retry_requested", actor_id
            )
            self._notify_configuration(conn, rollout)
        return True

    def rollback_configuration_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            rollout = conn.execute(
                """SELECT * FROM configuration_rollouts
                   WHERE rollout_id=%s FOR UPDATE""",
                (rollout_id,),
            ).fetchone()
            if rollout is None:
                return False
            if rollout["status"] != "completed":
                raise ValueError("only a completed rollout can be rolled back")
            previous = rollout["previous_revision_id"]
            if previous is None:
                raise ValueError("rollout has no previous active revision")
            current = self._current_configuration_revision(
                conn,
                str(rollout["aggregate_type"]),
                str(rollout["aggregate_id"]),
            )
            if current != str(rollout["revision_id"]):
                raise ValueError("rollout revision is no longer active")
            rollback_rollout_id = self._create_configuration_rollout(
                conn,
                aggregate_type=str(rollout["aggregate_type"]),
                aggregate_id=str(rollout["aggregate_id"]),
                revision_id=str(previous),
                actor_id=actor_id,
                activation_mode="automatic",
                timeout_seconds=int(rollout["timeout_seconds"]),
                auto_rollback=False,
                require_healthy_workers=True,
                rollback_of_rollout_id=rollout_id,
            )
            conn.execute(
                """UPDATE configuration_rollouts SET status='rollback_pending',
                       rollback_revision_id=%s,updated_at=clock_timestamp()
                   WHERE rollout_id=%s""",
                (str(previous), rollout_id),
            )
            self._append_configuration_event_from_rollout(
                conn, rollout, "rollback.requested", actor_id, revision_id=str(previous)
            )
            self._notify_configuration(conn, rollout)
        return bool(rollback_rollout_id)

    def reconcile_configuration_rollouts(self) -> int:
        """Expire overdue rollouts; called by the singleton maintenance Scheduler."""
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT * FROM configuration_rollouts
                   WHERE status='rolling_out' AND deadline_at<=clock_timestamp()
                   ORDER BY deadline_at FOR UPDATE SKIP LOCKED LIMIT 100"""
            ).fetchall()
            for rollout in rows:
                rollout_id = str(rollout["rollout_id"])
                conn.execute(
                    """UPDATE configuration_rollout_targets SET status='timed_out',
                           error='{"code":"ROLLOUT_TIMEOUT"}'::jsonb,
                           acknowledged_at=clock_timestamp()
                       WHERE rollout_id=%s AND status='pending'""",
                    (rollout_id,),
                )
                counts = conn.execute(
                    """SELECT count(*) FILTER (WHERE status<>'pending') AS acknowledged,
                              count(*) FILTER (WHERE status IN ('failed','timed_out')) AS failed
                       FROM configuration_rollout_targets WHERE rollout_id=%s""",
                    (rollout_id,),
                ).fetchone()
                rollback_revision = (
                    rollout["previous_revision_id"] if rollout["auto_rollback"] else None
                )
                conn.execute(
                    """UPDATE configuration_rollouts SET status='timed_out',
                           acknowledged_worker_count=%s,failed_worker_count=%s,
                           rollback_revision_id=%s,updated_at=clock_timestamp(),
                           completed_at=clock_timestamp() WHERE rollout_id=%s""",
                    (
                        int(counts["acknowledged"]),
                        int(counts["failed"]),
                        rollback_revision,
                        rollout_id,
                    ),
                )
                self._append_configuration_event_from_rollout(
                    conn, rollout, "rollout.timed_out", str(rollout["created_by"])
                )
                self._finish_parent_rollback(
                    conn,
                    rollout,
                    succeeded=False,
                    actor_id=str(rollout["created_by"]),
                )
        return len(rows)

    def _refresh_rollout_state(self, conn: Any, rollout_id: str) -> None:
        rollout = conn.execute(
            "SELECT * FROM configuration_rollouts WHERE rollout_id=%s FOR UPDATE",
            (rollout_id,),
        ).fetchone()
        if rollout is None or rollout["status"] != "rolling_out":
            return
        counts = conn.execute(
            """SELECT count(*) FILTER (WHERE status<>'pending') AS acknowledged,
                      count(*) FILTER (WHERE status='failed') AS failed,
                      count(*) AS total
               FROM configuration_rollout_targets WHERE rollout_id=%s""",
            (rollout_id,),
        ).fetchone()
        acknowledged = int(counts["acknowledged"])
        failed = int(counts["failed"])
        complete = acknowledged >= int(counts["total"])
        status = str(rollout["status"])
        if complete:
            if failed:
                status = "failed"
            elif rollout["activation_mode"] == "manual":
                status = "awaiting_approval"
            else:
                status = "completed"
        conn.execute(
            """UPDATE configuration_rollouts SET status=%s,
                   acknowledged_worker_count=%s,failed_worker_count=%s,
                   rollback_revision_id=CASE WHEN %s AND auto_rollback
                       THEN previous_revision_id ELSE rollback_revision_id END,
                   updated_at=clock_timestamp(),
                   completed_at=CASE WHEN %s IN ('completed','failed')
                       THEN clock_timestamp() ELSE NULL END
               WHERE rollout_id=%s""",
            (status, acknowledged, failed, bool(failed), status, rollout_id),
        )
        if not complete:
            return
        event_type = "rollout.failed"
        if not failed and status == "awaiting_approval":
            event_type = "rollout.awaiting_approval"
        elif not failed:
            self._activate_configuration_revision(
                conn,
                str(rollout["aggregate_type"]),
                str(rollout["aggregate_id"]),
                str(rollout["revision_id"]),
            )
            event_type = "activated"
        self._append_configuration_event_from_rollout(
            conn, rollout, event_type, str(rollout["created_by"])
        )
        self._finish_parent_rollback(
            conn,
            rollout,
            succeeded=not bool(failed) and status == "completed",
            actor_id=str(rollout["created_by"]),
        )
        self._notify_configuration(conn, rollout)
