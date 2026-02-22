"""SQLite-backed local state for house identity and tasks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from joyhousebot.utils.helpers import ensure_dir

TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class IdentityRecord:
    identity_public_key: str | None  # Ed25519 public key hex (house auth identity)
    house_id: str | None  # registered house id
    registered_at: str | None
    status: str
    access_token: str | None
    refresh_token: str | None
    ws_url: str | None
    server_url: str | None


@dataclass
class TaskRecord:
    task_id: str
    source: str
    task_type: str
    task_version: str
    payload: dict[str, Any]
    status: TaskStatus
    priority: int
    retry_count: int
    next_retry_at: str | None
    created_at: str
    updated_at: str
    error: dict[str, Any] | None


class LocalStateStore:
    """Local SQLite store for offline-first house operation."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        ensure_dir(db_path.parent)
        self._init_db()

    @classmethod
    def default(cls) -> "LocalStateStore":
        return cls(Path.home() / ".joyhousebot" / "state" / "house.db")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS house_identity (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    identity_public_key TEXT,
                    house_id TEXT,
                    registered_at TEXT,
                    status TEXT NOT NULL DEFAULT 'local_only',
                    access_token TEXT,
                    refresh_token TEXT,
                    ws_url TEXT,
                    server_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_queue (
                    task_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL CHECK (source IN ('cloud', 'local')),
                    task_type TEXT NOT NULL,
                    task_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_task_queue_status_priority
                    ON task_queue(status, priority, created_at);

                CREATE TABLE IF NOT EXISTS task_execution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES task_queue(task_id)
                );

                CREATE TABLE IF NOT EXISTS sync_cursor (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skills_cache (
                    skill_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    path TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    installed_at TEXT NOT NULL,
                    PRIMARY KEY(skill_name, version)
                );

                CREATE TABLE IF NOT EXISTS wallet (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    address TEXT NOT NULL,
                    encrypted_blob TEXT NOT NULL,
                    chain TEXT NOT NULL DEFAULT 'evm',
                    chain_id INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    encrypted_blob TEXT NOT NULL,
                    chain TEXT NOT NULL DEFAULT 'evm',
                    chain_id INTEGER NOT NULL DEFAULT 1,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_traces (
                    trace_id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER,
                    error_text TEXT,
                    steps_json TEXT NOT NULL,
                    tools_used TEXT NOT NULL,
                    message_preview TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_traces_session_started
                    ON agent_traces(session_key, started_at_ms DESC);
                """
            )
            self._migrate_identity_columns(conn)
            self._migrate_wallet_to_wallets(conn)

    def _migrate_identity_columns(self, conn: sqlite3.Connection) -> None:
        """Add newly introduced columns for existing local databases."""
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(house_identity)").fetchall()
        }
        add_columns = [
            ("access_token", "TEXT"),
            ("refresh_token", "TEXT"),
            ("ws_url", "TEXT"),
            ("server_url", "TEXT"),
            ("identity_public_key", "TEXT"),
        ]
        for name, type_def in add_columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE house_identity ADD COLUMN {name} {type_def}")

    def _migrate_wallet_to_wallets(self, conn: sqlite3.Connection) -> None:
        """Migrate single wallet row to wallets table if any."""
        row = conn.execute(
            "SELECT address, encrypted_blob, chain, chain_id, created_at, updated_at FROM wallet WHERE id = 1"
        ).fetchone()
        if not row:
            return
        if conn.execute("SELECT 1 FROM wallets LIMIT 1").fetchone():
            return
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO wallets (address, encrypted_blob, chain, chain_id, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (row["address"], row["encrypted_blob"], row["chain"], row["chain_id"], now, now),
        )

    def upsert_identity(
        self,
        *,
        identity_public_key: str | None = None,
        house_id: str | None = None,
        status: str = "local_only",
        access_token: str | None = None,
        refresh_token: str | None = None,
        ws_url: str | None = None,
        server_url: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO house_identity (
                    id, identity_public_key, house_id, registered_at, status,
                    access_token, refresh_token, ws_url, server_url, created_at, updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    identity_public_key=COALESCE(excluded.identity_public_key, house_identity.identity_public_key),
                    house_id=excluded.house_id,
                    registered_at=excluded.registered_at,
                    status=excluded.status,
                    access_token=COALESCE(excluded.access_token, house_identity.access_token),
                    refresh_token=COALESCE(excluded.refresh_token, house_identity.refresh_token),
                    ws_url=COALESCE(excluded.ws_url, house_identity.ws_url),
                    server_url=COALESCE(excluded.server_url, house_identity.server_url),
                    updated_at=excluded.updated_at
                """,
                (
                    identity_public_key,
                    house_id,
                    now if house_id else None,
                    status,
                    access_token,
                    refresh_token,
                    ws_url,
                    server_url,
                    now,
                    now,
                ),
            )

    def get_identity(self) -> IdentityRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT identity_public_key, house_id, registered_at, status,
                       access_token, refresh_token, ws_url, server_url
                FROM house_identity WHERE id = 1
                """
            ).fetchone()
            if not row:
                return None
            identity_pk = row["identity_public_key"] if "identity_public_key" in row.keys() else None
            return IdentityRecord(
                identity_public_key=identity_pk,
                house_id=row["house_id"],
                registered_at=row["registered_at"],
                status=str(row["status"]),
                access_token=row["access_token"],
                refresh_token=row["refresh_token"],
                ws_url=row["ws_url"],
                server_url=row["server_url"],
            )

    def enqueue_task(
        self,
        *,
        task_id: str,
        source: str,
        task_type: str,
        task_version: str,
        payload: dict[str, Any],
        priority: int = 100,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_queue (
                    task_id, source, task_type, task_version, payload_json, status,
                    priority, retry_count, next_retry_at, error_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    priority=excluded.priority,
                    updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    source,
                    task_type,
                    task_version,
                    json.dumps(payload, ensure_ascii=False),
                    "queued",
                    priority,
                    0,
                    None,
                    None,
                    now,
                    now,
                ),
            )
        self.log_task_event(task_id=task_id, event="enqueued", detail={"source": source})

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id, source, task_type, task_version, payload_json, status,
                       priority, retry_count, next_retry_at, error_json, created_at, updated_at
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return TaskRecord(
            task_id=str(row["task_id"]),
            source=str(row["source"]),
            task_type=str(row["task_type"]),
            task_version=str(row["task_version"]),
            payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
            status=str(row["status"]),
            priority=int(row["priority"]),
            retry_count=int(row["retry_count"]),
            next_retry_at=row["next_retry_at"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            error=json.loads(row["error_json"]) if row["error_json"] else None,
        )

    def pop_next_queued_task(self) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id
                FROM task_queue
                WHERE status = 'queued'
                  AND (next_retry_at IS NULL OR julianday(next_retry_at) <= julianday('now'))
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            task_id = str(row["task_id"])
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'running', updated_at = ?
                WHERE task_id = ?
                """,
                (_utc_now(), task_id),
            )
        self.log_task_event(task_id=task_id, event="claimed", detail={"status": "running"})
        return self.get_task(task_id)

    def update_task_status(
        self,
        *,
        task_id: str,
        status: TaskStatus,
        error: dict[str, Any] | None = None,
        retry_increment: int = 0,
        next_retry_at: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET status = ?,
                    error_json = ?,
                    retry_count = retry_count + ?,
                    next_retry_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    status,
                    json.dumps(error, ensure_ascii=False) if error else None,
                    retry_increment,
                    next_retry_at,
                    now,
                    task_id,
                ),
            )
        detail = {"status": status}
        if error:
            detail["error"] = error
        self.log_task_event(task_id=task_id, event="status_change", detail=detail)

    def list_tasks(self, limit: int = 50, status: str | None = None) -> list[TaskRecord]:
        query = """
            SELECT task_id, source, task_type, task_version, payload_json, status,
                   priority, retry_count, next_retry_at, error_json, created_at, updated_at
            FROM task_queue
        """
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY priority ASC, created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        tasks: list[TaskRecord] = []
        for row in rows:
            tasks.append(
                TaskRecord(
                    task_id=str(row["task_id"]),
                    source=str(row["source"]),
                    task_type=str(row["task_type"]),
                    task_version=str(row["task_version"]),
                    payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
                    status=str(row["status"]),
                    priority=int(row["priority"]),
                    retry_count=int(row["retry_count"]),
                    next_retry_at=row["next_retry_at"],
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                    error=json.loads(row["error_json"]) if row["error_json"] else None,
                )
            )
        return tasks

    def requeue_with_backoff(
        self,
        *,
        task_id: str,
        retry_increment: int,
        delay_seconds: int,
        error: dict[str, Any],
    ) -> None:
        next_retry = (datetime.now(timezone.utc) + timedelta(seconds=max(1, delay_seconds))).isoformat()
        self.update_task_status(
            task_id=task_id,
            status="queued",
            error=error,
            retry_increment=retry_increment,
            next_retry_at=next_retry,
        )
        self.log_task_event(
            task_id=task_id,
            event="retry_scheduled",
            detail={"next_retry_at": next_retry, "delay_seconds": delay_seconds},
        )

    def log_task_event(self, *, task_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_execution_log (task_id, event, detail_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    task_id,
                    event,
                    json.dumps(detail, ensure_ascii=False) if detail else None,
                    _utc_now(),
                ),
            )

    def list_task_events(self, *, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, event, detail_json, created_at
                FROM task_execution_log
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (task_id, max(1, limit)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": int(row["id"]),
                    "task_id": str(row["task_id"]),
                    "event": str(row["event"]),
                    "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
                    "created_at": str(row["created_at"]),
                }
            )
        return result

    def tail_task_events(self, *, cursor: int | None = None, limit: int = 200) -> dict[str, Any]:
        """
        Tail task execution logs across all tasks.

        Args:
            cursor: Last seen log id; only rows with id > cursor are returned.
            limit: Max rows to return.

        Returns:
            Dict with lines, next cursor, and total size hint.
        """
        limit = max(1, min(limit, 2000))
        with self._connect() as conn:
            if cursor is None:
                rows = conn.execute(
                    """
                    SELECT id, task_id, event, detail_json, created_at
                    FROM task_execution_log
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    """
                    SELECT id, task_id, event, detail_json, created_at
                    FROM task_execution_log
                    WHERE id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (int(cursor), limit),
                ).fetchall()
        lines: list[str] = []
        next_cursor = int(cursor or 0)
        for row in rows:
            next_cursor = max(next_cursor, int(row["id"]))
            payload = {
                "time": row["created_at"],
                "task_id": row["task_id"],
                "event": row["event"],
                "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            }
            lines.append(json.dumps(payload, ensure_ascii=False))
        return {"lines": lines, "cursor": next_cursor, "size": len(lines), "truncated": False}

    def set_sync_value(self, *, name: str, value: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_cursor (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (name, value, now),
            )

    def get_sync_value(self, *, name: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM sync_cursor WHERE name = ?",
                (name,),
            ).fetchone()
        return str(row["value"]) if row else None

    def set_sync_json(self, *, name: str, value: dict[str, Any] | list[Any]) -> None:
        self.set_sync_value(name=name, value=json.dumps(value, ensure_ascii=False))

    def get_sync_json(self, *, name: str, default: Any) -> Any:
        raw = self.get_sync_value(name=name)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    # --- Wallets (multi-wallet: address, encrypted private key, chain, chain_id, is_default) ---

    def add_wallet(
        self,
        *,
        address: str,
        encrypted_blob: str,
        chain: str = "evm",
        chain_id: int = 1,
        set_as_default: bool = False,
    ) -> int:
        now = _utc_now()
        with self._connect() as conn:
            if set_as_default:
                conn.execute("UPDATE wallets SET is_default = 0")
            conn.execute(
                """
                INSERT INTO wallets (address, encrypted_blob, chain, chain_id, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (address, encrypted_blob, chain, chain_id, 1 if set_as_default else 0, now, now),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def set_wallet(
        self,
        *,
        address: str,
        encrypted_blob: str,
        chain: str = "evm",
        chain_id: int = 1,
    ) -> None:
        """Backward compat: set single default wallet (replaces default or first)."""
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("UPDATE wallets SET is_default = 0")
            row = conn.execute("SELECT id FROM wallets LIMIT 1").fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE wallets SET address = ?, encrypted_blob = ?, chain = ?, chain_id = ?, is_default = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (address, encrypted_blob, chain, chain_id, now, row["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO wallets (address, encrypted_blob, chain, chain_id, is_default, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (address, encrypted_blob, chain, chain_id, now, now),
                )

    def get_wallet(self) -> dict[str, Any] | None:
        """Default wallet (is_default=1) or first row; for backward compat."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, address, encrypted_blob, chain, chain_id FROM wallets WHERE is_default = 1"
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT id, address, encrypted_blob, chain, chain_id FROM wallets ORDER BY id LIMIT 1"
                ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "address": str(row["address"]),
            "encrypted_blob": str(row["encrypted_blob"]),
            "chain": str(row["chain"]),
            "chain_id": int(row["chain_id"]),
        }

    def list_wallets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, address, chain, chain_id, is_default, created_at
                FROM wallets ORDER BY is_default DESC, id ASC
                """
            ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "address": str(r["address"]),
                "chain": str(r["chain"]),
                "chain_id": int(r["chain_id"]),
                "is_default": bool(r["is_default"]),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]

    def get_wallet_by_id(self, wallet_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, address, encrypted_blob, chain, chain_id, is_default FROM wallets WHERE id = ?",
                (wallet_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "address": str(row["address"]),
            "encrypted_blob": str(row["encrypted_blob"]),
            "chain": str(row["chain"]),
            "chain_id": int(row["chain_id"]),
            "is_default": bool(row["is_default"]),
        }

    def get_wallet_by_address(self, address: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, address, encrypted_blob, chain, chain_id, is_default FROM wallets WHERE address = ?",
                (address.strip(),),
            ).fetchone()
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "address": str(row["address"]),
            "encrypted_blob": str(row["encrypted_blob"]),
            "chain": str(row["chain"]),
            "chain_id": int(row["chain_id"]),
            "is_default": bool(row["is_default"]),
        }

    def set_default_wallet(self, wallet_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE wallets SET is_default = 0")
            conn.execute("UPDATE wallets SET is_default = 1 WHERE id = ?", (wallet_id,))

    def update_wallet_encrypted_blob(self, wallet_id: int, encrypted_blob: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE wallets SET encrypted_blob = ?, updated_at = ? WHERE id = ?",
                (encrypted_blob, now, wallet_id),
            )

    def delete_wallet(self) -> None:
        """Backward compat: delete default or first wallet."""
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM wallets WHERE is_default = 1").fetchone()
            if not row:
                row = conn.execute("SELECT id FROM wallets ORDER BY id LIMIT 1").fetchone()
            if row:
                conn.execute("DELETE FROM wallets WHERE id = ?", (row["id"],))

    def delete_wallet_by_id(self, wallet_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM wallets WHERE id = ?", (wallet_id,))

    # ---------- Agent traces (observability) ----------

    def insert_agent_trace(
        self,
        *,
        trace_id: str,
        session_key: str,
        status: str,
        started_at_ms: int,
        ended_at_ms: int | None = None,
        error_text: str | None = None,
        steps_json: str,
        tools_used: str,
        message_preview: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_traces
                (trace_id, session_key, status, started_at_ms, ended_at_ms, error_text,
                 steps_json, tools_used, message_preview, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    session_key,
                    status,
                    started_at_ms,
                    ended_at_ms,
                    error_text,
                    steps_json,
                    tools_used,
                    message_preview,
                    now,
                ),
            )

    def get_agent_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT trace_id, session_key, status, started_at_ms, ended_at_ms, error_text,
                       steps_json, tools_used, message_preview, updated_at
                FROM agent_traces WHERE trace_id = ?
                """,
                (trace_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "traceId": row["trace_id"],
            "sessionKey": row["session_key"],
            "status": row["status"],
            "startedAtMs": row["started_at_ms"],
            "endedAtMs": row["ended_at_ms"],
            "errorText": row["error_text"],
            "stepsJson": row["steps_json"],
            "toolsUsed": row["tools_used"],
            "messagePreview": row["message_preview"],
            "updatedAt": row["updated_at"],
        }

    def list_agent_traces(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        where_parts = []
        params: list[Any] = []
        if session_key:
            where_parts.append("session_key = ?")
            params.append(session_key)
        if cursor:
            try:
                cursor_ms = int(cursor)
                where_parts.append("started_at_ms < ?")
                params.append(cursor_ms)
            except (TypeError, ValueError):
                pass
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT trace_id, session_key, status, started_at_ms, ended_at_ms, error_text,
                       tools_used, message_preview
                FROM agent_traces {where_sql}
                ORDER BY started_at_ms DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        items = []
        for row in rows:
            items.append({
                "traceId": row["trace_id"],
                "sessionKey": row["session_key"],
                "status": row["status"],
                "startedAtMs": row["started_at_ms"],
                "endedAtMs": row["ended_at_ms"],
                "errorText": row["error_text"],
                "toolsUsed": row["tools_used"],
                "messagePreview": row["message_preview"],
            })
        next_cursor = None
        if len(items) > limit:
            next_cursor = str(items[limit - 1]["startedAtMs"])
            items = items[:limit]
        return items, next_cursor

