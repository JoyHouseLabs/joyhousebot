"""Atomic PostgreSQL promotion of a clarified Run into a task graph."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


class PostgresGraphStoreMixin:
    def materialize_runtime_graph(
        self,
        *,
        run_id: str,
        user_id: str,
        options: dict[str, Any],
        tasks: list[dict[str, Any]],
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> Any:
        with self._pool.connection() as conn, conn.transaction():
            run = conn.execute(
                "SELECT * FROM runtime_runs WHERE run_id=%s AND user_id=%s FOR UPDATE",
                (run_id, user_id),
            ).fetchone()
            if run is None:
                raise ValueError("planning run not found")
            existing = conn.execute(
                "SELECT COUNT(*) AS count FROM runtime_tasks WHERE run_id=%s", (run_id,)
            ).fetchone()
            if run["kind"] == "graph" and int(existing["count"]) == len(tasks):
                return self._run(run)
            owned_running = (
                run["status"] == "running"
                and worker_id is not None
                and run["lease_owner"] == worker_id
                and (lease_version is None or int(run["lease_version"]) == lease_version)
            )
            # A clarified scenario is queued before graph materialization so a
            # coordinator replica can claim it safely.  Accept that durable
            # hand-off state in addition to the legacy planning state.
            materializable = run["status"] in {"planning", "queued"}
            if (not materializable and not owned_running) or int(existing["count"]):
                raise ValueError("run cannot be materialized as a graph")
            row = conn.execute(
                """UPDATE runtime_runs SET kind='graph',status='queued',options=%s,
                       total_task_count=%s,lease_owner=NULL,lease_expires_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND user_id=%s
                     AND (status IN ('planning','queued') OR (status='running' AND lease_owner=%s
                          AND (%s::bigint IS NULL OR lease_version=%s))) RETURNING *""",
                (
                    Jsonb(options),
                    len(tasks),
                    run_id,
                    user_id,
                    worker_id,
                    lease_version,
                    lease_version,
                ),
            ).fetchone()
            assert row is not None
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO runtime_tasks
                           (task_id,run_id,agent_id,name,status,payload,priority,max_attempts)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            item["task_id"],
                            run_id,
                            item["agent_id"],
                            item["name"],
                            "blocked" if item.get("dependencies") else "queued",
                            Jsonb(item["payload"]),
                            item["priority"],
                            max(1, int(item["max_attempts"])),
                        )
                        for item in tasks
                    ],
                )
            edges = [
                (item["task_id"], dependency)
                for item in tasks
                for dependency in item.get("dependencies", [])
            ]
            if edges:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO runtime_task_dependencies
                               (task_id,depends_on_task_id) VALUES (%s,%s)""",
                        edges,
                    )
            self._audit(
                conn,
                run_id=run_id,
                stage="store.graph.materialized",
                message="Clarified run promoted to graph atomically",
                data={"task_count": len(tasks)},
            )
            self._notify(conn, run_id)
            return self._run(row)
