"""PostgreSQL runtime-data retention: purge coverage, diagnostics TTL, tombstones."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from psycopg.errors import DeadlockDetected, LockNotAvailable

from joyhousebot.storage.postgres_locks import (
    RUNTIME_PURGE_LOCK_ID,
    SCHEMA_MIGRATION_LOCK_ID,
)

# Model-trace tables form one diagnostics cluster and share the diagnostics
# retention cutoff.  Delete order respects the FK chain
# model_reasoning_segments -> model_invocations -> execution_spans.
_DIAGNOSTICS_TABLES = (
    ("model_reasoning_segments", "created_at"),
    ("model_invocations", "started_at"),
)
# Operational telemetry keeps the global retention cutoff.
_TELEMETRY_TABLES = (
    ("model_response_cache", "created_at"),
    ("replay_runs", "created_at"),
    ("runtime_artifacts", "created_at"),
    ("runtime_events", "created_at"),
    ("runtime_logs", "created_at"),
    ("request_trace_events", "created_at"),
)
_CONTENT_BLOB_GC_GRACE_SECONDS = 24 * 60 * 60


class PostgresMaintenanceStoreMixin:
    @staticmethod
    def _acquire_purge_locks(conn: Any) -> bool:
        for lock_id in (SCHEMA_MIGRATION_LOCK_ID, RUNTIME_PURGE_LOCK_ID):
            row = conn.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (lock_id,)
            ).fetchone()
            if not row or not row["acquired"]:
                return False
        conn.execute("SET LOCAL lock_timeout = '2s'")
        return True

    @staticmethod
    def _purge_diagnostics(
        conn: Any, diagnostics_cutoff: datetime, counts: dict[str, int]
    ) -> None:
        for table, timestamp_column in _DIAGNOSTICS_TABLES:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {timestamp_column} < %s",
                (diagnostics_cutoff,),
            )
            counts[table] = max(0, cursor.rowcount)
        cursor = conn.execute(
            """DELETE FROM execution_spans WHERE started_at < %s
               AND NOT EXISTS (
                   SELECT 1 FROM model_invocations mi
                   WHERE mi.span_id = execution_spans.span_id)""",
            (diagnostics_cutoff,),
        )
        counts["execution_spans"] = max(0, cursor.rowcount)
        cursor = conn.execute(
            """DELETE FROM trace_blobs
               WHERE expires_at IS NOT NULL AND expires_at < clock_timestamp()"""
        )
        expired_blobs = max(0, cursor.rowcount)
        cursor = conn.execute(
            "DELETE FROM trace_blobs WHERE created_at < %s", (diagnostics_cutoff,)
        )
        counts["trace_blobs"] = expired_blobs + max(0, cursor.rowcount)

    @staticmethod
    def _purge_callbacks(conn: Any, cutoff: datetime, counts: dict[str, int]) -> None:
        cursor = conn.execute(
            "DELETE FROM app_callback_delivery_events WHERE created_at < %s", (cutoff,)
        )
        counts["app_callback_delivery_events"] = max(0, cursor.rowcount)
        cursor = conn.execute(
            """DELETE FROM app_callback_outbox WHERE created_at < %s
               AND status IN ('sent','dead')""",
            (cutoff,),
        )
        counts["app_callback_outbox"] = max(0, cursor.rowcount)

    @staticmethod
    def _purge_telemetry(conn: Any, cutoff: datetime, counts: dict[str, int]) -> None:
        cursor = conn.execute(
            """UPDATE runtime_runs SET options = jsonb_set(
                   options, '{metadata}',
                   COALESCE(options->'metadata', '{}'::jsonb)
                       || '{"events_purged": true}'::jsonb,
                   true)
               WHERE NOT COALESCE(
                   (options->'metadata'->>'events_purged')::boolean, FALSE)
                 AND (run_id IN (
                     SELECT DISTINCT run_id FROM runtime_events WHERE created_at < %s)
                  OR run_id IN (
                     SELECT DISTINCT run_id FROM runtime_logs WHERE created_at < %s))""",
            (cutoff, cutoff),
        )
        counts["runtime_runs_tombstoned"] = max(0, cursor.rowcount)
        for table, timestamp_column in _TELEMETRY_TABLES:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {timestamp_column} < %s", (cutoff,)
            )
            counts[table] = max(0, cursor.rowcount)

    @staticmethod
    def _purge_assets(
        conn: Any, cutoff: datetime, older_than_ms: int, counts: dict[str, int]
    ) -> None:
        cursor = conn.execute(
            """UPDATE runtime_input_assets AS asset
               SET status='deleted', deleted_at=clock_timestamp()
               WHERE asset.status='ready' AND asset.created_at < %s
                 AND NOT EXISTS (
                     SELECT 1 FROM runtime_run_input_assets binding
                     JOIN runtime_runs run ON run.run_id=binding.run_id
                     WHERE binding.asset_id=asset.asset_id
                       AND run.status NOT IN ('completed','failed','cancelled','timed_out'))""",
            (cutoff,),
        )
        counts["runtime_input_assets"] = max(0, cursor.rowcount)
        cursor = conn.execute(
            """DELETE FROM capability_invocations
               WHERE created_at < %s AND finished_at IS NOT NULL""",
            (cutoff,),
        )
        counts["capability_invocations"] = max(0, cursor.rowcount)
        occurrences = conn.execute(
            "SELECT to_regclass('public.schedule_occurrences') AS name"
        ).fetchone()
        if not occurrences or not occurrences["name"]:
            counts["schedule_occurrences"] = 0
            return
        cursor = conn.execute(
            """DELETE FROM schedule_occurrences
               WHERE finished_at_ms IS NOT NULL AND finished_at_ms < %s""",
            (older_than_ms,),
        )
        counts["schedule_occurrences"] = max(0, cursor.rowcount)

    def _referenced_objects(self, conn: Any) -> tuple[set[str], set[str]]:
        blob_uris: set[str] = set()
        input_uris: set[str] = set()
        if getattr(self, "blob_store", None) is not None:
            for query in (
                "SELECT storage_uri AS uri FROM trace_blobs "
                "WHERE storage_uri LIKE 'joyhousebot-blob://sha256/%'",
                "SELECT uri FROM runtime_artifacts "
                "WHERE uri LIKE 'joyhousebot-blob://sha256/%'",
                "SELECT uri FROM work_versions "
                "WHERE uri LIKE 'joyhousebot-blob://sha256/%'",
            ):
                blob_uris.update(
                    str(row["uri"])
                    for row in conn.execute(query).fetchall()
                    if row["uri"]
                )
        if getattr(self, "input_asset_store", None) is not None:
            input_uris.update(
                str(row["storage_uri"])
                for row in conn.execute(
                    "SELECT storage_uri FROM runtime_input_assets WHERE status='ready'"
                ).fetchall()
                if row["storage_uri"]
            )
        return blob_uris, input_uris

    def _prune_objects(
        self,
        counts: dict[str, int],
        blob_uris: set[str],
        input_uris: set[str],
    ) -> None:
        blob_store = getattr(self, "blob_store", None)
        if blob_store is not None and hasattr(blob_store, "prune_unreferenced"):
            counts["content_blobs"] = blob_store.prune_unreferenced(
                blob_uris, min_unreferenced_seconds=_CONTENT_BLOB_GC_GRACE_SECONDS
            )
        input_store = getattr(self, "input_asset_store", None)
        if input_store is not None and hasattr(input_store, "prune_unreferenced"):
            counts["input_asset_objects"] = input_store.prune_unreferenced(
                input_uris, min_unreferenced_seconds=_CONTENT_BLOB_GC_GRACE_SECONDS
            )

    def _purge_runtime_data_once(
        self,
        *,
        cutoff: datetime,
        diagnostics_cutoff: datetime,
        older_than_ms: int,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._pool.connection() as conn, conn.transaction():
            if not self._acquire_purge_locks(conn):
                return {}
            self._purge_diagnostics(conn, diagnostics_cutoff, counts)
            self._purge_callbacks(conn, cutoff, counts)
            self._purge_telemetry(conn, cutoff, counts)
            self._purge_assets(conn, cutoff, older_than_ms, counts)
            blob_uris, input_uris = self._referenced_objects(conn)
        self._prune_objects(counts, blob_uris, input_uris)
        return counts

    async def purge_old_runtime_data(
        self, older_than_ms: int, diagnostics_older_than_ms: int | None = None
    ) -> dict[str, int]:
        """Delete expired runtime rows under cluster-wide maintenance locks.

        ``older_than_ms`` bounds operational telemetry; diagnostics (model
        traces) use ``diagnostics_older_than_ms`` so their retention can be
        configured independently.  Runs whose events/logs are purged are
        tombstoned (``options.metadata.events_purged``) so SSE replay can
        signal the gap instead of silently missing events.
        """
        cutoff = datetime.fromtimestamp(older_than_ms / 1000, tz=timezone.utc)
        diagnostics_cutoff = datetime.fromtimestamp(
            (diagnostics_older_than_ms if diagnostics_older_than_ms is not None else older_than_ms)
            / 1000,
            tz=timezone.utc,
        )
        for attempt in range(3):
            try:
                return self._purge_runtime_data_once(
                    cutoff=cutoff,
                    diagnostics_cutoff=diagnostics_cutoff,
                    older_than_ms=older_than_ms,
                )
            except (DeadlockDetected, LockNotAvailable):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (2**attempt))
        return {}
