"""Atomic expansion and completion of bounded Graph ``foreach`` nodes."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.storage.json_codec import Jsonb

_MAX_RUNTIME_GRAPH_TASKS = 512


class PostgresGraphForeachStoreMixin:
    def expand_runtime_foreach(self, **kwargs: Any) -> dict[str, Any]:
        children = list(kwargs.get("children") or [])
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
                (kwargs["task_id"],),
            ).fetchone()
            if not self._owns_foreach_task(task, kwargs):
                return {"saved": False, "status": "fenced", "child_task_ids": []}
            count = conn.execute(
                "SELECT count(*) AS count FROM runtime_tasks WHERE run_id=%s",
                (kwargs["run_id"],),
            ).fetchone()
            if int(count["count"]) + len(children) > _MAX_RUNTIME_GRAPH_TASKS:
                raise RuntimeError(
                    f"runtime Graph exceeds {_MAX_RUNTIME_GRAPH_TASKS} Tasks after foreach expansion"
                )
            child_ids = [str(item["task_id"]) for item in children]
            if children:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO runtime_tasks
                               (task_id,run_id,agent_id,parent_task_id,name,status,payload,
                                priority,max_attempts)
                           VALUES (%s,%s,%s,%s,%s,'queued',%s,%s,%s)""",
                        [
                            (
                                item["task_id"],
                                kwargs["run_id"],
                                item["agent_id"],
                                kwargs["task_id"],
                                item["name"],
                                Jsonb(item["payload"]),
                                item["priority"],
                                item["max_attempts"],
                            )
                            for item in children
                        ],
                    )
                    cursor.executemany(
                        """INSERT INTO runtime_task_dependencies
                               (task_id,depends_on_task_id) VALUES (%s,%s)""",
                        [(kwargs["task_id"], child_id) for child_id in child_ids],
                    )
            result = {
                "status": "blocked" if children else "completed",
                "stop_reason": "foreach_expanded" if children else "foreach_empty",
                "expansion_id": kwargs["expansion_id"],
                "item_count": len(children),
                "child_task_ids": child_ids,
                "structured_output": {"items": [], "count": 0} if not children else None,
                "content": '{"count": 0, "items": []}' if not children else None,
            }
            status = "blocked" if children else "completed"
            saved = conn.execute(
                """UPDATE runtime_tasks SET status=%s,result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,
                       finished_at=CASE WHEN %s='completed' THEN clock_timestamp() ELSE NULL END,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s RETURNING task_id""",
                (
                    status,
                    Jsonb(result),
                    status,
                    kwargs["task_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if saved is None:
                return {"saved": False, "status": "fenced", "child_task_ids": []}
            if children:
                conn.execute(
                    """UPDATE runtime_runs SET total_task_count=total_task_count+%s,
                           updated_at=clock_timestamp() WHERE run_id=%s""",
                    (len(children), kwargs["run_id"]),
                )
            else:
                self._queue_completed_dependents(conn, kwargs["run_id"])
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.foreach.expanded",
                message="Bounded foreach instances committed atomically",
                data={
                    "expansion_id": kwargs["expansion_id"],
                    "item_count": len(children),
                    "lease_version": kwargs["lease_version"],
                },
            )
            self._notify(conn, kwargs["run_id"])
            return {
                "saved": True,
                "status": status,
                "child_task_ids": child_ids,
                "result": result,
            }

    def complete_runtime_foreach(self, **kwargs: Any) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                "SELECT * FROM runtime_tasks WHERE task_id=%s FOR UPDATE",
                (kwargs["task_id"],),
            ).fetchone()
            if not self._owns_foreach_task(task, kwargs):
                return None
            children = conn.execute(
                """SELECT * FROM runtime_tasks WHERE run_id=%s AND parent_task_id=%s
                   ORDER BY (payload->>'foreach_item_index')::int,task_id FOR UPDATE""",
                (kwargs["run_id"], kwargs["task_id"]),
            ).fetchall()
            expected = int(dict(task["result"] or {}).get("item_count") or 0)
            if len(children) != expected or any(
                str(child["status"]) != "completed" for child in children
            ):
                raise RuntimeError("foreach children are not complete or changed")
            entries = [
                {
                    "index": int(child["payload"]["foreach_item_index"]),
                    "item": child["payload"].get("foreach_item"),
                    "item_hash": child["payload"]["foreach_item_hash"],
                    "output": dict(child["result"] or {}),
                }
                for child in children
            ]
            structured = {"items": entries, "count": len(entries)}
            usage = {
                key: sum(
                    float((dict(child["result"] or {}).get("usage") or {}).get(key) or 0)
                    for child in children
                )
                for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd")
            }
            result = {
                "status": "completed",
                "stop_reason": "foreach_completed",
                "expansion_id": dict(task["result"] or {}).get("expansion_id"),
                "item_count": len(entries),
                "child_task_ids": [str(child["task_id"]) for child in children],
                "structured_output": structured,
                "content": json.dumps(structured, ensure_ascii=False, sort_keys=True),
                "usage": usage,
                "tools_used": sorted(
                    {
                        str(tool)
                        for child in children
                        for tool in (dict(child["result"] or {}).get("tools_used") or [])
                    }
                ),
            }
            saved = conn.execute(
                """UPDATE runtime_tasks SET status='completed',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
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
            self._queue_completed_dependents(conn, kwargs["run_id"])
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.foreach.completed",
                message="Foreach child results aggregated deterministically",
                data={"item_count": len(entries), "lease_version": kwargs["lease_version"]},
            )
            self._notify(conn, kwargs["run_id"])
            return result

    @staticmethod
    def _owns_foreach_task(task: Any, kwargs: dict[str, Any]) -> bool:
        return bool(
            task is not None
            and str(task["run_id"]) == kwargs["run_id"]
            and str(task["status"]) == "running"
            and str(task["lease_owner"] or "") == kwargs["worker_id"]
            and int(task["lease_version"]) == int(kwargs["lease_version"])
            and str(task["payload"].get("node_type") or "") == "foreach"
        )

    @staticmethod
    def _queue_completed_dependents(conn: Any, run_id: str) -> None:
        conn.execute(
            """UPDATE runtime_tasks task SET status='queued',updated_at=clock_timestamp()
               WHERE task.run_id=%s AND task.status='blocked' AND NOT EXISTS (
                   SELECT 1 FROM runtime_task_dependencies dependency
                   JOIN runtime_tasks parent
                     ON parent.task_id=dependency.depends_on_task_id
                   WHERE dependency.task_id=task.task_id AND parent.status!='completed')""",
            (run_id,),
        )
