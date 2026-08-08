"""PostgreSQL state machine for queryable external capability operations."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.reconciliation_records import OperationReconciliationRecord

_ACTIVE = ("pending", "checking", "manual_required")


class PostgresReconciliationStoreMixin:
    def migrate_reconciliations(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS operation_reconciliations (
            reconciliation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            action_id TEXT NOT NULL UNIQUE REFERENCES action_intents(action_id) ON DELETE CASCADE,
            invocation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            capability_ref JSONB NOT NULL,
            idempotency_key TEXT NOT NULL,
            operation JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending',
            required_role TEXT NOT NULL DEFAULT 'owner',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 20,
            next_attempt_at TIMESTAMPTZ,
            deadline_at TIMESTAMPTZ,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version BIGINT NOT NULL DEFAULT 0,
            result JSONB,
            error JSONB,
            last_error JSONB,
            resolution_source TEXT,
            resolved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            resolved_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_operation_reconciliations_run_created
            ON operation_reconciliations(run_id, created_at);
        CREATE INDEX IF NOT EXISTS ix_operation_reconciliations_due
            ON operation_reconciliations(next_attempt_at, created_at)
            WHERE status='pending';
        CREATE INDEX IF NOT EXISTS ix_operation_reconciliations_leased
            ON operation_reconciliations(lease_expires_at)
            WHERE status='checking';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341924,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="operation_reconciliations",
                version=1,
                ddl=ddl,
                description="leased external operation reconciliation state machine",
            )

    def ensure_operation_reconciliation(
        self, **kwargs: Any
    ) -> tuple[OperationReconciliationRecord, bool]:
        reconciliation_id = kwargs.get("reconciliation_id") or f"rec_{kwargs['action_id']}"
        status = kwargs.get("status") or "pending"
        if status not in {"pending", "manual_required"}:
            raise ValueError("invalid initial reconciliation status")
        with self._pool.connection() as conn, conn.transaction():
            action = conn.execute(
                "SELECT * FROM action_intents WHERE action_id=%s",
                (kwargs["action_id"],),
            ).fetchone()
            if action is None:
                raise RuntimeError("reconciliation Action does not exist")
            frozen = (
                str(action["run_id"]) == kwargs["run_id"]
                and str(action["invocation_id"]) == kwargs["invocation_id"]
                and str(action["idempotency_key"]) == kwargs["idempotency_key"]
                and dict(action["capability_ref"]) == kwargs["capability_ref"]
            )
            if not frozen:
                raise RuntimeError(
                    f"reconciliation Action identity conflict: {kwargs['action_id']}"
                )
            row = conn.execute(
                """INSERT INTO operation_reconciliations
                       (reconciliation_id,run_id,action_id,invocation_id,user_id,
                        capability_ref,idempotency_key,operation,status,required_role,
                        max_attempts,next_attempt_at,deadline_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s='pending' THEN clock_timestamp() ELSE NULL END,
                           clock_timestamp()+make_interval(secs => %s))
                   ON CONFLICT(action_id) DO NOTHING RETURNING *,TRUE AS created""",
                (
                    reconciliation_id,
                    kwargs["run_id"],
                    kwargs["action_id"],
                    kwargs["invocation_id"],
                    kwargs["user_id"],
                    Jsonb(kwargs["capability_ref"]),
                    kwargs["idempotency_key"],
                    Jsonb(kwargs.get("operation") or {}),
                    status,
                    kwargs.get("required_role") or "owner",
                    max(1, int(kwargs.get("max_attempts") or 20)),
                    status,
                    max(60, int(kwargs.get("deadline_seconds") or 86_400)),
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT *,FALSE AS created FROM operation_reconciliations WHERE action_id=%s FOR UPDATE",
                    (kwargs["action_id"],),
                ).fetchone()
            if (
                row is not None
                and not row["created"]
                and status == "manual_required"
                and row["status"] == "pending"
            ):
                row = conn.execute(
                    """UPDATE operation_reconciliations SET status='manual_required',
                           next_attempt_at=NULL,last_error=%s,updated_at=clock_timestamp()
                       WHERE action_id=%s AND status='pending'
                       RETURNING *,FALSE AS created""",
                    (
                        Jsonb(
                            {
                                "code": "RECONCILIATION_UNSUPPORTED",
                                "message": "capability does not expose reconciliation",
                            }
                        ),
                        kwargs["action_id"],
                    ),
                ).fetchone()
            conn.execute(
                """UPDATE action_intents SET status='waiting_external',updated_at=clock_timestamp()
                   WHERE action_id=%s AND status IN ('proposed','invoking','waiting_external')""",
                (kwargs["action_id"],),
            )
        assert row is not None
        record = self._operation_reconciliation(row)
        if (
            record.run_id != kwargs["run_id"]
            or record.user_id != kwargs["user_id"]
            or record.invocation_id != kwargs["invocation_id"]
            or record.capability_ref != kwargs["capability_ref"]
            or record.idempotency_key != kwargs["idempotency_key"]
        ):
            raise RuntimeError(f"reconciliation identity conflict: {record.reconciliation_id}")
        return record, bool(row["created"])

    def get_operation_reconciliation(
        self, reconciliation_id: str, *, expected_user_id: str | None = None
    ) -> OperationReconciliationRecord | None:
        clause = " AND user_id=%s" if expected_user_id is not None else ""
        params = (
            (reconciliation_id, expected_user_id)
            if expected_user_id is not None
            else (reconciliation_id,)
        )
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM operation_reconciliations WHERE reconciliation_id=%s" + clause,
                params,
            ).fetchone()
        return self._operation_reconciliation(row) if row else None

    def get_action_reconciliation(
        self, action_id: str
    ) -> OperationReconciliationRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM operation_reconciliations WHERE action_id=%s", (action_id,)
            ).fetchone()
        return self._operation_reconciliation(row) if row else None

    def list_run_operation_reconciliations(
        self, run_id: str, *, expected_user_id: str
    ) -> list[OperationReconciliationRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM operation_reconciliations
                   WHERE run_id=%s AND user_id=%s ORDER BY created_at,reconciliation_id""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._operation_reconciliation(row) for row in rows]

    def claim_operation_reconciliation(
        self, reconciliation_id: str, *, worker_id: str, lease_seconds: int = 30
    ) -> OperationReconciliationRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE operation_reconciliations SET status='manual_required',
                       next_attempt_at=NULL,lease_owner=NULL,lease_expires_at=NULL,
                       last_error=%s,updated_at=clock_timestamp()
                   WHERE reconciliation_id=%s AND status=ANY(%s)
                     AND (attempt_count>=max_attempts
                          OR (deadline_at IS NOT NULL AND deadline_at<=clock_timestamp()))""",
                (
                    Jsonb({"code": "RECONCILIATION_LIMIT", "message": "automatic reconciliation limit reached"}),
                    reconciliation_id,
                    ["pending", "checking"],
                ),
            )
            row = conn.execute(
                """UPDATE operation_reconciliations SET status='checking',lease_owner=%s,
                       lease_expires_at=clock_timestamp()+make_interval(secs => %s),
                       lease_version=lease_version+1,attempt_count=attempt_count+1,
                       updated_at=clock_timestamp()
                   WHERE reconciliation_id=%s
                     AND ((status='pending' AND COALESCE(next_attempt_at,clock_timestamp())
                                                <=clock_timestamp())
                          OR (status='checking' AND lease_expires_at<clock_timestamp()))
                     AND attempt_count<max_attempts
                     AND (deadline_at IS NULL OR deadline_at>clock_timestamp())
                   RETURNING *""",
                (worker_id, max(5, int(lease_seconds)), reconciliation_id),
            ).fetchone()
        return self._operation_reconciliation(row) if row else None

    def defer_operation_reconciliation(self, reconciliation_id: str, **kwargs: Any) -> bool:
        status = "manual_required" if kwargs.get("manual_required") else "pending"
        requested_delay = kwargs.get("retry_after_seconds")
        delay = max(0, min(86_400, int(5 if requested_delay is None else requested_delay)))
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE operation_reconciliations SET status=%s,last_error=%s,
                       operation=COALESCE(%s,operation),
                       next_attempt_at=CASE WHEN %s='pending' THEN
                           clock_timestamp()+make_interval(secs => %s) ELSE NULL END,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE reconciliation_id=%s AND status='checking' AND lease_owner=%s
                     AND lease_version=%s RETURNING run_id""",
                (
                    status,
                    Jsonb(kwargs.get("last_error") or {}),
                    Jsonb(kwargs["operation"]) if kwargs.get("operation") else None,
                    status,
                    delay,
                    reconciliation_id,
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if row is not None and status == "pending":
                self._notify(conn, str(row["run_id"]))
        return row is not None

    def suspend_run_for_reconciliation(self, **kwargs: Any) -> bool:
        result = {
            "stop_reason": "waiting_external",
            "action_id": kwargs["action_id"],
            "invocation_id": kwargs["invocation_id"],
            "reconciliation_id": kwargs["reconciliation_id"],
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_runs AS run SET status='waiting_external',result=%s,error=NULL,
                       current_phase='waiting',status_summary='等待外部操作确认',
                       status_reason='capability operation requires reconciliation',
                       next_action='reconcile external operation',waiting_on=%s,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   FROM operation_reconciliations AS rec
                   WHERE run.run_id=%s AND run.lease_owner=%s AND run.lease_version=%s
                     AND rec.reconciliation_id=%s AND rec.run_id=run.run_id
                     AND rec.action_id=%s AND rec.status=ANY(%s)
                   RETURNING run.run_id""",
                (
                    Jsonb(result),
                    kwargs["reconciliation_id"],
                    kwargs["run_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                    kwargs["reconciliation_id"],
                    kwargs["action_id"],
                    list(_ACTIVE),
                ),
            ).fetchone()
            if row is not None:
                self._notify(conn, kwargs["run_id"])
        return row is not None

    def complete_operation_reconciliation(self, reconciliation_id: str, **kwargs: Any) -> OperationReconciliationRecord | None:
        result = kwargs["result"]
        status = str(result["status"])
        if status not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ValueError("reconciliation completion requires terminal capability result")
        source = kwargs.get("resolution_source") or "provider"
        worker_id = kwargs.get("worker_id")
        lease_version = kwargs.get("lease_version")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM operation_reconciliations WHERE reconciliation_id=%s FOR UPDATE",
                (reconciliation_id,),
            ).fetchone()
            if row is None:
                return None
            if source != "provider" and (
                str(row["run_id"]) != kwargs.get("run_id")
                or str(row["user_id"]) != kwargs.get("user_id")
            ):
                return None
            if row["status"] in {"succeeded", "failed"}:
                return self._operation_reconciliation(row)
            if source == "provider" and (
                row["status"] != "checking"
                or row["lease_owner"] != worker_id
                or int(row["lease_version"]) != int(lease_version or -1)
            ):
                return None
            saved = conn.execute(
                """UPDATE operation_reconciliations SET status=%s,result=%s,error=%s,
                       operation=%s,resolution_source=%s,resolved_by=%s,
                       lease_owner=NULL,lease_expires_at=NULL,next_attempt_at=NULL,
                       resolved_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE reconciliation_id=%s RETURNING *""",
                (
                    "succeeded" if status == "succeeded" else "failed",
                    Jsonb(result),
                    Jsonb(result.get("error")) if result.get("error") else None,
                    Jsonb(kwargs.get("operation") or dict(row["operation"])),
                    source,
                    kwargs.get("resolved_by") or worker_id or "system",
                    reconciliation_id,
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO action_observations
                       (observation_id,action_id,run_id,invocation_id,status,result,error,
                        operation,reconciliation_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'confirmed')
                   ON CONFLICT(action_id) DO UPDATE SET status=EXCLUDED.status,
                       result=EXCLUDED.result,error=EXCLUDED.error,operation=EXCLUDED.operation,
                       reconciliation_status='confirmed',observed_at=clock_timestamp()""",
                (
                    f"obs_{row['action_id']}",
                    row["action_id"],
                    row["run_id"],
                    row["invocation_id"],
                    status,
                    Jsonb(result),
                    Jsonb(result.get("error")) if result.get("error") else None,
                    Jsonb(kwargs.get("operation") or dict(row["operation"])),
                ),
            )
            conn.execute(
                "UPDATE action_intents SET status='observed',updated_at=clock_timestamp() WHERE action_id=%s",
                (row["action_id"],),
            )
            conn.execute(
                """UPDATE capability_invocations SET status=%s,result=%s,error=%s,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE invocation_id=%s""",
                (
                    status,
                    Jsonb(result),
                    Jsonb(result.get("error")) if result.get("error") else None,
                    row["invocation_id"],
                ),
            )
            resumed_graph = self._resume_graph_action_task(
                conn,
                action_id=str(row["action_id"]),
                waiting_status="waiting_external",
            )
            if not resumed_graph:
                conn.execute(
                    """UPDATE runtime_runs SET status='queued',result=NULL,error=NULL,
                           current_phase='execution',status_summary='外部操作已确认，等待继续执行',
                           status_reason='operation reconciled',next_action='resume frozen Action',
                           waiting_on=NULL,finished_at=NULL,updated_at=clock_timestamp()
                       WHERE run_id=%s AND status='waiting_external' AND waiting_on=%s""",
                    (row["run_id"], reconciliation_id),
                )
            self._notify(conn, str(row["run_id"]))
        return self._operation_reconciliation(saved) if saved else None

    def retry_operation_reconciliation(self, reconciliation_id: str, **kwargs: Any) -> OperationReconciliationRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE operation_reconciliations SET status='pending',next_attempt_at=clock_timestamp(),
                       lease_owner=NULL,lease_expires_at=NULL,last_error=NULL,
                       resolved_by=%s,updated_at=clock_timestamp()
                   WHERE reconciliation_id=%s AND run_id=%s AND user_id=%s
                     AND status='manual_required' RETURNING *""",
                (
                    kwargs["actor_id"],
                    reconciliation_id,
                    kwargs["run_id"],
                    kwargs["user_id"],
                ),
            ).fetchone()
            if row is None:
                return None
            resumed_graph = self._resume_graph_action_task(
                conn,
                action_id=str(row["action_id"]),
                waiting_status="waiting_external",
            )
            if not resumed_graph:
                conn.execute(
                    """UPDATE runtime_runs SET status='queued',result=NULL,error=NULL,
                           current_phase='execution',status_summary='外部操作重新对账',
                           status_reason='manual reconciliation retry',
                           next_action='query external operation',waiting_on=NULL,
                           updated_at=clock_timestamp()
                       WHERE run_id=%s AND status='waiting_external' AND waiting_on=%s""",
                    (kwargs["run_id"], reconciliation_id),
                )
            self._notify(conn, kwargs["run_id"])
        return self._operation_reconciliation(row)

    @staticmethod
    def _operation_reconciliation(row: dict[str, Any]) -> OperationReconciliationRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return OperationReconciliationRecord(
            reconciliation_id=str(row["reconciliation_id"]), run_id=str(row["run_id"]),
            action_id=str(row["action_id"]), invocation_id=str(row["invocation_id"]),
            user_id=str(row["user_id"]), capability_ref=dict(_json(row["capability_ref"], {})),
            idempotency_key=str(row["idempotency_key"]), operation=dict(_json(row["operation"], {})),
            status=str(row["status"]), required_role=str(row["required_role"]),
            attempt_count=int(row["attempt_count"]), max_attempts=int(row["max_attempts"]),
            next_attempt_at=_iso(row["next_attempt_at"]), deadline_at=_iso(row["deadline_at"]),
            lease_owner=row["lease_owner"], lease_expires_at=_iso(row["lease_expires_at"]),
            lease_version=int(row["lease_version"]), result=_json(row["result"]),
            error=_json(row["error"]), last_error=_json(row["last_error"]),
            resolution_source=row["resolution_source"], resolved_by=row["resolved_by"],
            created_at=_iso(row["created_at"]) or "", updated_at=_iso(row["updated_at"]) or "",
            resolved_at=_iso(row["resolved_at"]),
        )
