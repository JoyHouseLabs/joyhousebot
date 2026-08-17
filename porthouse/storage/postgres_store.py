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

from porthouse.storage.binary_objects import LocalBinaryObjectStore
from porthouse.storage.content_blobs import LocalContentBlobStore
from porthouse.storage.contracts import RuntimeStores
from porthouse.storage.json_codec import Jsonb
from porthouse.storage.postgres_repositories import PostgresRepositorySet
from porthouse.storage.runtime_store import RuntimeRunRecord, RuntimeTaskRecord

_CHANNEL = "porthouse_runtime_work"
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


class PostgresRuntimeStore:
    """Production runtime store backed by a psycopg connection pool."""

    backend_name = "postgres"

    def __getattr__(self, name: str) -> Any:
        repositories = self.__dict__.get("_repositories")
        if repositories is None:
            repositories = PostgresRepositorySet(self)
            object.__setattr__(self, "_repositories", repositories)
        return repositories.resolve(name)

    @property
    def repositories(self) -> PostgresRepositorySet:
        return self._repositories

    def runtime_stores(self) -> RuntimeStores:
        return self._repositories.runtime_stores()

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        application_name: str = "porthouse-runtime",
        auto_migrate: bool = True,
        bootstrap_model: str = "unconfigured/model",
        blob_directory: str = "",
        blob_inline_threshold_bytes: int = 65536,
        input_asset_directory: str = "~/.porthouse/input-assets",
        input_asset_max_bytes: int = 25 * 1024 * 1024,
        artifact_upload_directory: str = "~/.porthouse/artifact-uploads",
        artifact_upload_max_bytes: int = 250 * 1024 * 1024,
    ) -> None:
        if not database_url.strip():
            raise ValueError("PostgreSQL database_url is required")
        self.database_url = database_url
        self.application_name = application_name
        self.bootstrap_model = str(bootstrap_model).strip() or "unconfigured/model"
        self.blob_store = (
            LocalContentBlobStore(blob_directory) if str(blob_directory).strip() else None
        )
        self.blob_inline_threshold_bytes = max(0, int(blob_inline_threshold_bytes))
        self.input_asset_store = (
            LocalBinaryObjectStore(input_asset_directory)
            if str(input_asset_directory).strip()
            else None
        )
        self.input_asset_max_bytes = max(1, int(input_asset_max_bytes))
        self.artifact_upload_store = (
            LocalBinaryObjectStore(
                artifact_upload_directory, scheme="porthouse-artifact"
            )
            if str(artifact_upload_directory).strip()
            else None
        )
        self.artifact_upload_max_bytes = max(1, int(artifact_upload_max_bytes))
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
        self._repositories = PostgresRepositorySet(self)
        try:
            self._pool.wait(timeout=10)
            if auto_migrate:
                self._migrate_all()
        except BaseException:
            self._closed = True
            self._pool.close()
            raise

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
            graph_revision_id=row.get("graph_revision_id"),
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
