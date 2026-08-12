"""Cancel-request and reset persistence for durable runtime runs."""

from __future__ import annotations

from typing import Any


class PostgresRunCancelMixin:
    def request_runtime_cancel(self, run_id: str, *, reason: str) -> dict[str, Any] | None:
        """Record a durable cancel request on a non-terminal run.

        The flag is the first phase of a two-phase cancel: a live owning
        worker observes it on its next heartbeat and commits the fenced
        terminal state itself; once the lease is dead the recovery sweep may
        finish the run directly.  Returns the observed status and whether a
        live lease is held, or None when the run is missing or terminal.
        """
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_runs SET
                       cancel_requested_at=COALESCE(cancel_requested_at, clock_timestamp()),
                       cancel_reason=COALESCE(cancel_reason, %s),
                       updated_at=clock_timestamp()
                   WHERE run_id=%s
                     AND status NOT IN ('completed','failed','cancelled','timed_out')
                   RETURNING status,
                       (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL
                        AND lease_expires_at >= clock_timestamp()) AS lease_alive""",
                (reason, run_id),
            ).fetchone()
            if row is None:
                return None
            descendants = conn.execute(
                """WITH RECURSIVE child_runs AS (
                       SELECT run_id FROM runtime_runs WHERE parent_run_id=%s
                       UNION ALL
                       SELECT child.run_id
                       FROM runtime_runs AS child
                       JOIN child_runs AS parent ON child.parent_run_id=parent.run_id
                   )
                   UPDATE runtime_runs AS child SET
                       cancel_requested_at=COALESCE(
                           child.cancel_requested_at,clock_timestamp()
                       ),
                       cancel_reason=COALESCE(child.cancel_reason,%s),
                       updated_at=clock_timestamp()
                   WHERE child.run_id IN (SELECT run_id FROM child_runs)
                     AND child.status NOT IN (
                         'completed','failed','cancelled','timed_out'
                     )
                   RETURNING child.run_id,child.status""",
                (run_id, f"parent Run cancelled: {reason}"),
            ).fetchall()
            self._audit(
                conn,
                run_id=run_id,
                stage="store.run.cancel_requested",
                message="Cancel requested",
                data={
                    "reason": reason,
                    "status": row["status"],
                    "propagated_child_run_ids": [
                        str(item["run_id"]) for item in descendants
                    ],
                },
            )
            self._notify(conn, run_id)
            for child in descendants:
                self._notify(conn, str(child["run_id"]))
        return {"status": str(row["status"]), "lease_alive": bool(row["lease_alive"])}

    def reset_runtime_run(self, run_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                """UPDATE runtime_runs SET status='queued', result=NULL, error=NULL,
                       started_at=NULL, finished_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                       cancel_requested_at=NULL, cancel_reason=NULL,
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status IN ('failed','cancelled','timed_out')""",
                (run_id,),
            )
            if cur.rowcount:
                self._notify(conn, run_id)
            return cur.rowcount == 1

    def reset_runtime_graph(self, run_id: str) -> bool:
        """Resume a Graph without exposing a queued Run with stale terminal Tasks."""
        with self._pool.connection() as conn, conn.transaction():
            resumed = conn.execute(
                """UPDATE runtime_runs SET status='queued', result=NULL, error=NULL,
                       started_at=NULL, finished_at=NULL, lease_owner=NULL,
                       lease_expires_at=NULL, cancel_requested_at=NULL,
                       cancel_reason=NULL, updated_at=clock_timestamp()
                   WHERE run_id=%s AND kind='graph'
                     AND status IN ('failed','cancelled','timed_out')
                   RETURNING run_id""",
                (run_id,),
            ).fetchone()
            if resumed is None:
                return False
            conn.execute(
                """UPDATE runtime_tasks t SET status=CASE WHEN EXISTS(
                           SELECT 1 FROM runtime_task_dependencies d
                           WHERE d.task_id=t.task_id
                       ) THEN 'blocked' ELSE 'queued' END,
                       result=CASE
                           WHEN t.payload->>'node_type' IN ('foreach','bounded_loop')
                                AND EXISTS(SELECT 1 FROM runtime_tasks child
                                    WHERE child.parent_task_id=t.task_id)
                           THEN t.result ELSE NULL END,
                       error=NULL,attempt=0,available_at=clock_timestamp(),
                       lease_owner=NULL,lease_expires_at=NULL,started_at=NULL,
                       finished_at=NULL,updated_at=clock_timestamp()
                   WHERE run_id=%s AND status!='completed'""",
                (run_id,),
            )
            self._audit(
                conn,
                run_id=run_id,
                stage="store.graph.resumed",
                message="Graph Run and incomplete Tasks reset atomically",
            )
            self._notify(conn, run_id)
            return True
