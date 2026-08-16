"""Lease-fenced atomic completion of deterministic Graph branch nodes."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb


class PostgresGraphBranchStoreMixin:
    def complete_runtime_branch(self, **kwargs: Any) -> tuple[bool, list[str]]:
        all_targets = list(dict.fromkeys(kwargs.get("all_target_ids") or []))
        selected = set(kwargs.get("selected_target_ids") or [])
        if not selected <= set(all_targets):
            raise ValueError("selected branch targets are not frozen Graph targets")
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
                (kwargs["task_id"],),
            ).fetchone()
            if (
                task is None
                or str(task["run_id"]) != kwargs["run_id"]
                or str(task["status"]) != "running"
                or str(task["lease_owner"] or "") != kwargs["worker_id"]
                or int(task["lease_version"]) != int(kwargs["lease_version"])
            ):
                return False, []
            direct_targets = {
                str(row["task_id"])
                for row in conn.execute(
                    """SELECT task_id FROM runtime_task_dependencies
                       WHERE depends_on_task_id=%s""",
                    (kwargs["task_id"],),
                ).fetchall()
            }
            if direct_targets != set(all_targets):
                raise RuntimeError("frozen branch target Task set changed")
            target_rows = []
            if all_targets:
                target_rows = conn.execute(
                    """SELECT task_id,status FROM runtime_tasks
                       WHERE run_id=%s AND task_id=ANY(%s) FOR UPDATE""",
                    (kwargs["run_id"], all_targets),
                ).fetchall()
                if {str(row["task_id"]) for row in target_rows} != set(all_targets):
                    raise RuntimeError("frozen branch target Task set changed")
                invalid = [
                    str(row["task_id"])
                    for row in target_rows
                    if str(row["status"]) not in {"blocked", "queued", "skipped"}
                ]
                if invalid:
                    raise RuntimeError(f"branch targets already started: {sorted(invalid)}")
            saved = conn.execute(
                """UPDATE runtime_tasks SET status='completed',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s RETURNING task_id""",
                (
                    Jsonb(kwargs["result"]),
                    kwargs["task_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if saved is None:
                return False, []
            unselected = sorted(set(all_targets) - selected)
            skipped: list[str] = []
            if unselected:
                skipped = [
                    str(row["task_id"])
                    for row in conn.execute(
                        """UPDATE runtime_tasks SET status='skipped',result=%s,error=NULL,
                               lease_owner=NULL,lease_expires_at=NULL,
                               finished_at=clock_timestamp(),updated_at=clock_timestamp()
                           WHERE run_id=%s AND task_id=ANY(%s)
                             AND status IN ('blocked','queued') RETURNING task_id""",
                        (
                            Jsonb(
                                {
                                    "status": "skipped",
                                    "stop_reason": "branch_not_selected",
                                    "branch_task_id": kwargs["task_id"],
                                }
                            ),
                            kwargs["run_id"],
                            unselected,
                        ),
                    ).fetchall()
                ]
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.branch.completed",
                message="Graph branch evaluated and unselected targets skipped atomically",
                data={
                    "selected_target_ids": sorted(selected),
                    "skipped_target_ids": skipped,
                    "lease_version": kwargs["lease_version"],
                },
            )
            self._notify(conn, kwargs["run_id"])
        return True, skipped
