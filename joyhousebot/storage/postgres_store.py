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

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_admins import PostgresAdminStoreMixin
from joyhousebot.storage.postgres_agents import PostgresAgentStoreMixin
from joyhousebot.storage.postgres_cancel import PostgresRunCancelMixin
from joyhousebot.storage.postgres_capabilities import PostgresCapabilityStoreMixin
from joyhousebot.storage.postgres_clarifications import PostgresClarificationStoreMixin
from joyhousebot.storage.postgres_graphs import PostgresGraphStoreMixin
from joyhousebot.storage.postgres_mcp import PostgresMCPStoreMixin
from joyhousebot.storage.postgres_migrations import PostgresMigrationMixin
from joyhousebot.storage.postgres_observability import PostgresObservabilityStoreMixin
from joyhousebot.storage.postgres_operations import PostgresOperationsStoreMixin
from joyhousebot.storage.postgres_plugins import PostgresPluginStoreMixin
from joyhousebot.storage.postgres_rate_limits import PostgresRateLimitStoreMixin
from joyhousebot.storage.postgres_rollouts import PostgresRolloutStoreMixin
from joyhousebot.storage.postgres_run_listing import PostgresRunListingStoreMixin
from joyhousebot.storage.postgres_runs import PostgresRunStoreMixin
from joyhousebot.storage.postgres_scenarios import PostgresScenarioStoreMixin
from joyhousebot.storage.postgres_tasks import PostgresTaskStoreMixin
from joyhousebot.storage.runtime_store import (
    RuntimeRunRecord,
    RuntimeTaskRecord,
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
    PostgresMigrationMixin,
    PostgresAdminStoreMixin,
    PostgresAgentStoreMixin,
    PostgresCapabilityStoreMixin,
    PostgresClarificationStoreMixin,
    PostgresScenarioStoreMixin,
    PostgresGraphStoreMixin,
    PostgresRunListingStoreMixin,
    PostgresRunStoreMixin,
    PostgresRunCancelMixin,
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
            cancel_requested_at=_iso(row.get("cancel_requested_at")),
            cancel_reason=row.get("cancel_reason"),
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
