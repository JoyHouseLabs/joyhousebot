"""PostgreSQL state machine for automatic, declared Graph Saga compensation."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from porthouse.storage.json_codec import Jsonb

_SAGA_TERMINAL = {"completed", "failed"}
_TASK_FAILURES = {"failed", "timed_out"}


class PostgresGraphSagaStoreMixin:
    def migrate_graph_sagas(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS graph_sagas (
            saga_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            graph_revision_id TEXT NOT NULL REFERENCES graph_revisions(revision_id),
            trigger_task_id TEXT NOT NULL REFERENCES runtime_tasks(task_id),
            trigger_status TEXT NOT NULL,
            status TEXT NOT NULL,
            policy JSONB NOT NULL,
            compensation_total INTEGER NOT NULL DEFAULT 0,
            compensation_completed INTEGER NOT NULL DEFAULT 0,
            error JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_graph_sagas_status
            ON graph_sagas(status, updated_at);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341932,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="graph_sagas",
                version=1,
                ddl=ddl,
                description="automatic declared Graph Saga compensation state",
            )

    def get_runtime_saga(self, run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM graph_sagas WHERE run_id=%s", (run_id,)
            ).fetchone()
        return self._saga(row) if row else None

    def trigger_runtime_saga(self, run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 734921))",
                (run_id,),
            )
            # Runtime Task transitions lock the Task row before refreshing the
            # parent Run.  Keep the same lock order here.  Locking the Run
            # first and every Task second deadlocks with a finishing Worker:
            #
            #   task Worker: Task -> Run
            #   saga scanner: Run  -> Task
            #
            # The initial Run read is only a cheap policy guard.  Its state is
            # re-read under FOR UPDATE after the Task set has been locked.
            run = conn.execute(
                "SELECT * FROM runtime_runs WHERE run_id=%s", (run_id,)
            ).fetchone()
            if run is None or str(run["kind"]) != "graph":
                return None
            policy = dict((run["options"] or {}).get("failure_policy") or {})
            if policy.get("mode") != "saga":
                return None
            existing = conn.execute(
                "SELECT * FROM graph_sagas WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if existing is not None:
                return {**self._saga(existing), "created": False, "activated_task_ids": []}
            tasks = conn.execute(
                "SELECT * FROM runtime_tasks WHERE run_id=%s ORDER BY priority,task_id FOR UPDATE",
                (run_id,),
            ).fetchall()
            run = conn.execute(
                "SELECT * FROM runtime_runs WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if run is None or str(run["kind"]) != "graph":
                return None
            policy = dict((run["options"] or {}).get("failure_policy") or {})
            if policy.get("mode") != "saga" or str(run["status"]) in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }:
                return None
            existing = conn.execute(
                "SELECT * FROM graph_sagas WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if existing is not None:
                return {**self._saga(existing), "created": False, "activated_task_ids": []}
            trigger = next(
                (
                    task
                    for task in tasks
                    if not bool(task["payload"].get("saga_managed"))
                    and str(task["status"]) in _TASK_FAILURES
                ),
                None,
            )
            if trigger is None:
                return None
            saga_id = "saga_" + sha256(
                f"{run_id}:{run['graph_revision_id']}:{trigger['task_id']}".encode()
            ).hexdigest()
            by_id = {str(task["task_id"]): task for task in tasks}
            eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
            dormant = [task for task in tasks if bool(task["payload"].get("saga_managed"))]
            for compensation in dormant:
                source = str(compensation["payload"]["compensation"]["source"])
                source_id = f"{run_id}:{source.removeprefix('tasks.')}"
                source_task = by_id.get(source_id)
                if source_task is not None and str(source_task["status"]) == "completed":
                    eligible.append((compensation, source_task))
            eligible.sort(key=lambda item: (-int(item[1]["priority"]), str(item[0]["task_id"])))
            activated = [str(item[0]["task_id"]) for item in eligible]
            terminal_status = "running" if activated else "completed"
            row = conn.execute(
                """INSERT INTO graph_sagas
                       (saga_id,run_id,graph_revision_id,trigger_task_id,trigger_status,
                        status,policy,compensation_total,finished_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s='completed' THEN clock_timestamp() END)
                   RETURNING *""",
                (
                    saga_id,
                    run_id,
                    run["graph_revision_id"],
                    trigger["task_id"],
                    trigger["status"],
                    terminal_status,
                    Jsonb(policy),
                    len(activated),
                    terminal_status,
                ),
            ).fetchone()
            conn.execute(
                """UPDATE runtime_tasks SET status='skipped',
                       result='{"stop_reason":"saga_aborted"}'::jsonb,
                       error='{"message":"task skipped after Saga trigger"}'::jsonb,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status IN ('queued','blocked')
                     AND COALESCE((payload->>'saga_managed')::boolean,FALSE)=FALSE""",
                (run_id,),
            )
            conn.execute(
                """UPDATE runtime_tasks SET status='skipped',
                       result='{"stop_reason":"saga_not_required"}'::jsonb,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE run_id=%s AND status='dormant'
                     AND NOT (task_id=ANY(%s))""",
                (run_id, activated),
            )
            previous: str | None = None
            for order, (compensation, _source) in enumerate(eligible, start=1):
                task_id = str(compensation["task_id"])
                status = "queued" if previous is None else "blocked"
                conn.execute(
                    """UPDATE runtime_tasks SET status=%s,
                           payload=payload || %s,result=%s,error=NULL,
                           available_at=clock_timestamp(),finished_at=NULL,
                           updated_at=clock_timestamp()
                       WHERE task_id=%s AND status='dormant'""",
                    (
                        status,
                        Jsonb({"saga_id": saga_id, "saga_order": order}),
                        Jsonb(
                            {
                                "stop_reason": "saga_triggered",
                                "saga_id": saga_id,
                                "saga_order": order,
                            }
                        ),
                        task_id,
                    ),
                )
                if previous is not None:
                    conn.execute(
                        """INSERT INTO runtime_task_dependencies(task_id,depends_on_task_id)
                           VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                        (task_id, previous),
                    )
                previous = task_id
            conn.execute(
                """UPDATE runtime_runs SET status='running',current_phase='compensating',
                       status_summary=%s,status_reason='automatic Saga triggered',
                       next_action=%s,waiting_on=NULL,updated_at=clock_timestamp()
                   WHERE run_id=%s AND status NOT IN
                       ('completed','failed','cancelled','timed_out')""",
                (
                    "正在执行补偿" if activated else "无需补偿，准备结束",
                    "execute declared compensations" if activated else "finalize failed run",
                    run_id,
                ),
            )
            self._audit(
                conn,
                run_id=run_id,
                task_id=str(trigger["task_id"]),
                stage="store.graph.saga.started",
                message="Automatic declared Saga triggered",
                data={
                    "saga_id": saga_id,
                    "trigger_task_id": str(trigger["task_id"]),
                    "activated_task_ids": activated,
                },
            )
            self._notify(conn, run_id)
            assert row is not None
            return {**self._saga(row), "created": True, "activated_task_ids": activated}

    def reconcile_runtime_saga(self, run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 734921))",
                (run_id,),
            )
            row = conn.execute(
                "SELECT * FROM graph_sagas WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if row is None or str(row["status"]) in _SAGA_TERMINAL:
                return self._saga(row) if row else None
            tasks = conn.execute(
                """SELECT * FROM runtime_tasks WHERE run_id=%s
                   AND payload->>'saga_id'=%s ORDER BY (payload->>'saga_order')::int
                   FOR UPDATE""",
                (run_id, row["saga_id"]),
            ).fetchall()
            completed = sum(str(task["status"]) == "completed" for task in tasks)
            failures = [
                task
                for task in tasks
                if str(task["status"]) in {"failed", "cancelled", "timed_out", "skipped"}
            ]
            status = (
                "failed"
                if failures
                else "completed"
                if completed == int(row["compensation_total"])
                else "running"
            )
            if status == "failed":
                conn.execute(
                    """UPDATE runtime_tasks SET status='skipped',
                           result='{"stop_reason":"saga_compensation_aborted"}'::jsonb,
                           error='{"message":"earlier compensation failed"}'::jsonb,
                           finished_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE run_id=%s AND payload->>'saga_id'=%s
                         AND status IN ('queued','blocked')""",
                    (run_id, row["saga_id"]),
                )
            error = (
                {
                    "message": "declared Saga compensation failed",
                    "task_id": str(failures[0]["task_id"]),
                }
                if failures
                else None
            )
            if status != "running":
                row = conn.execute(
                    """UPDATE graph_sagas SET status=%s,compensation_completed=%s,error=%s,
                           finished_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE saga_id=%s RETURNING *""",
                    (status, completed, Jsonb(error) if error else None, row["saga_id"]),
                ).fetchone()
                self._audit(
                    conn,
                    run_id=run_id,
                    stage=f"store.graph.saga.{status}",
                    message=f"Automatic Saga {status}",
                    level="error" if status == "failed" else "info",
                    data={
                        "saga_id": str(row["saga_id"]),
                        "compensation_completed": completed,
                    },
                )
                self._notify(conn, run_id)
            return self._saga(row)

    @staticmethod
    def _saga(row: Any) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        return {
            "saga_id": str(row["saga_id"]),
            "run_id": str(row["run_id"]),
            "graph_revision_id": str(row["graph_revision_id"]),
            "trigger_task_id": str(row["trigger_task_id"]),
            "trigger_status": str(row["trigger_status"]),
            "status": str(row["status"]),
            "policy": dict(row["policy"] or {}),
            "compensation_total": int(row["compensation_total"]),
            "compensation_completed": int(row["compensation_completed"]),
            "error": dict(row["error"] or {}) or None,
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
        }
