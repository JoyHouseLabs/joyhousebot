"""PostgreSQL state machine for frozen Action and Graph approval requests."""

from __future__ import annotations

from typing import Any

from porthouse.contracts.events import AgentEvent, EventType
from porthouse.storage.approval_records import ApprovalRequestRecord
from porthouse.storage.json_codec import Jsonb
from porthouse.storage.postgres_event_writes import append_runtime_event_in_transaction


class PostgresApprovalStoreMixin:
    def migrate_approvals(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS approval_requests (
            approval_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            action_id TEXT NOT NULL UNIQUE REFERENCES action_intents(action_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            capability_ref JSONB NOT NULL,
            input_hash TEXT NOT NULL,
            input_preview JSONB NOT NULL DEFAULT '{}'::jsonb,
            risk TEXT NOT NULL,
            data_classification TEXT NOT NULL DEFAULT 'internal',
            required_role TEXT NOT NULL DEFAULT 'owner',
            status TEXT NOT NULL DEFAULT 'pending',
            requested_by TEXT NOT NULL,
            resolution TEXT,
            resolution_note TEXT,
            resolved_by TEXT,
            consumed_by TEXT,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            consumed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_approval_requests_run_requested
            ON approval_requests(run_id, requested_at);
        CREATE INDEX IF NOT EXISTS ix_approval_requests_user_pending
            ON approval_requests(user_id, requested_at)
            WHERE status IN ('pending', 'approved');
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341923,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="approvals",
                version=1,
                ddl=ddl,
                description="frozen Action approval requests and execution claims",
            )
            upgrade = """
            ALTER TABLE approval_requests ALTER COLUMN action_id DROP NOT NULL;
            ALTER TABLE approval_requests
                ADD COLUMN IF NOT EXISTS task_id TEXT
                    REFERENCES runtime_tasks(task_id) ON DELETE CASCADE;
            ALTER TABLE approval_requests
                ADD COLUMN IF NOT EXISTS subject_type TEXT NOT NULL DEFAULT 'action';
            ALTER TABLE approval_requests
                ADD COLUMN IF NOT EXISTS subject JSONB NOT NULL DEFAULT '{}'::jsonb;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_requests_pending_graph_task
                ON approval_requests(task_id)
                WHERE subject_type='graph_node' AND status='pending';
            """
            conn.execute(upgrade)
            self._record_migration(
                conn,
                name="approvals",
                version=2,
                ddl=upgrade,
                description="explicit Graph approval gates without synthetic capability Actions",
            )

    def create_approval_request(self, **kwargs: Any) -> tuple[ApprovalRequestRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            action = conn.execute(
                "SELECT * FROM action_intents WHERE action_id=%s FOR UPDATE",
                (kwargs["action_id"],),
            ).fetchone()
            if action is None:
                raise RuntimeError("approval Action does not exist")
            frozen = (
                str(action["run_id"]) == kwargs["run_id"]
                and str(action["input_hash"]) == kwargs["input_hash"]
                and dict(action["capability_ref"]) == kwargs["capability_ref"]
            )
            if not frozen:
                raise RuntimeError(f"approval Action identity conflict: {kwargs['action_id']}")
            row = conn.execute(
                """INSERT INTO approval_requests
                       (approval_id,run_id,action_id,user_id,capability_ref,input_hash,
                        input_preview,risk,data_classification,required_role,requested_by,
                        expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s::integer IS NULL THEN NULL
                                ELSE clock_timestamp()+make_interval(secs => %s) END)
                   ON CONFLICT(action_id) DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    kwargs["approval_id"],
                    kwargs["run_id"],
                    kwargs["action_id"],
                    kwargs["user_id"],
                    Jsonb(kwargs["capability_ref"]),
                    kwargs["input_hash"],
                    Jsonb(kwargs.get("input_preview") or {}),
                    kwargs["risk"],
                    kwargs.get("data_classification") or "internal",
                    kwargs.get("required_role") or "owner",
                    kwargs.get("requested_by") or "system",
                    kwargs.get("expires_in_seconds"),
                    kwargs.get("expires_in_seconds"),
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT *,FALSE AS created FROM approval_requests WHERE action_id=%s",
                    (kwargs["action_id"],),
                ).fetchone()
            conn.execute(
                """UPDATE action_intents SET status='approval_pending',
                       updated_at=clock_timestamp()
                   WHERE action_id=%s AND status='proposed'""",
                (kwargs["action_id"],),
            )
        assert row is not None
        record = self._approval_request(row)
        if (
            record.run_id != kwargs["run_id"]
            or record.user_id != kwargs["user_id"]
            or record.input_hash != kwargs["input_hash"]
            or record.capability_ref != kwargs["capability_ref"]
        ):
            raise RuntimeError(f"approval request identity conflict: {record.approval_id}")
        return record, bool(row["created"])

    def get_approval_request(
        self, approval_id: str, *, expected_user_id: str | None = None
    ) -> ApprovalRequestRecord | None:
        clause = " AND user_id=%s" if expected_user_id is not None else ""
        params: tuple[Any, ...] = (
            (approval_id, expected_user_id) if expected_user_id is not None else (approval_id,)
        )
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE approval_id=%s" + clause,
                params,
            ).fetchone()
        return self._approval_request(row) if row else None

    def get_action_approval(self, action_id: str) -> ApprovalRequestRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE action_id=%s", (action_id,)
            ).fetchone()
        return self._approval_request(row) if row else None

    def list_run_approval_requests(
        self, run_id: str, *, expected_user_id: str
    ) -> list[ApprovalRequestRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM approval_requests
                   WHERE run_id=%s AND user_id=%s ORDER BY requested_at,approval_id""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._approval_request(row) for row in rows]

    def list_pending_user_approval_requests(
        self, *, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Project pending approvals with their owning Run for the action view."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT approval.*,run.status AS run_status,
                          run.status_summary,run.prompt AS run_prompt,
                          run.agent_id,run.updated_at AS run_updated_at,
                          run.options AS run_options
                   FROM approval_requests AS approval
                   JOIN runtime_runs AS run ON run.run_id=approval.run_id
                   WHERE approval.user_id=%s AND approval.status='pending'
                     AND run.status='waiting_approval'
                   ORDER BY approval.requested_at ASC,approval.approval_id ASC
                   LIMIT %s""",
                (user_id, max(1, min(500, limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def suspend_run_for_approval(
        self,
        *,
        run_id: str,
        approval_id: str,
        action_id: str,
        worker_id: str,
        lease_version: int,
    ) -> bool:
        result = {
            "stop_reason": "waiting_approval",
            "approval_id": approval_id,
            "action_id": action_id,
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_runs AS run
                   SET status='waiting_approval', result=%s, error=NULL,
                       current_phase='waiting', status_summary='等待操作审批',
                       status_reason='capability side effect requires approval',
                       next_action='review approval request', waiting_on=%s,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   FROM approval_requests AS approval
                   WHERE run.run_id=%s AND run.lease_owner=%s AND run.lease_version=%s
                     AND approval.approval_id=%s AND approval.run_id=run.run_id
                     AND approval.action_id=%s AND approval.status='pending'
                   RETURNING run.run_id""",
                (
                    Jsonb(result),
                    approval_id,
                    run_id,
                    worker_id,
                    lease_version,
                    approval_id,
                    action_id,
                ),
            ).fetchone()
            if row is not None:
                self._notify(conn, run_id)
        return row is not None

    def resolve_approval_request(self, **kwargs: Any) -> ApprovalRequestRecord | None:
        resolution = str(kwargs["resolution"])
        if resolution not in {"approve", "reject", "request_changes", "revoke"}:
            raise ValueError("invalid approval resolution")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT approval.*,action.status AS action_status,run.status AS run_status,
                          run.waiting_on
                   FROM approval_requests AS approval
                   LEFT JOIN action_intents AS action ON action.action_id=approval.action_id
                   JOIN runtime_runs AS run ON run.run_id=approval.run_id
                   WHERE approval.approval_id=%s AND approval.run_id=%s
                     AND approval.user_id=%s
                   FOR UPDATE OF approval,run""",
                (kwargs["approval_id"], kwargs["run_id"], kwargs["user_id"]),
            ).fetchone()
            if row is None:
                return None
            if str(row.get("subject_type") or "action") == "graph_node":
                saved = self._resolve_explicit_graph_approval(conn, row=row, **kwargs)
                if saved is not None:
                    self._append_approval_resolution_event(
                        conn,
                        run_id=kwargs["run_id"],
                        task_id=str(row["task_id"]),
                        approval_id=kwargs["approval_id"],
                        action_id=None,
                        status=str(saved["status"]),
                        resolution=str(saved["resolution"] or resolution),
                        actor_id=kwargs["actor_id"],
                    )
                    self._append_approval_task_event(
                        conn,
                        run_id=kwargs["run_id"],
                        task_id=str(row["task_id"]),
                        approval_id=kwargs["approval_id"],
                        approved=str(saved["status"]) == "approved",
                        explicit=True,
                    )
                self._notify(conn, kwargs["run_id"])
                return self._approval_request(saved) if saved else None
            action = conn.execute(
                "SELECT status FROM action_intents WHERE action_id=%s FOR UPDATE",
                (row["action_id"],),
            ).fetchone()
            if action is None:
                return None
            graph_task = self._graph_action_task(conn, str(row["action_id"]))
            expected = "approved" if resolution == "revoke" else "pending"
            if row["status"] != expected or action["status"] != "approval_pending":
                return None
            if resolution != "revoke":
                if graph_task is not None:
                    if graph_task["status"] != "waiting_approval":
                        return None
                elif (
                    row["run_status"] != "waiting_approval"
                    or row["waiting_on"] != kwargs["approval_id"]
                ):
                    return None
            expired = bool(
                row["expires_at"] is not None
                and conn.execute(
                    "SELECT %s < clock_timestamp() AS expired", (row["expires_at"],)
                ).fetchone()["expired"]
            )
            status = (
                "expired"
                if expired
                else {
                    "approve": "approved",
                    "reject": "rejected",
                    "request_changes": "changes_requested",
                    "revoke": "revoked",
                }[resolution]
            )
            saved = conn.execute(
                """UPDATE approval_requests SET status=%s,resolution=%s,
                       resolution_note=%s,resolved_by=%s,resolved_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE approval_id=%s RETURNING *""",
                (status, resolution, kwargs.get("note"), kwargs["actor_id"], kwargs["approval_id"]),
            ).fetchone()
            assert saved is not None
            self._append_approval_resolution_event(
                conn,
                run_id=kwargs["run_id"],
                task_id=(str(graph_task["task_id"]) if graph_task is not None else None),
                approval_id=kwargs["approval_id"],
                action_id=str(row["action_id"]),
                status=status,
                resolution=resolution,
                actor_id=kwargs["actor_id"],
            )
            if status == "approved":
                resumed_graph = self._resume_graph_action_task(
                    conn,
                    action_id=str(row["action_id"]),
                    waiting_status="waiting_approval",
                )
                if not resumed_graph:
                    conn.execute(
                        """UPDATE runtime_runs SET status='queued',result=NULL,error=NULL,
                               current_phase='execution',status_summary='审批通过，等待继续执行',
                               status_reason='approval granted',next_action='resume frozen Action',
                               waiting_on=NULL,finished_at=NULL,updated_at=clock_timestamp()
                           WHERE run_id=%s""",
                        (kwargs["run_id"],),
                    )
                self._append_approval_task_event(
                    conn,
                    run_id=kwargs["run_id"],
                    task_id=(str(graph_task["task_id"]) if resumed_graph else None),
                    approval_id=kwargs["approval_id"],
                    approved=True,
                    explicit=False,
                )
            else:
                conn.execute(
                    """UPDATE action_intents SET status=%s,updated_at=clock_timestamp()
                       WHERE action_id=%s""",
                    (status, row["action_id"]),
                )
                error = {"code": f"approval_{status}", "message": kwargs.get("note") or status}
                failed_graph = self._fail_graph_action_task(
                    conn,
                    action_id=str(row["action_id"]),
                    waiting_status=(
                        str(graph_task["status"]) if graph_task is not None else "waiting_approval"
                    ),
                    error=error,
                )
                if not failed_graph:
                    terminal = self._finish_runtime_run_in_transaction(
                        conn,
                        run_id=kwargs["run_id"],
                        status="failed",
                        event=AgentEvent(
                            event_id=f"approval:{kwargs['approval_id']}:run.failed",
                            run_id=kwargs["run_id"],
                            type=EventType.RUN_FAILED.value,
                            status="failed",
                            summary="审批未通过",
                            data={"reason": f"approval_{status}"},
                        ),
                        error=error,
                        force_fence=resolution == "revoke",
                    )
                    if terminal is None:
                        raise RuntimeError("approval terminal Run transition was fenced")
                else:
                    self._append_approval_task_event(
                        conn,
                        run_id=kwargs["run_id"],
                        task_id=str(graph_task["task_id"]),
                        approval_id=kwargs["approval_id"],
                        approved=False,
                        explicit=False,
                        reason=f"approval_{status}",
                    )
            self._notify(conn, kwargs["run_id"])
        return self._approval_request(saved) if saved else None

    def claim_approved_action(self, action_id: str, *, worker_id: str) -> bool:
        """Consume approval and acquire Action execution in one transaction."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT approval.approval_id,approval.status,
                          action.status AS action_status
                   FROM approval_requests AS approval
                   JOIN action_intents AS action ON action.action_id=approval.action_id
                   WHERE approval.action_id=%s
                   FOR UPDATE OF approval,action""",
                (action_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "approved"
                or row["action_status"] != "approval_pending"
            ):
                return False
            conn.execute(
                """UPDATE approval_requests SET status='consumed',consumed_by=%s,
                       consumed_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE approval_id=%s""",
                (worker_id, row["approval_id"]),
            )
            conn.execute(
                """UPDATE action_intents SET status='invoking',worker_id=%s,
                       updated_at=clock_timestamp() WHERE action_id=%s""",
                (worker_id, action_id),
            )
        return True

    def expire_due_approval_requests(self, *, limit: int = 500) -> list[ApprovalRequestRecord]:
        """Expire pending requests and their waiting Runs atomically."""
        saved: list[dict[str, Any]] = []
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT approval.* FROM approval_requests AS approval
                   LEFT JOIN action_intents AS action ON action.action_id=approval.action_id
                   JOIN runtime_runs AS run ON run.run_id=approval.run_id
                   WHERE approval.status='pending'
                     AND approval.expires_at < clock_timestamp()
                     AND (
                       (approval.subject_type='action' AND action.status='approval_pending'
                        AND (
                          (action.task_id IS NULL AND run.status='waiting_approval'
                           AND run.waiting_on=approval.approval_id)
                          OR EXISTS (
                            SELECT 1 FROM runtime_tasks task
                            WHERE task.task_id=action.task_id
                              AND task.status='waiting_approval'
                              AND task.result->>'approval_id'=approval.approval_id
                          )
                        ))
                       OR (approval.subject_type='graph_node' AND EXISTS (
                         SELECT 1 FROM runtime_tasks task WHERE task.task_id=approval.task_id
                           AND task.status='waiting_approval'
                           AND task.result->>'approval_id'=approval.approval_id
                       ))
                     )
                   ORDER BY approval.expires_at
                   LIMIT %s FOR UPDATE OF approval,run SKIP LOCKED""",
                (max(1, min(5000, int(limit))),),
            ).fetchall()
            for row in rows:
                if str(row.get("subject_type") or "action") == "graph_node":
                    updated = self._expire_explicit_graph_approval(conn, row)
                    if updated is not None:
                        self._append_approval_resolution_event(
                            conn,
                            run_id=str(row["run_id"]),
                            task_id=str(row["task_id"]),
                            approval_id=str(row["approval_id"]),
                            action_id=None,
                            status="expired",
                            resolution="expired",
                            actor_id="system:expiry",
                        )
                        self._append_approval_task_event(
                            conn,
                            run_id=str(row["run_id"]),
                            task_id=str(row["task_id"]),
                            approval_id=str(row["approval_id"]),
                            approved=False,
                            explicit=True,
                            reason="approval_expired",
                        )
                        saved.append(updated)
                    self._notify(conn, str(row["run_id"]))
                    continue
                updated = conn.execute(
                    """UPDATE approval_requests SET status='expired',resolution='expired',
                           resolved_by='system:expiry',resolved_at=clock_timestamp(),
                           updated_at=clock_timestamp()
                       WHERE approval_id=%s RETURNING *""",
                    (row["approval_id"],),
                ).fetchone()
                self._append_approval_resolution_event(
                    conn,
                    run_id=str(row["run_id"]),
                    task_id=(str(row["task_id"]) if row.get("task_id") else None),
                    approval_id=str(row["approval_id"]),
                    action_id=str(row["action_id"]),
                    status="expired",
                    resolution="expired",
                    actor_id="system:expiry",
                )
                conn.execute(
                    """UPDATE action_intents SET status='expired',updated_at=clock_timestamp()
                       WHERE action_id=%s""",
                    (row["action_id"],),
                )
                error = {
                    "code": "approval_expired",
                    "message": "approval request expired",
                }
                failed_graph = self._fail_graph_action_task(
                    conn,
                    action_id=str(row["action_id"]),
                    waiting_status="waiting_approval",
                    error=error,
                )
                if not failed_graph:
                    terminal = self._finish_runtime_run_in_transaction(
                        conn,
                        run_id=str(row["run_id"]),
                        status="failed",
                        event=AgentEvent(
                            event_id=f"approval:{row['approval_id']}:run.failed",
                            run_id=str(row["run_id"]),
                            type=EventType.RUN_FAILED.value,
                            status="failed",
                            summary="审批请求已过期",
                            data={"reason": "approval_expired"},
                        ),
                        error=error,
                    )
                    if terminal is None:
                        raise RuntimeError("expired approval terminal Run transition was fenced")
                else:
                    self._append_approval_task_event(
                        conn,
                        run_id=str(row["run_id"]),
                        task_id=str(row["task_id"]),
                        approval_id=str(row["approval_id"]),
                        approved=False,
                        explicit=False,
                        reason="approval_expired",
                    )
                if updated is not None:
                    saved.append(updated)
                self._notify(conn, str(row["run_id"]))
        return [self._approval_request(row) for row in saved]

    @staticmethod
    def _append_approval_resolution_event(
        conn: Any,
        *,
        run_id: str,
        task_id: str | None,
        approval_id: str,
        action_id: str | None,
        status: str,
        resolution: str,
        actor_id: str,
    ) -> None:
        append_runtime_event_in_transaction(
            conn,
            AgentEvent(
                event_id=f"approval:{approval_id}:resolved:{status}",
                run_id=run_id,
                task_id=task_id,
                type=EventType.APPROVAL_RESOLVED.value,
                status=status,
                data={
                    "approval_id": approval_id,
                    "action_id": action_id,
                    "resolution": resolution,
                    "resolved_by": actor_id,
                },
            ),
        )

    @staticmethod
    def _append_approval_task_event(
        conn: Any,
        *,
        run_id: str,
        task_id: str | None,
        approval_id: str,
        approved: bool,
        explicit: bool,
        reason: str | None = None,
    ) -> None:
        if explicit:
            event_type = EventType.TASK_COMPLETED if approved else EventType.TASK_FAILED
        elif task_id:
            event_type = EventType.TASK_QUEUED if approved else EventType.TASK_FAILED
        else:
            event_type = EventType.RUN_QUEUED if approved else EventType.RUN_FAILED
        status = "completed" if explicit and approved else "queued" if approved else "failed"
        append_runtime_event_in_transaction(
            conn,
            AgentEvent(
                event_id=f"approval:{approval_id}:{event_type.value}",
                run_id=run_id,
                task_id=task_id,
                type=event_type.value,
                status=status,
                data={"reason": reason or "approval_granted", "approval_id": approval_id},
            ),
        )

    @staticmethod
    def _approval_request(row: dict[str, Any]) -> ApprovalRequestRecord:
        from porthouse.storage.postgres_store import _iso, _json

        return ApprovalRequestRecord(
            approval_id=str(row["approval_id"]),
            run_id=str(row["run_id"]),
            action_id=(str(row["action_id"]) if row["action_id"] is not None else None),
            task_id=(str(row["task_id"]) if row.get("task_id") is not None else None),
            subject_type=str(row.get("subject_type") or "action"),
            subject=dict(_json(row.get("subject"), {})),
            user_id=str(row["user_id"]),
            capability_ref=dict(_json(row["capability_ref"], {})),
            input_hash=str(row["input_hash"]),
            input_preview=dict(_json(row["input_preview"], {})),
            risk=str(row["risk"]),
            data_classification=str(row["data_classification"]),
            required_role=str(row["required_role"]),
            status=str(row["status"]),
            requested_by=str(row["requested_by"]),
            resolution=row["resolution"],
            resolution_note=row["resolution_note"],
            resolved_by=row["resolved_by"],
            consumed_by=row["consumed_by"],
            requested_at=_iso(row["requested_at"]) or "",
            expires_at=_iso(row["expires_at"]),
            resolved_at=_iso(row["resolved_at"]),
            consumed_at=_iso(row["consumed_at"]),
            updated_at=_iso(row["updated_at"]) or "",
        )
