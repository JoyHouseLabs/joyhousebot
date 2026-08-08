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
            self._audit(
                conn,
                run_id=run_id,
                stage="store.run.cancel_requested",
                message="Cancel requested",
                data={"reason": reason, "status": row["status"]},
            )
            self._notify(conn, run_id)
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
