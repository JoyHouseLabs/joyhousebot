"""Atomic PostgreSQL promotion of a clarified Run into a task graph."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_quotas import check_top_level_submission_quota
from joyhousebot.storage.runtime_store import RuntimeRunRecord


class PostgresGraphStoreMixin:
    def create_runtime_graph(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        prompt: str,
        options: dict[str, Any],
        tasks: list[dict[str, Any]],
        revision: dict[str, Any] | None = None,
        created_by: str = "runtime",
        idempotency_key: str | None = None,
        max_active_per_user: int | None = None,
        max_submissions_per_minute: int | None = None,
        root_run_id: str | None = None,
        parent_run_id: str | None = None,
        parent_task_id: str | None = None,
        max_children_per_root: int | None = None,
        input_asset_ids: list[str] | tuple[str, ...] = (),
    ) -> tuple[RuntimeRunRecord, bool]:
        """Persist a run, immutable revision, Tasks and edges atomically."""
        with self._pool.connection() as conn, conn.transaction():
            def require_same_idempotent_graph(existing_row: Any) -> None:
                requested_hash = str((revision or {}).get("spec_hash") or "")
                if not requested_hash:
                    return
                frozen = conn.execute(
                    """SELECT spec_hash FROM graph_revisions
                       WHERE run_id=%s AND revision_number=1""",
                    (existing_row["run_id"],),
                ).fetchone()
                if frozen and str(frozen["spec_hash"]) != requested_hash:
                    raise ValueError(
                        "Graph Idempotency-Key was reused with a different request"
                    )

            if parent_run_id is None and root_run_id is None:
                existing = check_top_level_submission_quota(
                    conn,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    max_active_per_user=max_active_per_user,
                    max_submissions_per_minute=max_submissions_per_minute,
                )
                if existing is not None:
                    require_same_idempotent_graph(existing)
                    self._require_same_run_input_assets_in_transaction(
                        conn,
                        run_id=str(existing["run_id"]),
                        user_id=user_id,
                        asset_ids=input_asset_ids,
                    )
                    return self._run(existing), False
            if root_run_id and parent_run_id and max_children_per_root is not None:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (root_run_id,))
                existing = None
                if idempotency_key:
                    existing = conn.execute(
                        """SELECT *,FALSE AS created FROM runtime_runs
                           WHERE user_id=%s AND agent_id=%s AND session_id=%s
                             AND idempotency_key=%s""",
                        (user_id, agent_id, session_id, idempotency_key),
                    ).fetchone()
                if existing is not None:
                    require_same_idempotent_graph(existing)
                    self._require_same_run_input_assets_in_transaction(
                        conn,
                        run_id=str(existing["run_id"]),
                        user_id=user_id,
                        asset_ids=input_asset_ids,
                    )
                    return self._run(existing), False
                child_count = conn.execute(
                    """SELECT COUNT(*) AS count FROM runtime_runs
                       WHERE root_run_id=%s AND parent_run_id IS NOT NULL""",
                    (root_run_id,),
                ).fetchone()
                if int(child_count["count"]) >= max(0, int(max_children_per_root)):
                    raise RuntimeError(
                        f"child run fan-out limit reached ({max_children_per_root})"
                    )
            revision = revision or self._freeze_graph_revision_from_rows(
                run_id, goal=prompt, options=options, tasks=tasks
            )
            options = {**options, "graph_revision_id": revision["revision_id"]}
            row = conn.execute(
                """INSERT INTO runtime_runs
                       (run_id,user_id,session_id,agent_id,kind,status,prompt,options,
                        idempotency_key,root_run_id,parent_run_id,parent_task_id,total_task_count)
                   VALUES (%s,%s,%s,%s,'graph','queued',%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,agent_id,session_id,idempotency_key)
                       WHERE idempotency_key IS NOT NULL DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    run_id,
                    user_id,
                    session_id,
                    agent_id,
                    prompt,
                    Jsonb(options),
                    idempotency_key,
                    root_run_id or run_id,
                    parent_run_id,
                    parent_task_id,
                    len(tasks),
                ),
            ).fetchone()
            if row is None:
                if idempotency_key is None:
                    raise RuntimeError(f"runtime run already exists: {run_id}")
                row = conn.execute(
                    """SELECT *,FALSE AS created FROM runtime_runs
                       WHERE user_id=%s AND agent_id=%s AND session_id=%s
                         AND idempotency_key=%s""",
                    (user_id, agent_id, session_id, idempotency_key),
                ).fetchone()
            assert row is not None
            if not row["created"]:
                require_same_idempotent_graph(row)
                self._require_same_run_input_assets_in_transaction(
                    conn,
                    run_id=str(row["run_id"]),
                    user_id=user_id,
                    asset_ids=input_asset_ids,
                )
                return self._run(row), False
            self._bind_input_assets_in_transaction(
                conn, run_id=run_id, user_id=user_id, asset_ids=input_asset_ids
            )
            self._insert_graph_revision(
                conn,
                run_id=run_id,
                user_id=user_id,
                revision=revision,
                created_by=created_by,
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO runtime_tasks
                           (task_id,run_id,agent_id,name,status,payload,priority,max_attempts)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            item["task_id"],
                            run_id,
                            item.get("agent_id") or agent_id,
                            item["name"],
                            item.get("initial_status")
                            or ("blocked" if item.get("dependencies") else "queued"),
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
                        "INSERT INTO runtime_task_dependencies(task_id,depends_on_task_id) VALUES (%s,%s)",
                        edges,
                    )
            self._audit(
                conn,
                run_id=run_id,
                stage="store.graph.created",
                message="Graph and immutable revision committed atomically",
                data={"task_count": len(tasks), "graph_revision_id": revision["revision_id"]},
            )
            self._notify(conn, run_id)
            saved = conn.execute("SELECT * FROM runtime_runs WHERE run_id=%s", (run_id,)).fetchone()
            return self._run(saved), True

    def materialize_runtime_graph(
        self,
        *,
        run_id: str,
        user_id: str,
        options: dict[str, Any],
        tasks: list[dict[str, Any]],
        revision: dict[str, Any] | None = None,
        created_by: str = "runtime",
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
            revision = revision or self._freeze_graph_revision_from_rows(
                run_id, goal=str(run["prompt"]), options=options, tasks=tasks
            )
            if run["kind"] == "graph" and int(existing["count"]) == len(tasks):
                if str(run["graph_revision_id"] or "") != revision["revision_id"]:
                    raise ValueError("materialized Graph revision conflicts with frozen Run")
                return self._run(run)
            owned_running = (
                run["status"] == "running"
                and worker_id is not None
                and run["lease_owner"] == worker_id
                and (lease_version is None or int(run["lease_version"]) == lease_version)
            )
            # A clarified scenario is queued before graph materialization so a
            # coordinator replica can claim it safely. Accept both durable
            # pre-execution states.
            materializable = run["status"] in {"planning", "queued"}
            if (not materializable and not owned_running) or int(existing["count"]):
                raise ValueError("run cannot be materialized as a graph")
            options = {**options, "graph_revision_id": revision["revision_id"]}
            self._insert_graph_revision(
                conn,
                run_id=run_id,
                user_id=user_id,
                revision=revision,
                created_by=created_by,
            )
            row = conn.execute(
                """UPDATE runtime_runs SET kind='graph',status='queued',options=%s,
                       graph_revision_id=%s,total_task_count=%s,
                       lease_owner=NULL,lease_expires_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND user_id=%s
                     AND (status IN ('planning','queued') OR (status='running' AND lease_owner=%s
                          AND (%s::bigint IS NULL OR lease_version=%s))) RETURNING *""",
                (
                    Jsonb(options),
                    revision["revision_id"],
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
                            item.get("initial_status")
                            or ("blocked" if item.get("dependencies") else "queued"),
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
