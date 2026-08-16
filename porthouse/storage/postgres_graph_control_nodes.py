"""Atomic PostgreSQL transitions for explicit Graph approval nodes."""

from __future__ import annotations

import json
from typing import Any

from porthouse.storage.approval_records import ApprovalRequestRecord
from porthouse.storage.json_codec import Jsonb


class PostgresGraphControlNodeStoreMixin:
    def suspend_graph_task_for_explicit_approval(
        self, **kwargs: Any
    ) -> ApprovalRequestRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                """SELECT task.*,run.user_id FROM runtime_tasks task
                   JOIN runtime_runs run ON run.run_id=task.run_id
                   WHERE task.task_id=%s FOR UPDATE OF task,run""",
                (kwargs["task_id"],),
            ).fetchone()
            if (
                task is None
                or str(task["run_id"]) != kwargs["run_id"]
                or str(task["status"]) != "running"
                or str(task["lease_owner"] or "") != kwargs["worker_id"]
                or int(task["lease_version"]) != int(kwargs["lease_version"])
                or str(task["payload"].get("node_type") or "") != "approval"
            ):
                return None
            row = conn.execute(
                """INSERT INTO approval_requests
                       (approval_id,run_id,task_id,subject_type,subject,user_id,
                        capability_ref,input_hash,input_preview,risk,data_classification,
                        required_role,requested_by,expires_at)
                   VALUES (%s,%s,%s,'graph_node',%s,%s,'{}'::jsonb,%s,%s,%s,%s,%s,%s,
                           clock_timestamp()+make_interval(secs => %s))
                   ON CONFLICT(approval_id) DO NOTHING RETURNING *""",
                (
                    kwargs["approval_id"],
                    kwargs["run_id"],
                    kwargs["task_id"],
                    Jsonb(kwargs["subject"]),
                    task["user_id"],
                    kwargs["input_hash"],
                    Jsonb(kwargs["input_preview"]),
                    kwargs["risk"],
                    kwargs["data_classification"],
                    kwargs["required_role"],
                    kwargs["requested_by"],
                    kwargs["expires_in_seconds"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM approval_requests WHERE approval_id=%s FOR UPDATE",
                    (kwargs["approval_id"],),
                ).fetchone()
            if (
                row is None
                or str(row["task_id"] or "") != kwargs["task_id"]
                or str(row["input_hash"]) != kwargs["input_hash"]
                or dict(row["subject"]) != kwargs["subject"]
                or str(row["status"]) != "pending"
            ):
                raise RuntimeError("explicit Graph approval identity conflict")
            result = {
                "stop_reason": "waiting_approval",
                "approval_id": kwargs["approval_id"],
                "node_type": "approval",
            }
            saved = conn.execute(
                """UPDATE runtime_tasks SET status='waiting_approval',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s RETURNING task_id""",
                (
                    Jsonb(result),
                    kwargs["task_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if saved is None:
                return None
            self._refresh_graph_run_waiting(conn, kwargs["run_id"])
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.approval.waiting",
                message="Explicit Graph approval gate suspended",
                data={
                    "approval_id": kwargs["approval_id"],
                    "required_role": kwargs["required_role"],
                    "lease_version": kwargs["lease_version"],
                },
            )
            self._notify(conn, kwargs["run_id"])
        return self._approval_request(row)

    def _resolve_explicit_graph_approval(
        self, conn: Any, *, row: dict[str, Any], resolution: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        if resolution == "revoke" or str(row["status"]) != "pending":
            return None
        task = conn.execute(
            "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
            (row["task_id"],),
        ).fetchone()
        if (
            task is None
            or str(task["run_id"]) != kwargs["run_id"]
            or str(task["status"]) != "waiting_approval"
            or str((task["result"] or {}).get("approval_id") or "") != row["approval_id"]
        ):
            return None
        expired = conn.execute(
            "SELECT %s IS NOT NULL AND %s < clock_timestamp() AS expired",
            (row["expires_at"], row["expires_at"]),
        ).fetchone()["expired"]
        status = (
            "expired"
            if expired
            else {
                "approve": "approved",
                "reject": "rejected",
                "request_changes": "changes_requested",
            }[resolution]
        )
        saved = conn.execute(
            """UPDATE approval_requests SET status=%s,resolution=%s,
                   resolution_note=%s,resolved_by=%s,resolved_at=clock_timestamp(),
                   consumed_by=CASE WHEN %s='approved' THEN %s ELSE NULL END,
                   consumed_at=CASE WHEN %s='approved' THEN clock_timestamp() ELSE NULL END,
                   updated_at=clock_timestamp()
               WHERE approval_id=%s RETURNING *""",
            (
                status,
                resolution,
                kwargs.get("note"),
                kwargs["actor_id"],
                status,
                kwargs["actor_id"],
                status,
                row["approval_id"],
            ),
        ).fetchone()
        if saved is None:
            return None
        if status == "approved":
            structured = {
                "approved": True,
                "approval_id": str(row["approval_id"]),
                "resolved_by": kwargs["actor_id"],
                "input_hash": str(row["input_hash"]),
            }
            result = {
                "status": "completed",
                "node_type": "approval",
                "content": json.dumps(structured, ensure_ascii=False, sort_keys=True),
                "structured_output": structured,
                "approval_id": str(row["approval_id"]),
            }
            conn.execute(
                """UPDATE runtime_tasks SET status='completed',result=%s,error=NULL,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='waiting_approval'""",
                (Jsonb(result), row["task_id"]),
            )
            self._queue_graph_dependents(conn, kwargs["run_id"])
        else:
            error = {
                "code": f"approval_{status}",
                "message": kwargs.get("note") or status,
            }
            conn.execute(
                """UPDATE runtime_tasks SET status='failed',error=%s,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='waiting_approval'""",
                (Jsonb(error), row["task_id"]),
            )
        self._refresh_graph_run_waiting(conn, kwargs["run_id"])
        self._audit(
            conn,
            run_id=kwargs["run_id"],
            task_id=str(row["task_id"]),
            stage="store.graph.approval.resolved",
            message="Explicit Graph approval gate resolved",
            data={
                "approval_id": str(row["approval_id"]),
                "status": status,
                "actor_id": kwargs["actor_id"],
            },
        )
        return saved

    def _expire_explicit_graph_approval(
        self, conn: Any, row: dict[str, Any]
    ) -> dict[str, Any] | None:
        updated = conn.execute(
            """UPDATE approval_requests SET status='expired',resolution='expired',
                   resolved_by='system:expiry',resolved_at=clock_timestamp(),
                   updated_at=clock_timestamp()
               WHERE approval_id=%s AND status='pending' RETURNING *""",
            (row["approval_id"],),
        ).fetchone()
        if updated is None:
            return None
        error = {"code": "approval_expired", "message": "approval request expired"}
        conn.execute(
            """UPDATE runtime_tasks SET status='failed',error=%s,
                   finished_at=clock_timestamp(),updated_at=clock_timestamp()
               WHERE task_id=%s AND status='waiting_approval'
                 AND result->>'approval_id'=%s""",
            (Jsonb(error), row["task_id"], row["approval_id"]),
        )
        self._refresh_graph_run_waiting(conn, str(row["run_id"]))
        self._audit(
            conn,
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            stage="store.graph.approval.expired",
            message="Explicit Graph approval gate expired",
            data={"approval_id": str(row["approval_id"])},
        )
        return updated

    @staticmethod
    def _queue_graph_dependents(conn: Any, run_id: str) -> None:
        conn.execute(
            """UPDATE runtime_tasks task SET status='queued',available_at=clock_timestamp(),
                   updated_at=clock_timestamp()
               WHERE task.run_id=%s AND task.status='blocked' AND NOT EXISTS (
                   SELECT 1 FROM runtime_task_dependencies dependency
                   JOIN runtime_tasks parent
                     ON parent.task_id=dependency.depends_on_task_id
                   WHERE dependency.task_id=task.task_id AND parent.status!='completed')""",
            (run_id,),
        )
