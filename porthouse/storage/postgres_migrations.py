"""Core schema migration history and cluster-wide migration lock.

Every domain migration records ``(name, version, checksum, applied_at)`` in
``schema_migration_history`` after applying its DDL.  A checksum mismatch on
an already-recorded migration means the DDL was edited after it shipped; that
is drift, and startup fails closed instead of silently absorbing it through
``IF NOT EXISTS`` idempotency or rewriting the recorded checksum.

Extensions never receive this lock or a RuntimeStore. Business services own
their database, migration history, and deployment coordination independently.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from porthouse.storage.migration_history import migration_checksum as _migration_checksum
from porthouse.storage.migration_history import migration_is_recorded, record_migration
from porthouse.storage.postgres_locks import SCHEMA_MIGRATION_LOCK_ID
from porthouse.storage.runtime_store import destructive_migrate_enabled

_logger = logging.getLogger(__name__)

_RUNTIME_CLOSURE_V4_DDL = """
CREATE TABLE IF NOT EXISTS channel_leases (
    channel_id TEXT PRIMARY KEY,
    owner_worker_id TEXT NOT NULL,
    lease_until_ms BIGINT NOT NULL,
    lease_version BIGINT NOT NULL DEFAULT 1,
    updated_at_ms BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_channel_leases_owner
    ON channel_leases(owner_worker_id, lease_until_ms);
CREATE TABLE IF NOT EXISTS channel_outbox (
    outbound_id TEXT PRIMARY KEY,
    user_id TEXT,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    content TEXT NOT NULL,
    reply_to TEXT,
    media JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_id TEXT,
    tracker_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    available_at_ms BIGINT NOT NULL,
    lease_owner TEXT,
    lease_until_ms BIGINT,
    lease_version BIGINT NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at_ms BIGINT NOT NULL,
    updated_at_ms BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_channel_outbox_claim
    ON channel_outbox(channel, available_at_ms, outbound_id)
    WHERE status IN ('pending', 'sending');
CREATE INDEX IF NOT EXISTS ix_channel_outbox_user
    ON channel_outbox(user_id, created_at_ms DESC);
CREATE TABLE IF NOT EXISTS channel_deliveries (
    delivery_id TEXT PRIMARY KEY,
    outbound_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    worker_id TEXT NOT NULL,
    error TEXT,
    created_at_ms BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_channel_deliveries_outbound
    ON channel_deliveries(outbound_id, created_at_ms DESC);
"""

_RUNTIME_ARTIFACT_V5_DDL = """
ALTER TABLE runtime_artifacts
    ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'runtime.output',
    ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT 'create',
    ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS content_sha256 TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS object_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ix_runtime_artifacts_content_sha256
    ON runtime_artifacts(content_sha256) WHERE content_sha256<>'';
"""

_RUNTIME_QUERY_PROJECTIONS_V6_DDL = """
ALTER TABLE runtime_runs
    ADD COLUMN IF NOT EXISTS max_concurrent INTEGER
        GENERATED ALWAYS AS (
            CASE
                WHEN options->>'max_concurrent' ~ '^[1-9][0-9]*$'
                THEN (options->>'max_concurrent')::integer
                ELSE 4
            END
        ) STORED,
    ADD COLUMN IF NOT EXISTS initial_events_required BOOLEAN
        GENERATED ALWAYS AS (
            options#>>'{metadata,_runtime_initial_events_required}' = 'true'
        ) STORED,
    ADD COLUMN IF NOT EXISTS submission_ready BOOLEAN
        GENERATED ALWAYS AS (
            options#>>'{metadata,_runtime_schedule_submission_ready}' IS DISTINCT FROM 'false'
        ) STORED,
    ADD COLUMN IF NOT EXISTS app_installation_id TEXT
        GENERATED ALWAYS AS (options#>>'{metadata,app,installation_id}') STORED,
    ADD COLUMN IF NOT EXISTS app_entrypoint_id TEXT
        GENERATED ALWAYS AS (options#>>'{metadata,app,entrypoint_id}') STORED;
ALTER TABLE runtime_tasks
    ADD COLUMN IF NOT EXISTS wait_reason TEXT
        GENERATED ALWAYS AS (result->>'stop_reason') STORED,
    ADD COLUMN IF NOT EXISTS node_type TEXT
        GENERATED ALWAYS AS (COALESCE(payload->>'node_type', 'agent')) STORED,
    ADD COLUMN IF NOT EXISTS child_concurrency_limit INTEGER
        GENERATED ALWAYS AS (
            CASE
                WHEN payload->>'foreach_max_concurrent' ~ '^[1-9][0-9]*$'
                THEN (payload->>'foreach_max_concurrent')::integer
                ELSE 1
            END
        ) STORED;
CREATE INDEX IF NOT EXISTS ix_runtime_runs_app_usage
    ON runtime_runs(user_id, app_installation_id, created_at)
    WHERE app_installation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_runtime_tasks_wait_reason
    ON runtime_tasks(run_id, wait_reason)
    WHERE wait_reason IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_runtime_tasks_node_type
    ON runtime_tasks(run_id, node_type, status);
"""

_RUNTIME_QUERY_PROJECTION_DEFAULTS_V7_DDL = """
ALTER TABLE runtime_runs DROP COLUMN IF EXISTS initial_events_required;
ALTER TABLE runtime_runs
    ADD COLUMN initial_events_required BOOLEAN
        GENERATED ALWAYS AS (
            COALESCE(
                options#>>'{metadata,_runtime_initial_events_required}' = 'true',
                FALSE
            )
        ) STORED;
"""

# Development-only reset: runtime tables dropped when the destructive gate is
# explicitly enabled, in dependency-safe order.
_DESTRUCTIVE_DROP_TABLES = (
    "runtime_task_dependencies",
    "runtime_events",
    "runtime_logs",
    "runtime_artifacts",
    "runtime_tasks",
    "runtime_runs",
)


def migration_checksum(ddl: str) -> str:
    """Backward-compatible public import for migration checksum tests/tools."""
    return _migration_checksum(ddl)


class PostgresMigrationMixin:
    """Migration sequencing, history recording, and the shared DDL lock."""

    def _migrate_all(self) -> None:
        """Serialize the complete migration sequence across all processes.

        Per-domain migration locks are not enough: one process can otherwise
        create an observability index while another process is still altering
        a runtime table referenced by that index.  The cluster-wide lock runs
        on a dedicated connection so this also works when the runtime pool
        has a maximum size of one.
        """
        with self.schema_migration_lock():
            self.migrate()
            self.migrate_input_assets()
            self.migrate_graph_revisions()
            self.migrate_graph_sagas()
            self.migrate_graph_patches()
            self.migrate_evals()
            self.migrate_experiments()
            self.migrate_prompts()
            self.migrate_works()
            self.migrate_graph_event_waits()
            self.migrate_execution_loop()
            self.migrate_context_manifests()
            self.migrate_memory_candidates()
            self.migrate_event_triggers()
            self.migrate_user_workflows()
            self.migrate_loop_decisions()
            self.migrate_verifications()
            self.migrate_approvals()
            self.migrate_reconciliations()
            self.migrate_device_hosts()
            self.migrate_device_host_controls()
            self.migrate_artifact_uploads()
            self.migrate_admins()
            self.migrate_agents()
            self.migrate_agent_teams()
            self.migrate_plan_confirmations()
            self.migrate_capabilities()
            self.migrate_skills()
            self.migrate_plugins()
            self.migrate_model_providers()
            self.migrate_model_gateway()
            self.migrate_host_tools()
            self.migrate_embedding_profiles()
            self.migrate_remote_connections()
            self.migrate_scenarios()
            self.migrate_app_packs()
            self.migrate_app_delegation()
            self.migrate_app_callbacks()
            self.migrate_app_market()
            self.migrate_clarifications()
            self.migrate_rate_limits()
            self.migrate_observability()

    @contextmanager
    def schema_migration_lock(self) -> Iterator[None]:
        """Hold the Core cluster-wide schema migration advisory lock."""
        with psycopg.connect(
            self.database_url,
            autocommit=True,
            application_name=f"{self.application_name}-migration-lock",
        ) as lock_connection:
            lock_connection.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_MIGRATION_LOCK_ID,))
            try:
                yield
            finally:
                lock_connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (SCHEMA_MIGRATION_LOCK_ID,)
                )

    def _record_migration(
        self,
        conn: Any,
        *,
        name: str,
        version: int,
        ddl: str,
        description: str = "",
    ) -> None:
        record_migration(
            conn, name=name, version=version, ddl=ddl, description=description
        )

    def _migration_is_recorded(
        self,
        conn: Any,
        *,
        name: str,
        version: int,
        ddl: str,
        description: str = "",
    ) -> bool:
        """Return whether an immutable migration already exists, validating it.

        Idempotent base DDL may still run on every startup to repair legacy
        installations. Data migrations, generated-column replacements and
        backfills must not: when recorded, they are validated and skipped.
        """
        return migration_is_recorded(
            conn,
            name=name,
            version=version,
            ddl=ddl,
            description=description,
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
            cancel_requested_at TIMESTAMPTZ,
            cancel_reason TEXT,
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
                    _logger.critical(
                        "PORTHOUSE_DESTRUCTIVE_MIGRATE=DROP_ALL_TABLES: dropping "
                        "legacy runtime tables %s before re-creating them; this "
                        "destroys all runtime data and is only allowed for "
                        "development resets",
                        ", ".join(_DESTRUCTIVE_DROP_TABLES),
                    )
                    for table in _DESTRUCTIVE_DROP_TABLES:
                        conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                # Incremental migration only: backfill missing columns on legacy
                # tables so the idempotent DDL above never requires data loss.
                self._migrate_runtime_columns(conn)
                conn.execute(ddl)
                conn.execute(_RUNTIME_CLOSURE_V4_DDL)
                conn.execute(_RUNTIME_ARTIFACT_V5_DDL)
                projection_description = (
                    "indexed Runtime query projections for scheduling and App usage"
                )
                projections_recorded = self._migration_is_recorded(
                    conn,
                    name="runtime",
                    version=6,
                    ddl=_RUNTIME_QUERY_PROJECTIONS_V6_DDL,
                    description=projection_description,
                )
                defaults_description = (
                    "total boolean default for initial-event claim projection"
                )
                defaults_recorded = self._migration_is_recorded(
                    conn,
                    name="runtime",
                    version=7,
                    ddl=_RUNTIME_QUERY_PROJECTION_DEFAULTS_V7_DDL,
                    description=defaults_description,
                )
                projection_columns_ready = conn.execute(
                    """SELECT count(*)::integer AS count
                       FROM pg_attribute
                       WHERE attrelid IN ('runtime_runs'::regclass,'runtime_tasks'::regclass)
                         AND attname=ANY(%s) AND NOT attisdropped""",
                    ([
                        "max_concurrent",
                        "initial_events_required",
                        "submission_ready",
                        "app_installation_id",
                        "app_entrypoint_id",
                        "wait_reason",
                        "node_type",
                        "child_concurrency_limit",
                    ],),
                ).fetchone()["count"] == 8
                # Only re-run the additive IF NOT EXISTS migration when a
                # development reset recreated tables but preserved history.
                if not projections_recorded or not projection_columns_ready:
                    conn.execute(_RUNTIME_QUERY_PROJECTIONS_V6_DDL)
                initial_projection = conn.execute(
                    """SELECT pg_get_expr(def.adbin,def.adrelid) AS expression
                       FROM pg_attribute attr
                       JOIN pg_attrdef def
                         ON def.adrelid=attr.attrelid AND def.adnum=attr.attnum
                       WHERE attr.attrelid='runtime_runs'::regclass
                         AND attr.attname='initial_events_required'
                         AND NOT attr.attisdropped"""
                ).fetchone()
                projection_expression = str(
                    (initial_projection or {}).get("expression") or ""
                ).upper()
                defaults_ready = "COALESCE" in projection_expression
                if not defaults_recorded or not defaults_ready:
                    conn.execute(_RUNTIME_QUERY_PROJECTION_DEFAULTS_V7_DDL)
                conn.execute(
                    """INSERT INTO runtime_schema_migrations(version,description)
                       VALUES (3,'observable multi-agent event envelope and projections')
                       ON CONFLICT(version) DO NOTHING"""
                )
                self._record_migration(
                    conn,
                    name="runtime",
                    version=3,
                    ddl=ddl,
                    description=("observable multi-agent event envelope and projections"),
                )
                self._record_migration(
                    conn,
                    name="runtime",
                    version=4,
                    ddl=_RUNTIME_CLOSURE_V4_DDL,
                    description="transactional Channel outbox for terminal Runtime Runs",
                )
                self._record_migration(
                    conn,
                    name="runtime",
                    version=5,
                    ddl=_RUNTIME_ARTIFACT_V5_DDL,
                    description="immutable content-addressed Runtime Artifacts with evidence",
                )
                self._record_migration(
                    conn,
                    name="runtime",
                    version=6,
                    ddl=_RUNTIME_QUERY_PROJECTIONS_V6_DDL,
                    description=projection_description,
                )
                self._record_migration(
                    conn,
                    name="runtime",
                    version=7,
                    ddl=_RUNTIME_QUERY_PROJECTION_DEFAULTS_V7_DDL,
                    description=defaults_description,
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
                "cancel_requested_at": "TIMESTAMPTZ",
                "cancel_reason": "TEXT",
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
