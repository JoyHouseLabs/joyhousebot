"""PostgreSQL runtime-data retention: purge coverage, diagnostics TTL, tombstones."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

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


class PostgresMaintenanceStoreMixin:
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

                    for table, timestamp_column in _DIAGNOSTICS_TABLES:
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE {timestamp_column} < %s",
                            (diagnostics_cutoff,),
                        )
                        counts[table] = max(0, cursor.rowcount)
                    # A span outlives its retention only while a surviving
                    # invocation still references it.
                    cursor = conn.execute(
                        """DELETE FROM execution_spans WHERE started_at < %s
                           AND NOT EXISTS (
                               SELECT 1 FROM model_invocations mi
                               WHERE mi.span_id = execution_spans.span_id)""",
                        (diagnostics_cutoff,),
                    )
                    counts["execution_spans"] = max(0, cursor.rowcount)
                    # trace_blobs.expires_at is authoritative: expired blobs go
                    # first regardless of their created_at retention cutoff.
                    cursor = conn.execute(
                        """DELETE FROM trace_blobs
                           WHERE expires_at IS NOT NULL AND expires_at < clock_timestamp()"""
                    )
                    expired_blobs = max(0, cursor.rowcount)
                    cursor = conn.execute(
                        "DELETE FROM trace_blobs WHERE created_at < %s", (diagnostics_cutoff,)
                    )
                    counts["trace_blobs"] = expired_blobs + max(0, cursor.rowcount)

                    # Completion callback payloads contain App/Run references and
                    # must obey the same operational-data retention boundary. Never
                    # purge rows that are still eligible for delivery.
                    cursor = conn.execute(
                        "DELETE FROM app_callback_delivery_events WHERE created_at < %s",
                        (cutoff,),
                    )
                    counts["app_callback_delivery_events"] = max(0, cursor.rowcount)
                    cursor = conn.execute(
                        """DELETE FROM app_callback_outbox WHERE created_at < %s
                           AND status IN ('sent','dead')""",
                        (cutoff,),
                    )
                    counts["app_callback_outbox"] = max(0, cursor.rowcount)

                    # Tombstone runs before their events vanish so sequence
                    # replay can surface the retention gap explicitly.
                    # (jsonb_set only creates the final path step, so merge
                    # into options.metadata in one level.)
                    cursor = conn.execute(
                        """UPDATE runtime_runs SET options = jsonb_set(
                               options, '{metadata}',
                               COALESCE(options->'metadata', '{}'::jsonb)
                                   || '{"events_purged": true}'::jsonb,
                               true)
                           WHERE NOT COALESCE(
                               (options->'metadata'->>'events_purged')::boolean, FALSE)
                             AND (run_id IN (
                                 SELECT DISTINCT run_id FROM runtime_events
                                 WHERE created_at < %s)
                              OR run_id IN (
                                 SELECT DISTINCT run_id FROM runtime_logs
                                 WHERE created_at < %s))""",
                        (cutoff, cutoff),
                    )
                    counts["runtime_runs_tombstoned"] = max(0, cursor.rowcount)

                    for table, timestamp_column in _TELEMETRY_TABLES:
                        cursor = conn.execute(
                            f"DELETE FROM {table} WHERE {timestamp_column} < %s", (cutoff,)
                        )
                        counts[table] = max(0, cursor.rowcount)
                    cursor = conn.execute(
                        """DELETE FROM capability_invocations
                           WHERE created_at < %s AND finished_at IS NOT NULL""",
                        (cutoff,),
                    )
                    counts["capability_invocations"] = max(0, cursor.rowcount)
                    # schedule_occurrences uses epoch-millis columns, has no
                    # inbound foreign keys, and is created lazily by the
                    # scheduling repository.  Only finished occurrences expire.
                    occurrences = conn.execute(
                        "SELECT to_regclass('public.schedule_occurrences') AS name"
                    ).fetchone()
                    if occurrences and occurrences["name"]:
                        cursor = conn.execute(
                            """DELETE FROM schedule_occurrences
                               WHERE finished_at_ms IS NOT NULL AND finished_at_ms < %s""",
                            (older_than_ms,),
                        )
                        counts["schedule_occurrences"] = max(0, cursor.rowcount)
                    else:
                        counts["schedule_occurrences"] = 0
                return counts
            except (DeadlockDetected, LockNotAvailable):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (2**attempt))
        return {}
