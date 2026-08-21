"""Atomic persistence transitions for bounded Graph loop nodes."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb

_MAX_RUNTIME_GRAPH_TASKS = 512
_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "skipped"}


class PostgresGraphLoopStoreMixin:
    def advance_runtime_bounded_loop(self, **kwargs: Any) -> dict[str, Any]:
        child = dict(kwargs["child"])
        iteration = int(kwargs["iteration"])
        payload = dict(child.get("payload") or {})
        if (
            not str(child.get("task_id") or "").startswith(f"{kwargs['task_id']}:loop:")
            or str(payload.get("bounded_loop_parent_task_id") or "") != kwargs["task_id"]
            or int(payload.get("bounded_loop_iteration") or 0) != iteration
            or str(payload.get("bounded_loop_id") or "") != kwargs["loop_id"]
            or str(payload.get("bounded_loop_input_state_hash") or "")
            != kwargs["input_state_hash"]
        ):
            raise ValueError("bounded_loop child identity is invalid")
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
                (kwargs["task_id"],),
            ).fetchone()
            if not self._owns_bounded_loop_task(task, kwargs):
                return {"saved": False, "status": "fenced"}
            children = conn.execute(
                """SELECT * FROM runtime_tasks WHERE run_id=%s AND parent_task_id=%s
                   ORDER BY (payload->>'bounded_loop_iteration')::int,task_id FOR UPDATE""",
                (kwargs["run_id"], kwargs["task_id"]),
            ).fetchall()
            if len(children) != iteration - 1:
                raise RuntimeError("bounded_loop iteration ledger changed")
            if any(str(item["status"]) != "completed" for item in children):
                raise RuntimeError("bounded_loop previous iteration is not complete")
            previous_child_id = kwargs.get("previous_child_id")
            if bool(children) != bool(previous_child_id) or (
                children and str(children[-1]["task_id"]) != str(previous_child_id)
            ):
                raise RuntimeError("bounded_loop previous child identity changed")
            count = conn.execute(
                "SELECT count(*) AS count FROM runtime_tasks WHERE run_id=%s",
                (kwargs["run_id"],),
            ).fetchone()
            if int(count["count"]) + 1 > _MAX_RUNTIME_GRAPH_TASKS:
                raise RuntimeError(
                    f"runtime Graph exceeds {_MAX_RUNTIME_GRAPH_TASKS} Tasks after loop advance"
                )
            conn.execute(
                """INSERT INTO runtime_tasks
                       (task_id,run_id,agent_id,parent_task_id,name,status,payload,
                        priority,max_attempts)
                   VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s)""",
                (
                    child["task_id"],
                    kwargs["run_id"],
                    child["agent_id"],
                    kwargs["task_id"],
                    child["name"],
                    Jsonb(child["payload"]),
                    child["priority"],
                    child["max_attempts"],
                ),
            )
            conn.execute(
                """INSERT INTO runtime_task_dependencies(task_id,depends_on_task_id)
                   VALUES (%s,%s)""",
                (kwargs["task_id"], child["task_id"]),
            )
            child_ids = [str(item["task_id"]) for item in children] + [child["task_id"]]
            result = {
                "status": "blocked",
                "stop_reason": "bounded_loop_waiting",
                "loop_id": kwargs["loop_id"],
                "iteration_count": iteration,
                "child_task_ids": child_ids,
                "latest_child_task_id": child["task_id"],
                "latest_input_state_hash": kwargs["input_state_hash"],
            }
            saved = conn.execute(
                """UPDATE runtime_tasks SET status='blocked',result=%s,error=NULL,
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
                raise RuntimeError("bounded_loop lease changed while row was locked")
            conn.execute(
                """UPDATE runtime_runs SET total_task_count=total_task_count+1,
                       updated_at=clock_timestamp() WHERE run_id=%s""",
                (kwargs["run_id"],),
            )
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.bounded_loop.advanced",
                message="Bounded loop iteration committed atomically",
                data={
                    "loop_id": kwargs["loop_id"],
                    "iteration": iteration,
                    "child_task_id": child["task_id"],
                    "lease_version": kwargs["lease_version"],
                },
            )
            self._notify(conn, kwargs["run_id"])
            return {"saved": True, "status": "blocked", "result": result}

    def finish_runtime_bounded_loop(self, **kwargs: Any) -> bool:
        outcome = str(kwargs["outcome"])
        if outcome not in {"completed", "exhausted", "iteration_failed"}:
            raise ValueError("invalid bounded_loop outcome")
        expected_ids = [str(item) for item in kwargs["child_task_ids"]]
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
                (kwargs["task_id"],),
            ).fetchone()
            if not self._owns_bounded_loop_task(task, kwargs):
                return False
            children = conn.execute(
                """SELECT * FROM runtime_tasks WHERE run_id=%s AND parent_task_id=%s
                   ORDER BY (payload->>'bounded_loop_iteration')::int,task_id FOR UPDATE""",
                (kwargs["run_id"], kwargs["task_id"]),
            ).fetchall()
            if [str(item["task_id"]) for item in children] != expected_ids:
                raise RuntimeError("bounded_loop child ledger changed")
            statuses = [str(item["status"]) for item in children]
            if outcome in {"completed", "exhausted"} and any(
                status != "completed" for status in statuses
            ):
                raise RuntimeError("bounded_loop cannot finish before iterations complete")
            if outcome == "iteration_failed" and (
                not statuses
                or any(status != "completed" for status in statuses[:-1])
                or statuses[-1] not in _TERMINAL - {"completed"}
            ):
                raise RuntimeError("bounded_loop failed iteration ledger is invalid")
            status = "completed" if outcome == "completed" else "failed"
            saved = conn.execute(
                """UPDATE runtime_tasks SET status=%s,result=%s,error=%s,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s RETURNING task_id""",
                (
                    status,
                    Jsonb(kwargs["result"]),
                    Jsonb(kwargs.get("error")) if kwargs.get("error") else None,
                    kwargs["task_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if saved is None:
                return False
            self._insert_bounded_loop_events(conn, kwargs.get("events") or [])
            if status == "completed":
                conn.execute(
                    """UPDATE runtime_tasks dependent SET status='queued',
                           updated_at=clock_timestamp()
                       WHERE dependent.run_id=%s AND dependent.status='blocked'
                         AND NOT EXISTS (
                           SELECT 1 FROM runtime_task_dependencies dependency
                           JOIN runtime_tasks parent
                             ON parent.task_id=dependency.depends_on_task_id
                           WHERE dependency.task_id=dependent.task_id
                             AND parent.status!='completed')""",
                    (kwargs["run_id"],),
                )
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage=f"store.graph.bounded_loop.{outcome}",
                message=f"Bounded loop finished with {outcome}",
                level="info" if status == "completed" else "error",
                data={
                    "outcome": outcome,
                    "iteration_count": len(children),
                    "lease_version": kwargs["lease_version"],
                },
            )
            self._notify(conn, kwargs["run_id"])
            return True

    @staticmethod
    def _insert_bounded_loop_events(conn: Any, events: list[dict[str, Any]]) -> None:
        for event in events:
            conn.execute(
                """INSERT INTO runtime_events
                       (event_id,run_id,task_id,root_run_id,parent_run_id,parent_task_id,
                        user_id,session_id,agent_id,turn_id,span_id,parent_span_id,tool_call_id,
                        attempt,phase,status,visibility,summary,worker_id,lease_version,
                        schema_version,event_type,data,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s::timestamptz)
                   ON CONFLICT(event_id) DO NOTHING""",
                (
                    event["event_id"],
                    event["run_id"],
                    event.get("task_id"),
                    event.get("root_run_id"),
                    event.get("parent_run_id"),
                    event.get("parent_task_id"),
                    event.get("user_id"),
                    event.get("session_id"),
                    event.get("agent_id"),
                    event.get("turn_id"),
                    event.get("span_id"),
                    event.get("parent_span_id"),
                    event.get("tool_call_id"),
                    event.get("attempt"),
                    event.get("phase"),
                    event.get("status"),
                    event.get("visibility") or "public",
                    event.get("summary"),
                    event.get("worker_id"),
                    event.get("lease_version"),
                    int(event.get("schema_version") or 2),
                    event["type"],
                    Jsonb(event.get("data") or {}),
                    event["created_at"],
                ),
            )

    @staticmethod
    def _owns_bounded_loop_task(task: Any, kwargs: dict[str, Any]) -> bool:
        return bool(
            task is not None
            and str(task["run_id"]) == kwargs["run_id"]
            and str(task["status"]) == "running"
            and str(task["lease_owner"] or "") == kwargs["worker_id"]
            and int(task["lease_version"]) == int(kwargs["lease_version"])
            and str(task["payload"].get("node_type") or "") == "bounded_loop"
        )
