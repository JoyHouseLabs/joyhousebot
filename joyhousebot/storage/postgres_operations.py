"""PostgreSQL runtime operational records and coordination primitives."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg import sql
from psycopg.errors import DeadlockDetected, LockNotAvailable
from psycopg.rows import dict_row

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_locks import (
    RUNTIME_PURGE_LOCK_ID,
    SCHEMA_MIGRATION_LOCK_ID,
)
from joyhousebot.storage.runtime_store import (
    RequestTraceEventRecord,
    RuntimeLogRecord,
)

_CHANNEL = "joyhousebot_runtime_work"
_TERMINAL = ("completed", "failed", "cancelled", "timed_out")
_TASK_TERMINAL = (*_TERMINAL, "skipped")


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
        # psycopg decodes a JSONB string scalar into a Python ``str``.  It is
        # valid artifact content (for example ``text/plain`` final output),
        # not necessarily serialized JSON; preserve it if it cannot be parsed.
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class PostgresOperationsStoreMixin:
    def get_platform_overview(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            totals = conn.execute(
                """SELECT count(*) AS runs,count(DISTINCT user_id) AS users,
                          count(DISTINCT (user_id,agent_id,session_id)) AS sessions,
                          count(*) FILTER (WHERE status IN ('queued','running','planning'))
                              AS active_runs,
                          COALESCE(sum((result->'usage'->>'input_tokens')::bigint),0)
                              AS input_tokens,
                          COALESCE(sum((result->'usage'->>'output_tokens')::bigint),0)
                              AS output_tokens,
                          COALESCE(sum((result->'usage'->>'cost_usd')::double precision),0)
                              AS cost_usd
                   FROM runtime_runs"""
            ).fetchone()
            statuses = conn.execute(
                "SELECT status,count(*) AS count FROM runtime_runs GROUP BY status"
            ).fetchall()
        workers = self.list_runtime_workers(limit=5000)
        input_tokens = int(totals["input_tokens"] or 0)
        output_tokens = int(totals["output_tokens"] or 0)
        return {
            "runs": int(totals["runs"]),
            "users": int(totals["users"]),
            "sessions": int(totals["sessions"]),
            "active_runs": int(totals["active_runs"]),
            "workers": len(workers),
            "healthy_workers": sum(1 for worker in workers if worker["healthy"]),
            "statuses": {str(row["status"]): int(row["count"]) for row in statuses},
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": float(totals["cost_usd"] or 0),
            },
        }

    def add_runtime_artifact(
        self,
        *,
        artifact_id: str,
        run_id: str,
        name: str,
        media_type: str,
        content: Any = None,
        uri: str | None = None,
        task_id: str | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO runtime_artifacts
                       (artifact_id,run_id,task_id,name,media_type,content,uri)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(artifact_id) DO UPDATE SET task_id=EXCLUDED.task_id,
                       name=EXCLUDED.name,media_type=EXCLUDED.media_type,
                       content=EXCLUDED.content,uri=EXCLUDED.uri""",
                (
                    artifact_id,
                    run_id,
                    task_id,
                    name,
                    media_type,
                    Jsonb(content) if content is not None else None,
                    uri,
                ),
            )

    def list_runtime_artifacts(
        self, run_id: str, *, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM runtime_artifacts WHERE run_id=%s"
        params: list[Any] = [run_id]
        if user_id is not None:
            query += (
                " AND EXISTS (SELECT 1 FROM runtime_runs owner"
                " WHERE owner.run_id=runtime_artifacts.run_id AND owner.user_id=%s)"
            )
            params.append(user_id)
        query += " ORDER BY created_at"
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "artifact_id": str(r["artifact_id"]),
                "run_id": str(r["run_id"]),
                "task_id": r["task_id"],
                "name": str(r["name"]),
                "media_type": str(r["media_type"]),
                "content": _json(r["content"]),
                "uri": r["uri"],
                "created_at": _iso(r["created_at"]) or "",
            }
            for r in rows
        ]

    def append_runtime_log(
        self,
        *,
        run_id: str,
        stage: str,
        message: str,
        level: str = "info",
        task_id: str | None = None,
        worker_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> RuntimeLogRecord:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO runtime_logs
                       (run_id,task_id,worker_id,level,stage,message,data)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING sequence,created_at""",
                (run_id, task_id, worker_id, level, stage, message, Jsonb(data or {})),
            ).fetchone()
            self._notify(conn, run_id)
        assert row is not None
        return RuntimeLogRecord(
            sequence=int(row["sequence"]),
            run_id=run_id,
            task_id=task_id,
            worker_id=worker_id,
            level=level,
            stage=stage,
            message=message,
            data=data or {},
            created_at=_iso(row["created_at"]) or "",
        )

    def list_runtime_logs(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
        task_id: str | None = None,
        user_id: str | None = None,
    ) -> list[RuntimeLogRecord]:
        params: list[Any] = [run_id, max(0, after_sequence)]
        query = """SELECT sequence,run_id,task_id,worker_id,level,stage,message,data,created_at
                   FROM runtime_logs WHERE run_id=%s AND sequence>%s"""
        if task_id is not None:
            query += " AND task_id=%s"
            params.append(task_id)
        if user_id is not None:
            query += (
                " AND EXISTS (SELECT 1 FROM runtime_runs owner"
                " WHERE owner.run_id=runtime_logs.run_id AND owner.user_id=%s)"
            )
            params.append(user_id)
        query += " ORDER BY sequence LIMIT %s"
        params.append(max(1, min(5000, limit)))
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            RuntimeLogRecord(
                sequence=int(r["sequence"]),
                run_id=str(r["run_id"]),
                task_id=r["task_id"],
                worker_id=r["worker_id"],
                level=str(r["level"]),
                stage=str(r["stage"]),
                message=str(r["message"]),
                data=dict(_json(r["data"], {})),
                created_at=_iso(r["created_at"]) or "",
            )
            for r in rows
        ]

    def register_runtime_worker(
        self,
        *,
        worker_id: str,
        capabilities: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO runtime_workers(worker_id,capabilities,metadata)
                   VALUES (%s,%s,%s) ON CONFLICT(worker_id) DO UPDATE SET
                       status='online',capabilities=EXCLUDED.capabilities,
                       metadata=EXCLUDED.metadata,last_heartbeat=clock_timestamp()""",
                (worker_id, Jsonb(capabilities or {}), Jsonb(metadata or {})),
            )

    def heartbeat_runtime_worker(self, worker_id: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE runtime_workers SET status='online',last_heartbeat=clock_timestamp() WHERE worker_id=%s",
                (worker_id,),
            )
            return cur.rowcount == 1

    def unregister_runtime_worker(self, worker_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE runtime_workers SET status='offline',last_heartbeat=clock_timestamp() WHERE worker_id=%s",
                (worker_id,),
            )

    def expire_stale_runtime_workers(self, *, stale_after_seconds: int = 120) -> int:
        """Fence crashed or force-stopped workers out of the live worker set.

        Worker ids are intentionally process-unique.  A clean shutdown marks
        its own row offline, but a host crash, SIGKILL, or deployment timeout
        cannot run that path.  The heartbeat is therefore a lease, not merely
        a UI freshness indicator: stale rows must no longer participate in
        plugin health, rollouts, or capacity reporting.
        """
        timeout = max(15, min(int(stale_after_seconds), 3600))
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """UPDATE runtime_workers
                   SET status='offline'
                   WHERE status='online'
                     AND last_heartbeat < clock_timestamp() - (%s * INTERVAL '1 second')""",
                (timeout,),
            )
            return max(0, cursor.rowcount)

    def list_runtime_workers(self, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT worker_id,status,capabilities,metadata,started_at,last_heartbeat,
                          (status='online' AND last_heartbeat >
                           clock_timestamp()-interval '2 minutes') AS healthy
                   FROM runtime_workers ORDER BY last_heartbeat DESC LIMIT %s""",
                (max(1, min(5000, limit)),),
            ).fetchall()
        return [
            {
                "worker_id": str(row["worker_id"]),
                "status": str(row["status"]),
                "healthy": bool(row["healthy"]),
                "capabilities": dict(_json(row["capabilities"], {})),
                "metadata": dict(_json(row["metadata"], {})),
                "started_at": _iso(row["started_at"]),
                "last_heartbeat": _iso(row["last_heartbeat"]),
            }
            for row in rows
        ]

    # ---------- Durable conversation sessions ----------

    def get_session_state(self, storage_key: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT state FROM conversation_sessions WHERE storage_key=%s",
                (storage_key,),
            ).fetchone()
        return dict(_json(row["state"], {})) if row else None

    def save_session_state(
        self,
        storage_key: str,
        *,
        session_key: str,
        namespace: str,
        state: dict[str, Any],
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO conversation_sessions
                       (storage_key,session_key,namespace,state,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s::timestamptz,clock_timestamp())
                   ON CONFLICT(storage_key) DO UPDATE SET
                       session_key=EXCLUDED.session_key,
                       namespace=EXCLUDED.namespace,
                       state=EXCLUDED.state,
                       updated_at=clock_timestamp()""",
                (
                    storage_key,
                    session_key,
                    namespace,
                    Jsonb(state),
                    state.get("created_at") or datetime.now().astimezone().isoformat(),
                ),
            )

    def delete_session_state(self, storage_key: str) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM conversation_sessions WHERE storage_key=%s", (storage_key,)
            )
        return cursor.rowcount > 0

    def list_session_states(self, *, namespace: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT session_key,created_at,updated_at
                   FROM conversation_sessions WHERE namespace=%s
                   ORDER BY updated_at DESC LIMIT %s""",
                (namespace, max(1, min(10000, limit))),
            ).fetchall()
        return [
            {
                "session_key": str(row["session_key"]),
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            }
            for row in rows
        ]

    # ---------- End-to-end request tracing ----------

    @staticmethod
    def _request_trace_event(row: dict[str, Any]) -> RequestTraceEventRecord:
        return RequestTraceEventRecord(
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            tracker_id=str(row["tracker_id"]),
            request_id=str(row["request_id"]),
            parent_request_id=(
                str(row["parent_request_id"]) if row.get("parent_request_id") else None
            ),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            run_id=str(row["run_id"]) if row.get("run_id") else None,
            transport=str(row["transport"]),
            direction=str(row["direction"]),
            operation=str(row["operation"]),
            stage=str(row["stage"]),
            status=str(row["status"]) if row.get("status") else None,
            data=dict(_json(row.get("data"), {}) or {}),
            created_at=_iso(row["created_at"]) or "",
        )

    def append_request_trace_event(self, **kwargs: Any) -> RequestTraceEventRecord:
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO request_trace_events
                       (event_id,tracker_id,request_id,parent_request_id,user_id,run_id,
                        transport,direction,operation,stage,status,data,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           COALESCE(%s::timestamptz,clock_timestamp()))
                   RETURNING *""",
                (
                    str(kwargs.get("event_id") or uuid4().hex),
                    str(kwargs["tracker_id"]),
                    str(kwargs["request_id"]),
                    kwargs.get("parent_request_id"),
                    kwargs.get("user_id"),
                    kwargs.get("run_id"),
                    str(kwargs.get("transport") or "internal"),
                    str(kwargs.get("direction") or "internal"),
                    str(kwargs.get("operation") or "request"),
                    str(kwargs.get("stage") or "event"),
                    kwargs.get("status"),
                    Jsonb(kwargs.get("data") or {}),
                    kwargs.get("created_at"),
                ),
            ).fetchone()
        assert row is not None
        return self._request_trace_event(row)

    def list_request_trace_events(
        self,
        tracker_id: str,
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        limit: int = 2000,
    ) -> list[RequestTraceEventRecord]:
        clauses = [sql.SQL("tracker_id=%s")]
        values: list[Any] = [tracker_id]
        if request_id is not None:
            clauses.append(sql.SQL("request_id=%s"))
            values.append(request_id)
        if user_id is not None:
            clauses.append(sql.SQL("user_id=%s"))
            values.append(user_id)
        values.append(max(1, min(limit, 10000)))
        query = sql.SQL(
            "SELECT * FROM request_trace_events WHERE {} ORDER BY sequence LIMIT %s"
        ).format(sql.SQL(" AND ").join(clauses))
        with self._pool.connection() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._request_trace_event(row) for row in rows]

    def notify_work(self, run_id: str | None = None) -> None:
        with self._pool.connection() as conn:
            self._notify(conn, run_id)

    def wait_for_work(self, timeout: float) -> bool:
        """Block until committed work is announced; polling remains the safety net."""
        with self._listener_lock:
            if self._closed:
                return False
            if self._listener is None or self._listener.closed:
                from psycopg import connect

                self._listener = connect(
                    self.database_url,
                    autocommit=True,
                    row_factory=dict_row,
                    application_name=f"{self.application_name}-listener",
                )
                self._listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(_CHANNEL)))
            try:
                return (
                    next(self._listener.notifies(timeout=max(0.0, timeout), stop_after=1), None)
                    is not None
                )
            except Exception:
                try:
                    self._listener.close()
                finally:
                    self._listener = None
                return False

    def healthcheck(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT current_database() AS database,
                          current_setting('server_version') AS version,
                          (SELECT count(*) FROM runtime_workers
                           WHERE status='online' AND last_heartbeat > clock_timestamp()-interval '2 minutes')
                              AS online_workers,
                          (SELECT count(*) FROM runtime_tasks WHERE status='queued') AS queued_tasks,
                          (SELECT count(*) FROM runtime_tasks WHERE status='running') AS running_tasks"""
            ).fetchone()
        return {
            "ok": True,
            "backend": self.backend_name,
            "database": row["database"] if row else None,
            "server_version": row["version"] if row else None,
            "online_workers": int(row["online_workers"]) if row else 0,
            "queued_tasks": int(row["queued_tasks"]) if row else 0,
            "running_tasks": int(row["running_tasks"]) if row else 0,
        }

    def runtime_metrics(self) -> dict[str, Any]:
        """Return low-cardinality operational counters for dashboards/alerts."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT status, count(*) AS count FROM runtime_runs GROUP BY status
                   ORDER BY status"""
            ).fetchall()
            task_rows = conn.execute(
                """SELECT status, count(*) AS count FROM runtime_tasks GROUP BY status
                   ORDER BY status"""
            ).fetchall()
            workers = conn.execute(
                """SELECT status, count(*) AS count FROM runtime_workers GROUP BY status
                   ORDER BY status"""
            ).fetchall()
        return {
            "runs": {str(row["status"]): int(row["count"]) for row in rows},
            "tasks": {str(row["status"]): int(row["count"]) for row in task_rows},
            "workers": {str(row["status"]): int(row["count"]) for row in workers},
        }

    def operational_metrics(self) -> dict[str, Any]:
        """Return bounded aggregates used by Prometheus and the control UI."""
        metrics = self.runtime_metrics()
        with self._pool.connection() as conn:
            queue = conn.execute(
                """SELECT count(*) FILTER (WHERE status='queued') AS queued,
                          COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() -
                              min(available_at) FILTER (WHERE status='queued'))), 0)
                              AS oldest_age_seconds,
                          count(*) FILTER (WHERE status='running'
                              AND lease_expires_at < clock_timestamp()) AS expired_leases,
                          count(*) FILTER (WHERE attempt > 1) AS retried
                   FROM runtime_tasks"""
            ).fetchone()
            providers = conn.execute(
                """SELECT provider, model, status, count(*) AS count,
                          COALESCE(avg(duration_ms), 0) AS avg_duration_ms,
                          COALESCE(avg(ttft_ms), 0) AS avg_ttft_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                              FILTER (WHERE duration_ms IS NOT NULL), 0) AS p95_duration_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms)
                              FILTER (WHERE ttft_ms IS NOT NULL), 0) AS p95_ttft_ms,
                          COALESCE(sum(cost_usd), 0) AS cost_usd
                   FROM model_invocations GROUP BY provider, model, status
                   ORDER BY provider, model, status LIMIT 500"""
            ).fetchall()
            stale_workers = conn.execute(
                """SELECT count(*) AS count FROM runtime_workers
                   WHERE status='online' AND last_heartbeat < clock_timestamp() - INTERVAL '30 seconds'"""
            ).fetchone()
            outbox = conn.execute(
                """SELECT channel, status, count(*) AS count
                   FROM channel_outbox GROUP BY channel, status
                   ORDER BY channel, status LIMIT 500"""
            ).fetchall() if conn.execute("SELECT to_regclass('public.channel_outbox') AS name").fetchone()["name"] else []
        metrics["providers"] = [
            {
                "provider": str(row["provider"]), "model": str(row["model"]),
                "status": str(row["status"]), "count": int(row["count"]),
                "avg_duration_ms": float(row["avg_duration_ms"] or 0),
                "avg_ttft_ms": float(row["avg_ttft_ms"] or 0),
                "p95_duration_ms": float(row["p95_duration_ms"] or 0),
                "p95_ttft_ms": float(row["p95_ttft_ms"] or 0),
                "cost_usd": float(row["cost_usd"] or 0),
            }
            for row in providers
        ]
        metrics["queue"] = {
            "queued": int(queue["queued"] or 0),
            "oldest_age_seconds": float(queue["oldest_age_seconds"] or 0),
            "expired_leases": int(queue["expired_leases"] or 0),
            "retried_tasks": int(queue["retried"] or 0),
        }
        metrics["workers_stale"] = int(stale_workers["count"] or 0)
        metrics["channels"] = [
            {"channel": str(row["channel"]), "status": str(row["status"]), "count": int(row["count"])}
            for row in outbox
        ]
        return metrics

    async def purge_old_runtime_data(self, older_than_ms: int) -> dict[str, int]:
        """Delete expired runtime telemetry under cluster-wide maintenance locks."""
        cutoff = datetime.fromtimestamp(older_than_ms / 1000, tz=timezone.utc)
        for attempt in range(3):
            try:
                counts: dict[str, int] = {}
                with self._pool.connection() as conn, conn.transaction():
                    migration_lock = conn.execute(
                        "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                        (SCHEMA_MIGRATION_LOCK_ID,),
                    ).fetchone()
                    if not migration_lock or not migration_lock["acquired"]:
                        return {}
                    purge_lock = conn.execute(
                        "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                        (RUNTIME_PURGE_LOCK_ID,),
                    ).fetchone()
                    if not purge_lock or not purge_lock["acquired"]:
                        return {}
                    conn.execute("SET LOCAL lock_timeout = '2s'")
                    for table, timestamp_column in (
                        ("model_response_cache", "created_at"),
                        ("model_reasoning_segments", "created_at"),
                        ("model_invocations", "started_at"),
                        ("execution_spans", "started_at"),
                        ("trace_blobs", "created_at"),
                        ("replay_runs", "created_at"),
                        ("runtime_events", "created_at"),
                        ("runtime_logs", "created_at"),
                        ("request_trace_events", "created_at"),
                    ):
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE {timestamp_column} < %s", (cutoff,)
                        )
                        counts[table] = max(0, cursor.rowcount)
                return counts
            except (DeadlockDetected, LockNotAvailable):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (2**attempt))
        return {}

    def close(self) -> None:
        self._closed = True
        with self._listener_lock:
            if self._listener is not None:
                self._listener.close()
                self._listener = None
        self._pool.close()
