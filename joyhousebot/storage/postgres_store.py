"""PostgreSQL-first durable runtime store.

PostgreSQL is the coordination plane: row locks serialize transitions,
``SKIP LOCKED`` distributes work, database time owns leases, lease versions
fence stale workers, JSONB keeps structured context queryable, and NOTIFY
wakes idle workers after the creating transaction commits.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from joyhousebot.storage.postgres_admins import PostgresAdminStoreMixin
from joyhousebot.storage.postgres_agents import PostgresAgentStoreMixin
from joyhousebot.storage.postgres_capabilities import PostgresCapabilityStoreMixin
from joyhousebot.storage.postgres_clarifications import PostgresClarificationStoreMixin
from joyhousebot.storage.postgres_graphs import PostgresGraphStoreMixin
from joyhousebot.storage.postgres_locks import SCHEMA_MIGRATION_LOCK_ID
from joyhousebot.storage.postgres_mcp import PostgresMCPStoreMixin
from joyhousebot.storage.postgres_observability import PostgresObservabilityStoreMixin
from joyhousebot.storage.postgres_operations import PostgresOperationsStoreMixin
from joyhousebot.storage.postgres_plugins import PostgresPluginStoreMixin
from joyhousebot.storage.postgres_rate_limits import PostgresRateLimitStoreMixin
from joyhousebot.storage.postgres_rollouts import PostgresRolloutStoreMixin
from joyhousebot.storage.postgres_runs import PostgresRunStoreMixin
from joyhousebot.storage.postgres_scenarios import PostgresScenarioStoreMixin
from joyhousebot.storage.postgres_tasks import PostgresTaskStoreMixin
from joyhousebot.storage.runtime_store import (
    RuntimeRunRecord,
    RuntimeTaskRecord,
    destructive_migrate_enabled,
)

_CHANNEL = "joyhousebot_runtime_work"
_TERMINAL = ("completed", "failed", "cancelled", "timed_out")
_TASK_TERMINAL = (*_TERMINAL, "skipped")

_logger = logging.getLogger(__name__)


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


class PostgresRuntimeStore(
    PostgresAdminStoreMixin,
    PostgresAgentStoreMixin,
    PostgresCapabilityStoreMixin,
    PostgresClarificationStoreMixin,
    PostgresScenarioStoreMixin,
    PostgresGraphStoreMixin,
    PostgresRunStoreMixin,
    PostgresTaskStoreMixin,
    PostgresRateLimitStoreMixin,
    PostgresObservabilityStoreMixin,
    PostgresRolloutStoreMixin,
    PostgresOperationsStoreMixin,
    PostgresPluginStoreMixin,
    PostgresMCPStoreMixin,
):
    """Production runtime store backed by a psycopg connection pool."""

    backend_name = "postgres"

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        application_name: str = "joyhousebot-runtime",
        auto_migrate: bool = True,
    ) -> None:
        if not database_url.strip():
            raise ValueError("PostgreSQL database_url is required")
        self.database_url = database_url
        self.application_name = application_name
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=max(0, min_pool_size),
            max_size=max(1, max_pool_size),
            kwargs={"row_factory": dict_row, "application_name": application_name},
            open=True,
        )
        self._listener = None
        self._listener_lock = threading.Lock()
        self._closed = False
        self._pool.wait(timeout=10)
        if auto_migrate:
            self._migrate_all()

    def _migrate_all(self) -> None:
        """Serialize the complete migration sequence across all processes.

        Per-domain migration locks are not enough: one process can otherwise
        create an observability index while another process is still altering
        a runtime table referenced by that index.  Use a dedicated connection
        so this also works when the runtime pool has a maximum size of one.
        """
        with psycopg.connect(
            self.database_url,
            autocommit=True,
            application_name=f"{self.application_name}-migration-lock",
        ) as lock_connection:
            lock_connection.execute(
                "SELECT pg_advisory_lock(%s)", (SCHEMA_MIGRATION_LOCK_ID,)
            )
            try:
                self.migrate()
                self.migrate_admins()
                self.migrate_agents()
                self.migrate_capabilities()
                self.migrate_plugins()
                self.migrate_scenarios()
                self.migrate_clarifications()
                self.migrate_rate_limits()
                self.migrate_observability()
                self.migrate_mcp_servers()
            finally:
                lock_connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (SCHEMA_MIGRATION_LOCK_ID,)
                )

    def migrate(self) -> None:
        """Apply idempotent schema changes under a cluster-wide advisory lock."""
        ddl = """
        CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );

        CREATE TABLE IF NOT EXISTS runtime_runs (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'agent',
            status TEXT NOT NULL,
            prompt TEXT NOT NULL,
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB,
            error JSONB,
            idempotency_key TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version BIGINT NOT NULL DEFAULT 0,
            root_run_id TEXT,
            parent_run_id TEXT,
            parent_task_id TEXT,
            current_phase TEXT,
            status_summary TEXT,
            status_reason TEXT,
            next_action TEXT,
            waiting_on TEXT,
            active_turn_id TEXT,
            active_span_count INTEGER NOT NULL DEFAULT 0,
            completed_task_count INTEGER NOT NULL DEFAULT 0,
            total_task_count INTEGER NOT NULL DEFAULT 0,
            last_event_sequence BIGINT NOT NULL DEFAULT 0,
            last_progress_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_runs_parent
            ON runtime_runs(parent_run_id, created_at) WHERE parent_run_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_runtime_runs_root
            ON runtime_runs(root_run_id, created_at) WHERE root_run_id IS NOT NULL;
        DROP INDEX IF EXISTS uq_runtime_runs_idempotency;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_runs_idempotency_v2
            ON runtime_runs(user_id, agent_id, session_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_runtime_runs_active
            ON runtime_runs(status, created_at)
            WHERE status IN ('queued', 'running');
        CREATE INDEX IF NOT EXISTS ix_runtime_runs_user_session_created
            ON runtime_runs(user_id, session_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_runtime_runs_agent_created
            ON runtime_runs(agent_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS runtime_tasks (
            task_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            parent_task_id TEXT,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB,
            error JSONB,
            priority INTEGER NOT NULL DEFAULT 100,
            attempt INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_tasks_claim
            ON runtime_tasks(priority, available_at, created_at)
            WHERE status = 'queued';
        CREATE INDEX IF NOT EXISTS ix_runtime_tasks_expired
            ON runtime_tasks(lease_expires_at)
            WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS ix_runtime_tasks_run
            ON runtime_tasks(run_id, priority, created_at);
        CREATE INDEX IF NOT EXISTS ix_runtime_tasks_agent_claim
            ON runtime_tasks(agent_id, priority, available_at, created_at)
            WHERE status = 'queued';

        CREATE TABLE IF NOT EXISTS runtime_task_dependencies (
            task_id TEXT NOT NULL REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
            depends_on_task_id TEXT NOT NULL REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
            PRIMARY KEY(task_id, depends_on_task_id)
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_dependencies_parent
            ON runtime_task_dependencies(depends_on_task_id, task_id);

        CREATE TABLE IF NOT EXISTS runtime_events (
            sequence BIGSERIAL PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            root_run_id TEXT,
            parent_run_id TEXT,
            parent_task_id TEXT,
            user_id TEXT,
            session_id TEXT,
            agent_id TEXT,
            turn_id TEXT,
            span_id TEXT,
            parent_span_id TEXT,
            tool_call_id TEXT,
            attempt INTEGER,
            phase TEXT,
            status TEXT,
            visibility TEXT NOT NULL DEFAULT 'public',
            summary TEXT,
            worker_id TEXT,
            lease_version BIGINT,
            schema_version INTEGER NOT NULL DEFAULT 2,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_events_run_sequence
            ON runtime_events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS ix_runtime_events_trace
            ON runtime_events(root_run_id, parent_run_id, task_id, turn_id, tool_call_id);
        CREATE INDEX IF NOT EXISTS ix_runtime_events_public
            ON runtime_events(run_id, sequence) WHERE visibility = 'public';

        CREATE TABLE IF NOT EXISTS runtime_logs (
            sequence BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            worker_id TEXT,
            level TEXT NOT NULL,
            stage TEXT NOT NULL,
            message TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_logs_run_sequence
            ON runtime_logs(run_id, sequence);
        CREATE INDEX IF NOT EXISTS ix_runtime_logs_task_sequence
            ON runtime_logs(task_id, sequence) WHERE task_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_runtime_logs_data
            ON runtime_logs USING GIN(data jsonb_path_ops);

        CREATE TABLE IF NOT EXISTS runtime_artifacts (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            content JSONB,
            uri TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_artifacts_run
            ON runtime_artifacts(run_id, created_at);

        CREATE TABLE IF NOT EXISTS runtime_workers (
            worker_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'online',
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            last_heartbeat TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_workers_heartbeat
            ON runtime_workers(last_heartbeat DESC);

        CREATE TABLE IF NOT EXISTS conversation_sessions (
            storage_key TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            namespace TEXT NOT NULL,
            state JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_conversation_sessions_namespace_updated
            ON conversation_sessions(namespace, updated_at DESC);

        CREATE TABLE IF NOT EXISTS request_trace_events (
            sequence BIGSERIAL PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            tracker_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            parent_request_id TEXT,
            user_id TEXT,
            run_id TEXT,
            transport TEXT NOT NULL,
            direction TEXT NOT NULL,
            operation TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_request_trace_tracker_sequence
            ON request_trace_events(tracker_id, sequence);
        CREATE INDEX IF NOT EXISTS ix_request_trace_request_sequence
            ON request_trace_events(request_id, sequence);
        CREATE INDEX IF NOT EXISTS ix_request_trace_user_created
            ON request_trace_events(user_id, created_at DESC);
        """
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341907,))
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
                           version INTEGER PRIMARY KEY,
                           description TEXT NOT NULL,
                           applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                       )"""
                )
                current = conn.execute(
                    "SELECT 1 AS ready FROM runtime_schema_migrations WHERE version=3"
                ).fetchone()
                if current is None and destructive_migrate_enabled():
                    _logger.warning(
                        "JOYHOUSEBOT_DESTRUCTIVE_MIGRATE=1: dropping legacy runtime "
                        "tables before re-creating them"
                    )
                    conn.execute("DROP TABLE IF EXISTS runtime_task_dependencies CASCADE")
                    conn.execute("DROP TABLE IF EXISTS runtime_events CASCADE")
                    conn.execute("DROP TABLE IF EXISTS runtime_logs CASCADE")
                    conn.execute("DROP TABLE IF EXISTS runtime_artifacts CASCADE")
                    conn.execute("DROP TABLE IF EXISTS runtime_tasks CASCADE")
                    conn.execute("DROP TABLE IF EXISTS runtime_runs CASCADE")
                # Incremental migration only: backfill missing columns on legacy
                # tables so the idempotent DDL above never requires data loss.
                self._migrate_runtime_columns(conn)
                conn.execute(ddl)
                conn.execute(
                    """INSERT INTO runtime_schema_migrations(version,description)
                       VALUES (3,'observable multi-agent event envelope and projections')
                       ON CONFLICT(version) DO NOTHING"""
                )

    def _migrate_runtime_columns(self, conn: Any) -> None:
        """Backfill columns on pre-v3 tables in place; never drops data."""

        def table_exists(name: str) -> bool:
            row = conn.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()
            return bool(row and row["t"])

        if table_exists("runtime_runs"):
            run_columns = {
                "user_id": "TEXT NOT NULL DEFAULT ''",
                "session_id": "TEXT NOT NULL DEFAULT ''",
                "agent_id": "TEXT NOT NULL DEFAULT 'default'",
                "kind": "TEXT NOT NULL DEFAULT 'agent'",
                "status": "TEXT NOT NULL DEFAULT 'queued'",
                "prompt": "TEXT NOT NULL DEFAULT ''",
                "options": "JSONB NOT NULL DEFAULT '{}'::jsonb",
                "result": "JSONB",
                "error": "JSONB",
                "idempotency_key": "TEXT",
                "created_at": "TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()",
                "started_at": "TIMESTAMPTZ",
                "finished_at": "TIMESTAMPTZ",
                "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()",
                "lease_owner": "TEXT",
                "lease_expires_at": "TIMESTAMPTZ",
                "lease_version": "BIGINT NOT NULL DEFAULT 0",
                "root_run_id": "TEXT",
                "parent_run_id": "TEXT",
                "parent_task_id": "TEXT",
                "current_phase": "TEXT",
                "status_summary": "TEXT",
                "status_reason": "TEXT",
                "next_action": "TEXT",
                "waiting_on": "TEXT",
                "active_turn_id": "TEXT",
                "active_span_count": "INTEGER NOT NULL DEFAULT 0",
                "completed_task_count": "INTEGER NOT NULL DEFAULT 0",
                "total_task_count": "INTEGER NOT NULL DEFAULT 0",
                "last_event_sequence": "BIGINT NOT NULL DEFAULT 0",
                "last_progress_at": "TIMESTAMPTZ",
            }
            for name, definition in run_columns.items():
                conn.execute(
                    f"ALTER TABLE runtime_runs ADD COLUMN IF NOT EXISTS {name} {definition}"
                )
        if table_exists("runtime_tasks"):
            conn.execute(
                "ALTER TABLE runtime_tasks ADD COLUMN IF NOT EXISTS agent_id TEXT NOT NULL DEFAULT 'default'"
            )
        if table_exists("runtime_events"):
            event_columns = {
                "root_run_id": "TEXT",
                "parent_run_id": "TEXT",
                "parent_task_id": "TEXT",
                "user_id": "TEXT",
                "session_id": "TEXT",
                "agent_id": "TEXT",
                "turn_id": "TEXT",
                "span_id": "TEXT",
                "parent_span_id": "TEXT",
                "tool_call_id": "TEXT",
                "attempt": "INTEGER",
                "phase": "TEXT",
                "status": "TEXT",
                "visibility": "TEXT NOT NULL DEFAULT 'public'",
                "summary": "TEXT",
                "worker_id": "TEXT",
                "lease_version": "BIGINT",
                "schema_version": "INTEGER NOT NULL DEFAULT 2",
            }
            for name, definition in event_columns.items():
                conn.execute(
                    f"ALTER TABLE runtime_events ADD COLUMN IF NOT EXISTS {name} {definition}"
                )

    @staticmethod
    def _run(row: dict[str, Any]) -> RuntimeRunRecord:
        return RuntimeRunRecord(
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            agent_id=str(row["agent_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            prompt=str(row["prompt"]),
            options=dict(_json(row["options"], {})),
            result=_json(row["result"]),
            error=_json(row["error"]),
            idempotency_key=row["idempotency_key"],
            created_at=_iso(row["created_at"]) or "",
            started_at=_iso(row["started_at"]),
            finished_at=_iso(row["finished_at"]),
            updated_at=_iso(row["updated_at"]) or "",
            lease_owner=row["lease_owner"],
            lease_expires_at=_iso(row["lease_expires_at"]),
            lease_version=int(row["lease_version"] or 0),
            root_run_id=row.get("root_run_id"),
            parent_run_id=row.get("parent_run_id"),
            parent_task_id=row.get("parent_task_id"),
            current_phase=row.get("current_phase"),
            status_summary=row.get("status_summary"),
            status_reason=row.get("status_reason"),
            next_action=row.get("next_action"),
            waiting_on=row.get("waiting_on"),
            active_turn_id=row.get("active_turn_id"),
            active_span_count=int(row.get("active_span_count") or 0),
            completed_task_count=int(row.get("completed_task_count") or 0),
            total_task_count=int(row.get("total_task_count") or 0),
            last_event_sequence=int(row.get("last_event_sequence") or 0),
            last_progress_at=_iso(row.get("last_progress_at")),
        )

    @staticmethod
    def _task(row: dict[str, Any]) -> RuntimeTaskRecord:
        return RuntimeTaskRecord(
            task_id=str(row["task_id"]),
            run_id=str(row["run_id"]),
            agent_id=str(row["agent_id"]),
            parent_task_id=row["parent_task_id"],
            name=str(row["name"]),
            status=str(row["status"]),
            payload=dict(_json(row["payload"], {})),
            result=_json(row["result"]),
            error=_json(row["error"]),
            priority=int(row["priority"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            available_at=_iso(row["available_at"]) or "",
            lease_owner=row["lease_owner"],
            lease_expires_at=_iso(row["lease_expires_at"]),
            created_at=_iso(row["created_at"]) or "",
            started_at=_iso(row["started_at"]),
            finished_at=_iso(row["finished_at"]),
            updated_at=_iso(row["updated_at"]) or "",
            lease_version=int(row["lease_version"] or 0),
        )

    @staticmethod
    def _notify(conn: Any, run_id: str | None = None) -> None:
        conn.execute("SELECT pg_notify(%s, %s)", (_CHANNEL, run_id or "*"))

    @staticmethod
    def _audit(
        conn: Any,
        *,
        run_id: str,
        stage: str,
        message: str,
        task_id: str | None = None,
        worker_id: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Write a state-machine audit record in the caller's transaction."""
        conn.execute(
            """INSERT INTO runtime_logs
                   (run_id,task_id,worker_id,level,stage,message,data)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, task_id, worker_id, level, stage, message, Jsonb(data or {})),
        )
