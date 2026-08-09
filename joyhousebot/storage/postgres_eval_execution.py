"""Durable leased Eval execution jobs and periodic materialization."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.identity import payload_hash
from joyhousebot.storage.json_codec import Jsonb


class PostgresEvalExecutionStoreMixin:
    def enqueue_eval_execution(
        self,
        eval_run_id: str,
        *,
        configuration: dict[str, Any],
        requested_by: str,
        schedule_policy_id: str | None = None,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            run = conn.execute(
                "SELECT status FROM eval_runs WHERE eval_run_id=%s FOR UPDATE",
                (eval_run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("evaluation run not found")
            if str(run["status"]) != "running":
                raise ValueError("completed evaluation runs cannot be enqueued")
            row = conn.execute(
                """INSERT INTO eval_execution_jobs
                       (eval_run_id,status,configuration,requested_by,schedule_policy_id)
                   VALUES (%s,'queued',%s,%s,%s)
                   ON CONFLICT(eval_run_id) DO UPDATE SET
                       status=CASE WHEN eval_execution_jobs.status='failed'
                           THEN 'queued' ELSE eval_execution_jobs.status END,
                       configuration=CASE WHEN eval_execution_jobs.status='failed'
                           THEN EXCLUDED.configuration ELSE eval_execution_jobs.configuration END,
                       requested_by=CASE WHEN eval_execution_jobs.status='failed'
                           THEN EXCLUDED.requested_by ELSE eval_execution_jobs.requested_by END,
                       attempt=CASE WHEN eval_execution_jobs.status='failed'
                           THEN 0 ELSE eval_execution_jobs.attempt END,
                       available_at=CASE WHEN eval_execution_jobs.status='failed'
                           THEN clock_timestamp() ELSE eval_execution_jobs.available_at END,
                       error=CASE WHEN eval_execution_jobs.status='failed'
                           THEN NULL ELSE eval_execution_jobs.error END,
                       updated_at=clock_timestamp()
                   RETURNING *""",
                (eval_run_id, Jsonb(configuration), requested_by, schedule_policy_id),
            ).fetchone()
            self._notify(conn, "eval:queued")
            assert row is not None
            return self._eval_job(row)

    def get_eval_execution_job(self, eval_run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM eval_execution_jobs WHERE eval_run_id=%s", (eval_run_id,)
            ).fetchone()
        return self._eval_job(row) if row else None

    def claim_eval_execution_job(
        self, *, worker_id: str, lease_seconds: int = 90
    ) -> dict[str, Any] | None:
        lease = max(30, min(int(lease_seconds), 3600))
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """WITH candidate AS (
                       SELECT eval_run_id FROM eval_execution_jobs
                       WHERE attempt<max_attempts AND (
                           (status='queued' AND available_at<=clock_timestamp()) OR
                           (status='running' AND lease_expires_at<=clock_timestamp())
                       )
                       ORDER BY available_at,created_at
                       FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE eval_execution_jobs job SET status='running',
                       lease_owner=%s,lease_version=job.lease_version+1,
                       lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       attempt=job.attempt+1,
                       started_at=COALESCE(job.started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   FROM candidate WHERE job.eval_run_id=candidate.eval_run_id
                   RETURNING job.*""",
                (worker_id, lease),
            ).fetchone()
        return self._eval_job(row) if row else None

    def heartbeat_eval_execution_job(
        self,
        eval_run_id: str,
        *,
        worker_id: str,
        lease_version: int,
        lease_seconds: int = 90,
    ) -> bool:
        lease = max(30, min(int(lease_seconds), 3600))
        with self._pool.connection() as conn:
            changed = conn.execute(
                """UPDATE eval_execution_jobs
                   SET lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE eval_run_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s""",
                (lease, eval_run_id, worker_id, lease_version),
            ).rowcount
        return changed == 1

    def complete_eval_execution_job(
        self, eval_run_id: str, *, worker_id: str, lease_version: int
    ) -> bool:
        with self._pool.connection() as conn:
            changed = conn.execute(
                """UPDATE eval_execution_jobs SET status='completed',
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE eval_run_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s""",
                (eval_run_id, worker_id, lease_version),
            ).rowcount
        return changed == 1

    def fail_eval_execution_job(
        self,
        eval_run_id: str,
        *,
        worker_id: str,
        lease_version: int,
        error: dict[str, Any],
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM eval_execution_jobs WHERE eval_run_id=%s FOR UPDATE",
                (eval_run_id,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != "running"
                or str(row["lease_owner"]) != worker_id
                or int(row["lease_version"]) != int(lease_version)
            ):
                return False
            retry = int(row["attempt"]) < int(row["max_attempts"])
            delay = min(300, 2 ** max(0, int(row["attempt"]) - 1))
            conn.execute(
                """UPDATE eval_execution_jobs SET status=%s,error=%s,
                       available_at=CASE WHEN %s THEN
                           clock_timestamp()+(%s*interval '1 second') ELSE available_at END,
                       lease_owner=NULL,lease_expires_at=NULL,
                       finished_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
                       updated_at=clock_timestamp() WHERE eval_run_id=%s""",
                (
                    "queued" if retry else "failed",
                    Jsonb(error),
                    retry,
                    delay,
                    retry,
                    eval_run_id,
                ),
            )
            if retry:
                self._notify(conn, "eval:retry")
        return True

    def upsert_eval_schedule_policy(self, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            suite = conn.execute(
                """SELECT 1 FROM eval_suites WHERE suite_id=%s AND version=%s
                   AND status='active'""",
                (value["suite_id"], value["suite_version"]),
            ).fetchone()
            if suite is None:
                raise ValueError("active evaluation suite not found")
            row = conn.execute(
                """INSERT INTO eval_schedule_policies
                       (policy_id,suite_id,suite_version,target_type,target_id,
                        target_revision_id,cadence_seconds,enabled,
                        execution_configuration,next_run_at,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp()),%s)
                   ON CONFLICT(policy_id) DO UPDATE SET
                       suite_id=EXCLUDED.suite_id,suite_version=EXCLUDED.suite_version,
                       target_type=EXCLUDED.target_type,target_id=EXCLUDED.target_id,
                       target_revision_id=EXCLUDED.target_revision_id,
                       cadence_seconds=EXCLUDED.cadence_seconds,enabled=EXCLUDED.enabled,
                       execution_configuration=EXCLUDED.execution_configuration,
                       next_run_at=COALESCE(%s::timestamptz,eval_schedule_policies.next_run_at),
                       updated_at=clock_timestamp()
                   RETURNING *""",
                (
                    value["policy_id"],
                    value["suite_id"],
                    value["suite_version"],
                    value["target_type"],
                    value["target_id"],
                    value["target_revision_id"],
                    value["cadence_seconds"],
                    value.get("enabled", True),
                    Jsonb(value.get("execution_configuration") or {}),
                    value.get("next_run_at"),
                    value["created_by"],
                    value.get("next_run_at"),
                ),
            ).fetchone()
            assert row is not None
            return self._eval_schedule(row)

    def list_eval_schedule_policies(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_schedule_policies ORDER BY policy_id"
            ).fetchall()
        return [self._eval_schedule(row) for row in rows]

    def reconcile_due_eval_schedules(self, *, limit: int = 20) -> int:
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT * FROM eval_schedule_policies WHERE enabled
                   AND next_run_at<=clock_timestamp()
                   ORDER BY next_run_at,policy_id FOR UPDATE SKIP LOCKED LIMIT %s""",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            for policy in rows:
                occurrence = policy["next_run_at"].isoformat()
                request_hash = payload_hash(
                    {"policy_id": str(policy["policy_id"]), "occurrence": occurrence}
                )
                eval_run_id = f"evalrun_{request_hash}"
                conn.execute(
                    """INSERT INTO eval_runs
                           (eval_run_id,suite_id,suite_version,target_type,target_id,
                            target_revision_id,request_hash,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(request_hash) DO NOTHING""",
                    (
                        eval_run_id,
                        policy["suite_id"],
                        policy["suite_version"],
                        policy["target_type"],
                        policy["target_id"],
                        policy["target_revision_id"],
                        request_hash,
                        f"schedule:{policy['policy_id']}",
                    ),
                )
                conn.execute(
                    """INSERT INTO eval_execution_jobs
                           (eval_run_id,status,configuration,requested_by,schedule_policy_id)
                       VALUES (%s,'queued',%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (
                        eval_run_id,
                        Jsonb(dict(policy["execution_configuration"] or {})),
                        f"schedule:{policy['policy_id']}",
                        policy["policy_id"],
                    ),
                )
                conn.execute(
                    """UPDATE eval_schedule_policies SET last_eval_run_id=%s,
                           next_run_at=clock_timestamp()+(cadence_seconds*interval '1 second'),
                           updated_at=clock_timestamp() WHERE policy_id=%s""",
                    (eval_run_id, policy["policy_id"]),
                )
            if rows:
                self._notify(conn, "eval:schedule")
        return len(rows)

    @staticmethod
    def _eval_job(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "eval_run_id": str(row["eval_run_id"]),
            "status": str(row["status"]),
            "configuration": dict(row["configuration"] or {}),
            "requested_by": str(row["requested_by"]),
            "schedule_policy_id": row["schedule_policy_id"],
            "attempt": int(row["attempt"]),
            "max_attempts": int(row["max_attempts"]),
            "available_at": _iso(row["available_at"]),
            "lease_owner": row["lease_owner"],
            "lease_version": int(row["lease_version"]),
            "lease_expires_at": _iso(row["lease_expires_at"]),
            "error": dict(row["error"] or {}) or None,
            "created_at": _iso(row["created_at"]),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    @staticmethod
    def _eval_schedule(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "policy_id": str(row["policy_id"]),
            "suite_id": str(row["suite_id"]),
            "suite_version": int(row["suite_version"]),
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"]),
            "target_revision_id": str(row["target_revision_id"]),
            "cadence_seconds": int(row["cadence_seconds"]),
            "enabled": bool(row["enabled"]),
            "execution_configuration": dict(row["execution_configuration"] or {}),
            "next_run_at": _iso(row["next_run_at"]),
            "last_eval_run_id": row["last_eval_run_id"],
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
