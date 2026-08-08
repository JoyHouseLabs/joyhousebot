"""Task-lease-fenced suspension and recovery for Graph capability Actions."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb

_WAITING = ("waiting_approval", "waiting_external")


class PostgresGraphActionStoreMixin:
    def suspend_graph_task_for_approval(self, **kwargs: Any) -> bool:
        result = {
            "stop_reason": "waiting_approval",
            "approval_id": kwargs["approval_id"],
            "action_id": kwargs["action_id"],
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_tasks AS task
                   SET status='waiting_approval',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   FROM action_intents AS action, approval_requests AS approval,
                        runtime_runs AS run
                   WHERE task.task_id=%s AND task.run_id=%s AND task.status='running'
                     AND task.lease_owner=%s AND task.lease_version=%s
                     AND action.action_id=%s AND action.task_id=task.task_id
                     AND action.run_id=task.run_id
                     AND approval.approval_id=%s AND approval.action_id=action.action_id
                     AND approval.status='pending'
                     AND run.run_id=task.run_id AND run.kind='graph'
                   RETURNING task.run_id""",
                (
                    Jsonb(result),
                    kwargs["task_id"],
                    kwargs["run_id"],
                    kwargs["worker_id"],
                    kwargs["task_lease_version"],
                    kwargs["action_id"],
                    kwargs["approval_id"],
                ),
            ).fetchone()
            if row is not None:
                self._refresh_graph_run_waiting(conn, str(row["run_id"]))
                self._notify(conn, str(row["run_id"]))
        return row is not None

    def suspend_graph_task_for_reconciliation(self, **kwargs: Any) -> bool:
        result = {
            "stop_reason": "waiting_external",
            "action_id": kwargs["action_id"],
            "invocation_id": kwargs["invocation_id"],
            "reconciliation_id": kwargs["reconciliation_id"],
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_tasks AS task
                   SET status='waiting_external',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   FROM action_intents AS action, operation_reconciliations AS rec,
                        runtime_runs AS run
                   WHERE task.task_id=%s AND task.run_id=%s AND task.status='running'
                     AND task.lease_owner=%s AND task.lease_version=%s
                     AND action.action_id=%s AND action.task_id=task.task_id
                     AND action.run_id=task.run_id
                     AND rec.reconciliation_id=%s AND rec.action_id=action.action_id
                     AND rec.status IN ('pending','checking','manual_required')
                     AND run.run_id=task.run_id AND run.kind='graph'
                   RETURNING task.run_id""",
                (
                    Jsonb(result),
                    kwargs["task_id"],
                    kwargs["run_id"],
                    kwargs["worker_id"],
                    kwargs["task_lease_version"],
                    kwargs["action_id"],
                    kwargs["reconciliation_id"],
                ),
            ).fetchone()
            if row is not None:
                self._refresh_graph_run_waiting(conn, str(row["run_id"]))
                self._notify(conn, str(row["run_id"]))
        return row is not None

    @staticmethod
    def _graph_action_task(conn: Any, action_id: str) -> dict[str, Any] | None:
        return conn.execute(
            """SELECT task.task_id,task.run_id,task.status,task.result
               FROM action_intents AS action
               JOIN runtime_tasks AS task ON task.task_id=action.task_id
               JOIN runtime_runs AS run ON run.run_id=task.run_id AND run.kind='graph'
               WHERE action.action_id=%s""",
            (action_id,),
        ).fetchone()

    def _resume_graph_action_task(
        self,
        conn: Any,
        *,
        action_id: str,
        waiting_status: str,
    ) -> bool:
        task = self._graph_action_task(conn, action_id)
        if task is None:
            return False
        conn.execute(
            """UPDATE runtime_tasks SET status='queued',error=NULL,
                   available_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL,
                   finished_at=NULL,updated_at=clock_timestamp()
               WHERE task_id=%s AND status=%s""",
            (task["task_id"], waiting_status),
        )
        self._refresh_graph_run_waiting(conn, str(task["run_id"]))
        return True

    def _fail_graph_action_task(
        self,
        conn: Any,
        *,
        action_id: str,
        waiting_status: str,
        error: dict[str, Any],
    ) -> bool:
        task = self._graph_action_task(conn, action_id)
        if task is None:
            return False
        conn.execute(
            """UPDATE runtime_tasks SET status='failed',error=%s,
                   lease_owner=NULL,lease_expires_at=NULL,
                   finished_at=clock_timestamp(),updated_at=clock_timestamp()
               WHERE task_id=%s AND status=%s""",
            (Jsonb(error), task["task_id"], waiting_status),
        )
        self._refresh_graph_run_waiting(conn, str(task["run_id"]))
        return True

    def _refresh_graph_run_waiting(self, conn: Any, run_id: str) -> None:
        waiting = conn.execute(
            """SELECT status,result FROM runtime_tasks
               WHERE run_id=%s AND status IN ('waiting_approval','waiting_external')
               ORDER BY CASE status WHEN 'waiting_approval' THEN 0 ELSE 1 END,
                        priority,created_at LIMIT 1""",
            (run_id,),
        ).fetchone()
        if waiting is None:
            conn.execute(
                """UPDATE runtime_runs SET status='running',result=NULL,error=NULL,
                       current_phase='execution',status_summary='任务图继续执行',
                       status_reason='graph Action resumed',next_action='dispatch ready tasks',
                       waiting_on=NULL,finished_at=NULL,updated_at=clock_timestamp()
                   WHERE run_id=%s AND kind='graph'
                     AND status IN ('waiting_approval','waiting_external')""",
                (run_id,),
            )
            return
        result = dict(waiting["result"] or {})
        status = str(waiting["status"])
        waiting_on = (
            result.get("approval_id")
            if status == "waiting_approval"
            else result.get("reconciliation_id") or result.get("wait_id")
        )
        waits_for_event = status == "waiting_external" and bool(result.get("wait_id"))
        conn.execute(
            """UPDATE runtime_runs SET status=%s,result=%s,error=NULL,
                   current_phase='waiting',status_summary=%s,status_reason=%s,
                   next_action=%s,waiting_on=%s,lease_owner=NULL,
                   lease_expires_at=NULL,finished_at=NULL,updated_at=clock_timestamp()
               WHERE run_id=%s AND kind='graph'
                 AND status NOT IN ('completed','failed','cancelled','timed_out')""",
            (
                status,
                Jsonb(result),
                (
                    "等待操作审批"
                    if status == "waiting_approval"
                    else "等待外部事件"
                    if waits_for_event
                    else "等待外部操作确认"
                ),
                (
                    "graph Task is waiting for an external event"
                    if waits_for_event
                    else "graph capability Action is suspended"
                ),
                (
                    "review approval request"
                    if status == "waiting_approval"
                    else "deliver external event"
                    if waits_for_event
                    else "reconcile external operation"
                ),
                waiting_on,
                run_id,
            ),
        )

    def refresh_runtime_graph_wait_state(self, run_id: str) -> None:
        with self._pool.connection() as conn, conn.transaction():
            self._refresh_graph_run_waiting(conn, run_id)
            self._notify(conn, run_id)
