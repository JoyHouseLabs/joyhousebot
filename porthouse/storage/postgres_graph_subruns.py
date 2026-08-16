"""Durable suspension of Graph Tasks while frozen child Runs execute."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb


class PostgresGraphSubrunStoreMixin:
    def suspend_graph_task_for_subrun(self, **kwargs: Any) -> bool:
        result = {
            "status": "waiting_external",
            "stop_reason": "subrun_waiting",
            "child_run_id": kwargs["child_run_id"],
            "subrun_mode": kwargs["subrun_mode"],
        }
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_tasks AS task
                   SET status='waiting_external',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   FROM runtime_runs AS parent,runtime_runs AS child
                   WHERE task.task_id=%s AND task.run_id=%s AND task.status='running'
                     AND task.lease_owner=%s AND task.lease_version=%s
                     AND task.node_type='subrun'
                     AND parent.run_id=task.run_id AND parent.kind='graph'
                     AND child.run_id=%s AND child.user_id=parent.user_id
                     AND child.root_run_id=parent.root_run_id
                     AND child.parent_run_id=parent.run_id
                     AND child.parent_task_id=task.task_id
                   RETURNING task.run_id""",
                (
                    Jsonb(result),
                    kwargs["task_id"],
                    kwargs["run_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                    kwargs["child_run_id"],
                ),
            ).fetchone()
            if row is None:
                return False
            self._refresh_graph_run_waiting(conn, str(row["run_id"]))
            self._audit(
                conn,
                run_id=str(row["run_id"]),
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.subrun.waiting",
                message="Graph Task suspended for a frozen child Run",
                data={
                    "child_run_id": kwargs["child_run_id"],
                    "subrun_mode": kwargs["subrun_mode"],
                },
            )
            self._notify(conn, str(row["run_id"]))
            return True
