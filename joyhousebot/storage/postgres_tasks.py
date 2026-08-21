"""PostgreSQL runtime events, task graph, and task leases."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from joyhousebot.contracts.events import AgentEvent
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_event_writes import append_runtime_event_in_transaction
from joyhousebot.storage.postgres_task_claiming import lock_claimable_task_run
from joyhousebot.storage.runtime_store import (
    RuntimeTaskRecord,
)

_CHANNEL = "joyhousebot_runtime_work"
_TERMINAL = ("completed", "failed", "cancelled", "timed_out")
_TASK_TERMINAL = (*_TERMINAL, "skipped")
# Lease-expiry sweeps are cluster-wide UPDATEs.  They only matter once a lease
# actually dies, so each worker runs them at most this often instead of on
# every claim attempt.
_LEASE_SWEEP_INTERVAL_SECONDS = 1.0


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresTaskStoreMixin:
    def append_runtime_event(self, event: AgentEvent) -> AgentEvent:
        with self._pool.connection() as conn, conn.transaction():
            persisted = append_runtime_event_in_transaction(conn, event)
            self._notify(conn, event.run_id)
        return persisted

    def list_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
        user_id: str | None = None,
    ) -> list[AgentEvent]:
        clauses = ["run_id=%s", "sequence>%s"]
        params: list[Any] = [run_id, max(0, after_sequence)]
        if user_id is not None:
            clauses.append("user_id=%s")
            params.append(user_id)
        params.append(max(1, min(5000, limit)))
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT sequence,event_id,run_id,task_id,root_run_id,parent_run_id,
                          parent_task_id,user_id,session_id,agent_id,turn_id,span_id,
                          parent_span_id,tool_call_id,attempt,phase,status,visibility,
                          summary,worker_id,lease_version,schema_version,event_type,data,created_at
                   FROM runtime_events WHERE {" AND ".join(clauses)}
                   ORDER BY sequence LIMIT %s""",
                params,
            ).fetchall()
        return [
            AgentEvent(
                sequence=int(r["sequence"]),
                event_id=str(r["event_id"]),
                run_id=str(r["run_id"]),
                task_id=r["task_id"],
                root_run_id=r["root_run_id"],
                parent_run_id=r["parent_run_id"],
                parent_task_id=r["parent_task_id"],
                user_id=r["user_id"],
                session_id=r["session_id"],
                agent_id=r["agent_id"],
                turn_id=r["turn_id"],
                span_id=r["span_id"],
                parent_span_id=r["parent_span_id"],
                tool_call_id=r["tool_call_id"],
                attempt=r["attempt"],
                phase=r["phase"],
                status=r["status"],
                visibility=r["visibility"],
                summary=r["summary"],
                worker_id=r["worker_id"],
                lease_version=r["lease_version"],
                schema_version=int(r["schema_version"] or 2),
                type=str(r["event_type"]),
                data=dict(_json(r["data"], {})),
                created_at=_iso(r["created_at"]) or "",
            )
            for r in rows
        ]

    def create_runtime_task(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str = "default",
        name: str,
        payload: dict[str, Any],
        dependencies: list[str] | None = None,
        parent_task_id: str | None = None,
        priority: int = 100,
        max_attempts: int = 1,
    ) -> RuntimeTaskRecord:
        deps = list(dict.fromkeys(dependencies or []))
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO runtime_tasks
                       (task_id,run_id,agent_id,parent_task_id,name,status,payload,priority,max_attempts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    task_id,
                    run_id,
                    agent_id,
                    parent_task_id,
                    name,
                    "blocked" if deps else "queued",
                    Jsonb(payload),
                    priority,
                    max(1, max_attempts),
                ),
            ).fetchone()
            if deps:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO runtime_task_dependencies(task_id,depends_on_task_id) VALUES (%s,%s)",
                        [(task_id, dep) for dep in deps],
                    )
            self._notify(conn, run_id)
        assert row is not None
        return self._task(row)

    def get_runtime_task(self, task_id: str) -> RuntimeTaskRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s", (task_id,)
            ).fetchone()
        return self._task(row) if row else None

    def list_runtime_tasks(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeTaskRecord]:
        clauses, params = [], []
        if run_id:
            clauses.append("t.run_id=%s")
            params.append(run_id)
        if status:
            clauses.append("t.status=%s")
            params.append(status)
        if user_id:
            clauses.append("r.user_id=%s")
            params.append(user_id)
        query = "SELECT t.* FROM runtime_tasks t JOIN runtime_runs r ON r.run_id=t.run_id"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY t.priority,t.created_at LIMIT %s"
        params.append(max(1, min(5000, limit)))
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._task(row) for row in rows]

    def get_runtime_task_dependencies(self, task_id: str) -> list[str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT depends_on_task_id FROM runtime_task_dependencies WHERE task_id=%s",
                (task_id,),
            ).fetchall()
        return [str(row["depends_on_task_id"]) for row in rows]

    def update_runtime_task(
        self,
        task_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        retry_delay_seconds: float | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
        event: AgentEvent | None = None,
        workspace_entry: dict[str, Any] | None = None,
    ) -> bool:
        terminal = status in _TASK_TERMINAL
        delay = max(0.0, retry_delay_seconds or 0.0)
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_tasks SET status=%s, result=COALESCE(%s,result), error=%s,
                       available_at=CASE WHEN %s='queued' THEN clock_timestamp()+(%s*interval '1 second') ELSE available_at END,
                       lease_owner=CASE WHEN %s IN ('queued','waiting_approval','waiting_external','completed','failed','cancelled','timed_out','skipped') THEN NULL ELSE lease_owner END,
                       lease_expires_at=CASE WHEN %s IN ('queued','waiting_approval','waiting_external','completed','failed','cancelled','timed_out','skipped') THEN NULL ELSE lease_expires_at END,
                       finished_at=CASE WHEN %s THEN clock_timestamp() WHEN %s IN ('queued','blocked','running','waiting_approval','waiting_external') THEN NULL ELSE finished_at END,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND (%s::text IS NULL OR lease_owner=%s)
                     AND (%s::bigint IS NULL OR lease_version=%s)
                   RETURNING run_id""",
                (
                    status,
                    Jsonb(result) if result is not None else None,
                    Jsonb(error) if error is not None else None,
                    status,
                    delay,
                    status,
                    status,
                    terminal,
                    status,
                    task_id,
                    worker_id,
                    worker_id,
                    lease_version,
                    lease_version,
                ),
            ).fetchone()
            if row and status == "completed":
                conn.execute(
                    """UPDATE runtime_tasks t SET status='queued',updated_at=clock_timestamp()
                       WHERE t.status='blocked' AND NOT EXISTS (
                           SELECT 1 FROM runtime_task_dependencies d
                           JOIN runtime_tasks dep ON dep.task_id=d.depends_on_task_id
                           WHERE d.task_id=t.task_id AND dep.status!='completed')"""
                )
            if row:
                if workspace_entry is not None:
                    append_workspace = getattr(
                        self, "_append_team_workspace_entry_tx", None
                    )
                    if append_workspace is None:
                        raise RuntimeError("AgentTeam Workspace storage is unavailable")
                    append_workspace(conn, **workspace_entry)
                if event is not None:
                    if event.run_id != str(row["run_id"]) or event.task_id != task_id:
                        raise ValueError("task transition event identity mismatch")
                    append_runtime_event_in_transaction(conn, event)
                refresh = getattr(self, "_refresh_graph_run_waiting", None)
                if refresh is not None and status not in {"waiting_approval", "waiting_external"}:
                    refresh(conn, str(row["run_id"]))
                self._audit(
                    conn,
                    run_id=str(row["run_id"]),
                    task_id=task_id,
                    worker_id=worker_id,
                    stage="store.task.transition",
                    message=f"Task transitioned to {status}",
                    level="error" if status == "failed" else "info",
                    data={"status": status, "lease_version": lease_version},
                )
                self._notify(conn, str(row["run_id"]))
            return row is not None

    def claim_runtime_task(
        self, *, worker_id: str, lease_seconds: int = 60, run_id: str | None = None
    ) -> RuntimeTaskRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            sweep_now = time.monotonic()
            if sweep_now - getattr(self, "_lease_sweep_at", 0.0) >= _LEASE_SWEEP_INTERVAL_SECONDS:
                self._lease_sweep_at = sweep_now
                conn.execute(
                    """UPDATE runtime_tasks task SET status='queued',
                           result=CASE WHEN EXISTS (
                               SELECT 1 FROM runtime_turns turn
                               WHERE turn.task_id=task.task_id
                           ) OR EXISTS (
                               SELECT 1 FROM action_intents action
                               WHERE action.task_id=task.task_id
                                 AND action.status IN
                                     ('proposed','approval_pending','invoking','waiting_external','observed')
                           ) OR task.node_type IN
                                ('branch','foreach','wait_event','approval','verify','compensation',
                                 'bounded_loop','aggregate','subrun')
                           THEN CASE
                               WHEN task.wait_reason IN
                                    ('foreach_expanded','bounded_loop_waiting')
                               THEN task.result
                               ELSE COALESCE(task.result,'{}'::jsonb)
                                    || '{"stop_reason":"durable_recovery"}'::jsonb
                               END
                               ELSE task.result END,
                           lease_owner=NULL,lease_expires_at=NULL,
                           updated_at=clock_timestamp()
                       WHERE status='running' AND lease_expires_at<clock_timestamp()
                         AND (attempt<max_attempts OR EXISTS (
                               SELECT 1 FROM runtime_turns turn
                               WHERE turn.task_id=task.task_id
                             ) OR EXISTS (
                               SELECT 1 FROM action_intents action
                               WHERE action.task_id=task.task_id
                                 AND action.status IN
                                     ('proposed','approval_pending','invoking','waiting_external','observed')
                             ) OR task.node_type IN
                                  ('branch','foreach','wait_event','approval','verify','compensation',
                                   'bounded_loop','aggregate','subrun'))"""
                )
                conn.execute(
                    """UPDATE runtime_tasks task SET status='failed',lease_owner=NULL,lease_expires_at=NULL,
                           error='{"message":"task lease expired after maximum attempts"}'::jsonb,
                           finished_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE status='running' AND lease_expires_at<clock_timestamp()
                         AND attempt>=max_attempts
                         AND NOT EXISTS (
                           SELECT 1 FROM runtime_turns turn
                           WHERE turn.task_id=task.task_id
                         )
                         AND NOT EXISTS (
                           SELECT 1 FROM action_intents action
                           WHERE action.task_id=task.task_id
                             AND action.status IN
                                 ('proposed','approval_pending','invoking','waiting_external','observed')
                         )
                         AND task.node_type NOT IN
                             ('branch','foreach','wait_event','approval','verify','compensation',
                              'bounded_loop','aggregate','subrun')"""
                )
            selected_run_id = lock_claimable_task_run(conn, run_id)
            if selected_run_id is None:
                return None
            row = conn.execute(
                """WITH candidate AS (
                       SELECT t.task_id FROM runtime_tasks t
                       JOIN runtime_runs r ON r.run_id=t.run_id
                       WHERE (
                           (t.status='queued' AND t.available_at<=clock_timestamp())
                           OR (
                             t.status='waiting_external' AND EXISTS (
                               SELECT 1 FROM action_intents action
                               JOIN operation_reconciliations rec
                                 ON rec.action_id=action.action_id
                               WHERE action.task_id=t.task_id
                                 AND ((rec.status='pending'
                                       AND rec.next_attempt_at<=clock_timestamp())
                                      OR (rec.status='checking'
                                          AND rec.lease_expires_at<clock_timestamp()))
                             )
                           )
                           OR (
                             t.status='waiting_external'
                             AND t.node_type='subrun'
                             AND EXISTS (
                               SELECT 1 FROM runtime_runs child
                               WHERE child.parent_task_id=t.task_id
                                 AND child.parent_run_id=r.run_id
                                 AND child.status IN
                                     ('completed','failed','cancelled','timed_out')
                             )
                           )
                         )
                         AND (
                           t.attempt<t.max_attempts
                           OR t.status='waiting_external'
                           OR COALESCE(t.wait_reason,'')='waiting_approval'
                           OR COALESCE(t.wait_reason,'')='durable_recovery'
                           OR COALESCE(t.wait_reason,'')='foreach_expanded'
                           OR COALESCE(t.wait_reason,'')='bounded_loop_waiting'
                           OR COALESCE(t.wait_reason,'')='subrun_waiting'
                         )
                         AND t.run_id=%s
                         AND (
                           (t.status='queued' AND r.status IN ('queued','running'))
                           OR (t.status='waiting_external'
                               AND r.status IN ('running','waiting_external'))
                         )
                         AND (
                           r.parent_run_id IS NOT NULL OR NOT EXISTS (
                             SELECT 1 FROM runtime_runs earlier
                             WHERE earlier.user_id=r.user_id
                               AND earlier.session_id=r.session_id
                               AND earlier.agent_id=r.agent_id
                               AND earlier.run_id<>r.run_id
                               AND earlier.parent_run_id IS NULL
                               AND (
                                 earlier.status='running'
                                 OR (
                                   earlier.status IN ('queued','planning')
                                   AND (earlier.created_at,earlier.run_id)
                                       < (r.created_at,r.run_id)
                                 )
                               )
                           )
                         )
                         AND (
                           r.initial_events_required = FALSE
                           OR EXISTS (
                             SELECT 1 FROM runtime_events ready
                             WHERE ready.run_id=r.run_id
                               AND ready.event_type='run.queued'
                           )
                         )
                         AND (SELECT count(*) FROM runtime_tasks active
                              WHERE active.run_id=t.run_id AND active.status='running')
                             < r.max_concurrent
                         AND (
                           t.parent_task_id IS NULL OR
                           (SELECT count(*) FROM runtime_tasks sibling
                            WHERE sibling.parent_task_id=t.parent_task_id
                              AND sibling.status='running')
                           < t.child_concurrency_limit
                         )
                       ORDER BY t.priority,t.created_at FOR UPDATE OF r,t SKIP LOCKED LIMIT 1
                   )
                   UPDATE runtime_tasks t SET status='running',lease_owner=%s,
                       lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       lease_version=t.lease_version+1,
                       attempt=t.attempt+CASE
                           WHEN t.status='waiting_external'
                             OR COALESCE(t.wait_reason,'') IN
                                ('waiting_approval','durable_recovery','foreach_expanded',
                                 'bounded_loop_waiting','subrun_waiting')
                           THEN 0 ELSE 1 END,
                       started_at=COALESCE(t.started_at,clock_timestamp()),updated_at=clock_timestamp()
                   FROM candidate c WHERE t.task_id=c.task_id RETURNING t.*""",
                (selected_run_id, worker_id, max(1, lease_seconds)),
            ).fetchone()
            if row:
                self._audit(
                    conn,
                    run_id=str(row["run_id"]),
                    task_id=str(row["task_id"]),
                    worker_id=worker_id,
                    stage="store.task.claimed",
                    message="Task lease acquired with SKIP LOCKED",
                    data={
                        "attempt": int(row["attempt"]),
                        "lease_version": int(row["lease_version"]),
                    },
                )
        return self._task(row) if row else None

    def heartbeat_runtime_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        lease_version: int | None = None,
    ) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                """UPDATE runtime_tasks SET
                       lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND (%s::bigint IS NULL OR lease_version=%s)""",
                (max(1, lease_seconds), task_id, worker_id, lease_version, lease_version),
            )
            return cur.rowcount == 1

    def cancel_runtime_tasks(self, run_id: str) -> int:
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                """UPDATE runtime_tasks SET status='cancelled',lease_owner=NULL,
                       lease_expires_at=NULL,finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE run_id=%s AND status IN
                       ('queued','blocked','running','waiting_approval','waiting_external')""",
                (run_id,),
            )
            conn.execute(
                """UPDATE graph_event_waits SET status='cancelled',
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status='pending'""",
                (run_id,),
            )
            conn.execute(
                """UPDATE approval_requests SET status='cancelled',resolution='cancelled',
                       resolved_by='system:cancel',resolved_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND subject_type='graph_node' AND status='pending'""",
                (run_id,),
            )
            self._notify(conn, run_id)
            return cur.rowcount

    def reset_runtime_tasks(self, run_id: str) -> int:
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                """UPDATE runtime_tasks t SET status=CASE WHEN EXISTS(
                           SELECT 1 FROM runtime_task_dependencies d WHERE d.task_id=t.task_id
                       ) THEN 'blocked' ELSE 'queued' END,
                       result=CASE
                           WHEN t.node_type IN ('foreach','bounded_loop','subrun') AND (
                               EXISTS(SELECT 1 FROM runtime_tasks child
                               WHERE child.parent_task_id=t.task_id)
                               OR EXISTS(SELECT 1 FROM runtime_runs child_run
                               WHERE child_run.parent_task_id=t.task_id)
                           ) THEN t.result ELSE NULL END,
                       error=NULL,attempt=0,available_at=clock_timestamp(),
                       lease_owner=NULL,lease_expires_at=NULL,started_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status!='completed'""",
                (run_id,),
            )
            self._notify(conn, run_id)
            return cur.rowcount

    def reconcile_runtime_graph(self, run_id: str) -> dict[str, int]:
        """Advance a DAG in one transaction and return its current status counts."""
        with self._pool.connection() as conn, conn.transaction():
            loop_parents = conn.execute(
                """UPDATE runtime_tasks parent SET status='queued',
                       updated_at=clock_timestamp()
                   WHERE parent.run_id=%s AND parent.status='blocked'
                     AND parent.node_type='bounded_loop' AND EXISTS (
                       SELECT 1 FROM runtime_tasks child
                       WHERE child.parent_task_id=parent.task_id
                         AND child.status IN ('failed','cancelled','timed_out','skipped'))
                   RETURNING parent.task_id""",
                (run_id,),
            ).rowcount
            skipped = conn.execute(
                """UPDATE runtime_tasks t SET status='skipped',
                       error='{"message":"dependency failed"}'::jsonb,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE t.run_id=%s AND t.status='blocked' AND EXISTS (
                       SELECT 1 FROM runtime_task_dependencies d
                       JOIN runtime_tasks dep ON dep.task_id=d.depends_on_task_id
                       WHERE d.task_id=t.task_id
                         AND dep.status IN ('failed','cancelled','timed_out','skipped'))""",
                (run_id,),
            ).rowcount
            queued = conn.execute(
                """UPDATE runtime_tasks t SET status='queued',updated_at=clock_timestamp()
                   WHERE t.run_id=%s AND t.status='blocked' AND NOT EXISTS (
                       SELECT 1 FROM runtime_task_dependencies d
                       JOIN runtime_tasks dep ON dep.task_id=d.depends_on_task_id
                       WHERE d.task_id=t.task_id AND dep.status!='completed')""",
                (run_id,),
            ).rowcount
            rows = conn.execute(
                "SELECT status,count(*) AS count FROM runtime_tasks WHERE run_id=%s GROUP BY status",
                (run_id,),
            ).fetchall()
            if loop_parents or skipped or queued:
                self._notify(conn, run_id)
        return {str(row["status"]): int(row["count"]) for row in rows}

    def start_runtime_graph(self, run_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                """UPDATE runtime_runs SET status='running',
                       started_at=COALESCE(started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND kind='graph' AND status='queued'""",
                (run_id,),
            )
            if cur.rowcount:
                self._notify(conn, run_id)
            return cur.rowcount == 1
