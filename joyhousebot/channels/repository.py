"""Durable channel ownership and delivery outbox repository."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

from joyhousebot.storage.json_codec import Jsonb


class ChannelRepository:
    """Coordinate channel workers with fenced leases and a transactional outbox."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("ChannelRepository requires PostgreSQL runtime store")
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def migrate(self) -> None:
        ddl = """
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
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341912,))
                connection.execute(ddl)

    def acquire_lease(self, channel: str, *, worker_id: str, now_ms: int, lease_ms: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                    """INSERT INTO channel_leases
                       (channel_id,owner_worker_id,lease_until_ms,lease_version,updated_at_ms)
                       VALUES (%s,%s,%s,1,%s)
                       ON CONFLICT(channel_id) DO UPDATE SET
                         owner_worker_id=EXCLUDED.owner_worker_id,
                         lease_until_ms=EXCLUDED.lease_until_ms,
                         lease_version=channel_leases.lease_version+1,
                         updated_at_ms=EXCLUDED.updated_at_ms
                       WHERE channel_leases.owner_worker_id=EXCLUDED.owner_worker_id
                          OR channel_leases.lease_until_ms<=%s
                       RETURNING owner_worker_id""",
                    (channel, worker_id, now_ms + lease_ms, now_ms, now_ms),
                ).fetchone()
        return bool(row and row["owner_worker_id"] == worker_id)

    def release_owner(self, worker_id: str) -> int:
        p = "%s"
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM channel_leases WHERE owner_worker_id={p}", (worker_id,)
            )
            return cursor.rowcount

    def list_leases(self) -> dict[str, dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM channel_leases").fetchall()
        return {
            str(row["channel_id"]): {
                "owner": str(row["owner_worker_id"]),
                "until": int(row["lease_until_ms"]),
                "version": int(row["lease_version"]),
            }
            for row in rows
        }

    def enqueue(self, entry: dict[str, Any]) -> str:
        outbound_id = str(entry.get("id") or uuid.uuid4().hex)
        now_ms = int(entry["available_at_ms"])
        values = (
            outbound_id,
            entry.get("user_id"),
            entry["channel"],
            entry["chat_id"],
            entry.get("content") or "",
            entry.get("reply_to"),
            entry.get("media") or [],
            entry.get("metadata") or {},
            entry.get("request_id"),
            entry.get("tracker_id"),
            now_ms,
            now_ms,
            now_ms,
        )
        values = (*values[:6], Jsonb(values[6]), Jsonb(values[7]), *values[8:])
        query = """INSERT INTO channel_outbox
                (outbound_id,user_id,channel,chat_id,content,reply_to,media,metadata,
                 request_id,tracker_id,available_at_ms,created_at_ms,updated_at_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        with self._connection() as connection:
            connection.execute(query, values)
        return outbound_id

    def enqueue_message(self, message: Any) -> str:
        """Persist an OutboundMessage without coupling callers to SQL shape."""
        metadata = dict(getattr(message, "metadata", {}) or {})
        return self.enqueue(
            {
                "user_id": metadata.get("user_id"),
                "channel": message.channel,
                "chat_id": message.chat_id,
                "content": message.content,
                "reply_to": message.reply_to,
                "media": list(getattr(message, "media", []) or []),
                "metadata": metadata,
                "request_id": getattr(message, "request_id", None),
                "tracker_id": getattr(message, "tracker_id", None),
                "available_at_ms": int(time.time() * 1000),
            }
        )

    def claim(
        self,
        channels: Sequence[str],
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not channels:
            return []
        query = """
            WITH available AS (
                SELECT outbound_id FROM channel_outbox
                WHERE channel=ANY(%s) AND available_at_ms<=%s
                  AND (status='pending' OR (status='sending' AND lease_until_ms<=%s))
                ORDER BY available_at_ms,outbound_id
                FOR UPDATE SKIP LOCKED LIMIT %s
            )
            UPDATE channel_outbox o SET status='sending',lease_owner=%s,
                lease_until_ms=%s,lease_version=o.lease_version+1,updated_at_ms=%s
            FROM available WHERE o.outbound_id=available.outbound_id RETURNING o.*
            """
        with self._connection() as connection:
            rows = connection.execute(
                    query,
                    (list(channels), now_ms, now_ms, limit, worker_id, now_ms + lease_ms, now_ms),
                ).fetchall()
        return [self._entry(row) for row in rows]

    def finish(
        self,
        outbound_id: str,
        *,
        worker_id: str,
        lease_version: int,
        success: bool,
        error: str | None,
        max_attempts: int,
        now_ms: int,
    ) -> tuple[str, int] | None:
        p = "%s"
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT * FROM channel_outbox WHERE outbound_id={p} FOR UPDATE",
                (outbound_id,),
            ).fetchone()
            if (
                not row
                or row["lease_owner"] != worker_id
                or row["status"] != "sending"
                or int(row["lease_version"]) != lease_version
            ):
                return None
            attempt = int(row["attempt"]) + (0 if success else 1)
            status = "sent" if success else ("dead" if attempt >= max_attempts else "pending")
            delivery_values = (
                uuid.uuid4().hex,
                outbound_id,
                row["channel"],
                status,
                attempt,
                worker_id,
                error,
                now_ms,
            )
            connection.execute(
                """INSERT INTO channel_deliveries
                   (delivery_id,outbound_id,channel,status,attempt,worker_id,error,created_at_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                delivery_values,
            )
            if success:
                connection.execute(
                    f"DELETE FROM channel_outbox WHERE outbound_id={p}", (outbound_id,)
                )
            elif status == "dead":
                connection.execute(
                    f"""UPDATE channel_outbox SET status='dead',attempt={p},last_error={p},
                        lease_owner=NULL,lease_until_ms=NULL,updated_at_ms={p}
                        WHERE outbound_id={p}""",
                    (attempt, error, now_ms, outbound_id),
                )
            else:
                available_at_ms = now_ms + min(300_000, 1000 * 2 ** min(attempt, 8))
                connection.execute(
                    f"""UPDATE channel_outbox SET status='pending',attempt={p},last_error={p},
                        available_at_ms={p},lease_owner=NULL,lease_until_ms=NULL,updated_at_ms={p}
                        WHERE outbound_id={p}""",
                    (attempt, error, available_at_ms, now_ms, outbound_id),
                )
        return status, attempt

    def status_counts(self) -> dict[str, dict[str, int]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT channel,status,COUNT(*) AS count FROM channel_outbox
                   GROUP BY channel,status"""
            ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            counts = result.setdefault(str(row["channel"]), {"pending": 0, "sending": 0, "dead": 0})
            status = str(row["status"])
            if status in counts:
                counts[status] = int(row["count"])
        return result

    def outbox_size(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM channel_outbox").fetchone()
        return int(row["count"])

    def _entry(self, row: Any) -> dict[str, Any]:
        media = row["media"]
        metadata = row["metadata"]
        return {
            "id": str(row["outbound_id"]),
            "user_id": row["user_id"],
            "channel": str(row["channel"]),
            "chat_id": str(row["chat_id"]),
            "content": str(row["content"]),
            "reply_to": row["reply_to"],
            "media": json.loads(media) if isinstance(media, str) else list(media or []),
            "metadata": json.loads(metadata) if isinstance(metadata, str) else dict(metadata or {}),
            "request_id": row["request_id"],
            "tracker_id": row["tracker_id"],
            "attempt": int(row["attempt"]),
            "lease_version": int(row["lease_version"]),
        }
