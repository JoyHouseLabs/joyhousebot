"""PostgreSQL persistence for immutable per-Turn context manifests."""

from __future__ import annotations

from typing import Any

from porthouse.storage.context_records import (
    ContextManifestEntryRecord,
    ContextManifestRecord,
)
from porthouse.storage.json_codec import Jsonb


class PostgresContextManifestStoreMixin:
    def migrate_context_manifests(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS context_manifests (
            manifest_id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL UNIQUE REFERENCES runtime_turns(turn_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            scope TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            owner_scope TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            budget_tokens INTEGER,
            budget_strategy TEXT NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            included_tokens INTEGER NOT NULL,
            excluded_tokens INTEGER NOT NULL,
            worker_id TEXT,
            run_lease_version BIGINT,
            task_lease_version BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_context_manifests_run_turn
            ON context_manifests(run_id, turn_index, created_at);

        CREATE TABLE IF NOT EXISTS context_manifest_entries (
            entry_id TEXT PRIMARY KEY,
            manifest_id TEXT NOT NULL REFERENCES context_manifests(manifest_id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            owner_scope TEXT NOT NULL,
            classification TEXT NOT NULL,
            authority TEXT NOT NULL,
            freshness TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            estimated_tokens INTEGER NOT NULL,
            priority INTEGER NOT NULL,
            included BOOLEAN NOT NULL,
            included_reason TEXT,
            excluded_reason TEXT,
            citation_id TEXT,
            redaction_policy TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            UNIQUE(manifest_id, ordinal)
        );
        CREATE INDEX IF NOT EXISTS ix_context_manifest_entries_source
            ON context_manifest_entries(manifest_id, source_kind, included);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341928,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="context_manifests",
                version=1,
                ddl=ddl,
                description="immutable source-level context manifests for model turns",
            )

    def record_context_manifest(self, **kwargs: Any) -> ContextManifestRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            if not self._owns_context_scope(conn, kwargs):
                return None
            existing = conn.execute(
                "SELECT * FROM context_manifests WHERE manifest_id=%s FOR UPDATE",
                (kwargs["manifest_id"],),
            ).fetchone()
            if existing is not None:
                self._assert_frozen_manifest(existing, kwargs)
                entries = self._context_entries(conn, kwargs["manifest_id"])
                return self._context_manifest(existing, entries)
            row = conn.execute(
                """INSERT INTO context_manifests
                       (manifest_id,turn_id,run_id,task_id,scope,turn_index,owner_scope,
                        request_hash,manifest_hash,budget_tokens,budget_strategy,
                        estimated_tokens,included_tokens,excluded_tokens,worker_id,
                        run_lease_version,task_lease_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    kwargs["manifest_id"],
                    kwargs["turn_id"],
                    kwargs["run_id"],
                    kwargs.get("task_id"),
                    kwargs["scope"],
                    int(kwargs["turn_index"]),
                    kwargs["owner_scope"],
                    kwargs["request_hash"],
                    kwargs["manifest_hash"],
                    kwargs.get("budget_tokens"),
                    kwargs["budget_strategy"],
                    int(kwargs["estimated_tokens"]),
                    int(kwargs["included_tokens"]),
                    int(kwargs["excluded_tokens"]),
                    kwargs.get("worker_id"),
                    kwargs.get("run_lease_version"),
                    kwargs.get("task_lease_version"),
                ),
            ).fetchone()
            for entry in kwargs.get("entries") or []:
                conn.execute(
                    """INSERT INTO context_manifest_entries
                           (entry_id,manifest_id,ordinal,source_kind,source_id,owner_scope,
                            classification,authority,freshness,content_hash,estimated_tokens,
                            priority,included,included_reason,excluded_reason,citation_id,
                            redaction_policy,metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        entry["entry_id"],
                        kwargs["manifest_id"],
                        int(entry["ordinal"]),
                        entry["source_kind"],
                        entry["source_id"],
                        kwargs["owner_scope"],
                        entry["classification"],
                        entry["authority"],
                        entry["freshness"],
                        entry["content_hash"],
                        int(entry["estimated_tokens"]),
                        int(entry["priority"]),
                        bool(entry["included"]),
                        entry.get("included_reason"),
                        entry.get("excluded_reason"),
                        entry.get("citation_id"),
                        entry["redaction_policy"],
                        Jsonb(entry.get("metadata") or {}),
                    ),
                )
            entries = self._context_entries(conn, kwargs["manifest_id"])
        return self._context_manifest(row, entries) if row else None

    def list_context_manifests(
        self, run_id: str, *, expected_user_id: str | None = None
    ) -> list[ContextManifestRecord]:
        clauses = ["manifest.run_id=%s"]
        params: list[Any] = [run_id]
        if expected_user_id is not None:
            clauses.append("run.user_id=%s")
            params.append(expected_user_id)
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT manifest.* FROM context_manifests manifest
                   JOIN runtime_runs run ON run.run_id=manifest.run_id WHERE """
                + " AND ".join(clauses)
                + " ORDER BY manifest.created_at,manifest.turn_index",
                tuple(params),
            ).fetchall()
            return [
                self._context_manifest(row, self._context_entries(conn, str(row["manifest_id"])))
                for row in rows
            ]

    def get_context_manifest_for_turn(
        self,
        turn_id: str,
        *,
        worker_id: str | None,
        run_lease_version: int | None,
        task_lease_version: int | None,
    ) -> ContextManifestRecord | None:
        """Read a frozen manifest only while its execution lease is still owned."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM context_manifests WHERE turn_id=%s FOR UPDATE",
                (turn_id,),
            ).fetchone()
            if row is None or not self._owns_context_scope(
                conn,
                {
                    "run_id": str(row["run_id"]),
                    "task_id": row["task_id"],
                    "worker_id": worker_id,
                    "run_lease_version": run_lease_version,
                    "task_lease_version": task_lease_version,
                },
            ):
                return None
            entries = self._context_entries(conn, str(row["manifest_id"]))
            return self._context_manifest(row, entries)

    @staticmethod
    def _owns_context_scope(conn: Any, value: dict[str, Any]) -> bool:
        if value.get("task_id") is not None:
            if value.get("task_lease_version") is None:
                row = conn.execute(
                    """SELECT 1 FROM runtime_tasks
                       WHERE task_id=%s AND run_id=%s AND status='running'
                         AND lease_owner IS NULL AND lease_version=0 FOR UPDATE""",
                    (value["task_id"], value["run_id"]),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT 1 FROM runtime_tasks
                       WHERE task_id=%s AND run_id=%s AND status='running'
                         AND lease_owner=%s AND lease_version=%s FOR UPDATE""",
                    (
                        value["task_id"],
                        value["run_id"],
                        value.get("worker_id"),
                        value.get("task_lease_version"),
                    ),
                ).fetchone()
        else:
            if value.get("run_lease_version") is None:
                row = conn.execute(
                    """SELECT 1 FROM runtime_runs
                       WHERE run_id=%s AND status IN ('planning','running')
                         AND lease_owner IS NULL AND lease_version=0 FOR UPDATE""",
                    (value["run_id"],),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT 1 FROM runtime_runs
                       WHERE run_id=%s AND status IN ('planning','running')
                         AND lease_owner=%s AND lease_version=%s FOR UPDATE""",
                    (
                        value["run_id"],
                        value.get("worker_id"),
                        value.get("run_lease_version"),
                    ),
                ).fetchone()
        return row is not None

    @staticmethod
    def _assert_frozen_manifest(row: dict[str, Any], value: dict[str, Any]) -> None:
        frozen = (
            str(row["turn_id"]) == value["turn_id"]
            and str(row["run_id"]) == value["run_id"]
            and row["task_id"] == value.get("task_id")
            and str(row["scope"]) == value["scope"]
            and int(row["turn_index"]) == int(value["turn_index"])
            and str(row["owner_scope"]) == value["owner_scope"]
            and str(row["request_hash"]) == value["request_hash"]
            and str(row["manifest_hash"]) == value["manifest_hash"]
        )
        if not frozen:
            raise RuntimeError(f"context manifest identity conflict: {row['manifest_id']}")

    @staticmethod
    def _context_entries(conn: Any, manifest_id: str) -> tuple[ContextManifestEntryRecord, ...]:
        from porthouse.storage.postgres_store import _json

        rows = conn.execute(
            """SELECT * FROM context_manifest_entries WHERE manifest_id=%s
               ORDER BY ordinal""",
            (manifest_id,),
        ).fetchall()
        return tuple(
            ContextManifestEntryRecord(
                entry_id=str(row["entry_id"]),
                ordinal=int(row["ordinal"]),
                source_kind=str(row["source_kind"]),
                source_id=str(row["source_id"]),
                owner_scope=str(row["owner_scope"]),
                classification=str(row["classification"]),
                authority=str(row["authority"]),
                freshness=str(row["freshness"]),
                content_hash=str(row["content_hash"]),
                estimated_tokens=int(row["estimated_tokens"]),
                priority=int(row["priority"]),
                included=bool(row["included"]),
                included_reason=row["included_reason"],
                excluded_reason=row["excluded_reason"],
                citation_id=row["citation_id"],
                redaction_policy=str(row["redaction_policy"]),
                metadata=dict(_json(row["metadata"], {})),
            )
            for row in rows
        )

    @staticmethod
    def _context_manifest(
        row: dict[str, Any], entries: tuple[ContextManifestEntryRecord, ...]
    ) -> ContextManifestRecord:
        from porthouse.storage.postgres_store import _iso

        return ContextManifestRecord(
            manifest_id=str(row["manifest_id"]),
            turn_id=str(row["turn_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            scope=str(row["scope"]),
            turn_index=int(row["turn_index"]),
            owner_scope=str(row["owner_scope"]),
            request_hash=str(row["request_hash"]),
            manifest_hash=str(row["manifest_hash"]),
            budget_tokens=(int(row["budget_tokens"]) if row["budget_tokens"] is not None else None),
            budget_strategy=str(row["budget_strategy"]),
            estimated_tokens=int(row["estimated_tokens"]),
            included_tokens=int(row["included_tokens"]),
            excluded_tokens=int(row["excluded_tokens"]),
            worker_id=row["worker_id"],
            run_lease_version=(
                int(row["run_lease_version"]) if row["run_lease_version"] is not None else None
            ),
            task_lease_version=(
                int(row["task_lease_version"]) if row["task_lease_version"] is not None else None
            ),
            created_at=_iso(row["created_at"]) or "",
            entries=entries,
        )
