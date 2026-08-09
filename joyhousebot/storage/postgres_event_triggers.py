"""Durable user-owned webhook triggers and delivery audit records."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


class PostgresEventTriggerStoreMixin:
    def migrate_event_triggers(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS event_triggers (
            trigger_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            event_type_filter TEXT NOT NULL DEFAULT '*',
            instruction TEXT NOT NULL,
            session_mode TEXT NOT NULL DEFAULT 'shared',
            session_id TEXT,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            secret_hash TEXT NOT NULL,
            secret_version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_event_triggers_user
            ON event_triggers(user_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS event_trigger_deliveries (
            delivery_id TEXT PRIMARY KEY,
            trigger_id TEXT NOT NULL REFERENCES event_triggers(trigger_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            run_id TEXT,
            error TEXT,
            received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(trigger_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_event_trigger_deliveries_user
            ON event_trigger_deliveries(user_id, received_at DESC);
        CREATE INDEX IF NOT EXISTS ix_event_trigger_deliveries_trigger
            ON event_trigger_deliveries(trigger_id, received_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="event_triggers",
                version=1,
                ddl=ddl,
                description="user-owned webhook rules and idempotent delivery audit",
            )

    @staticmethod
    def _event_trigger(row: Any) -> dict[str, Any]:
        return {
            "trigger_id": str(row["trigger_id"]),
            "user_id": str(row["user_id"]),
            "name": str(row["name"]),
            "agent_id": str(row["agent_id"]),
            "event_type_filter": str(row["event_type_filter"]),
            "instruction": str(row["instruction"]),
            "session_mode": str(row["session_mode"]),
            "session_id": row["session_id"],
            "enabled": bool(row["enabled"]),
            "secret_hash": str(row["secret_hash"]),
            "secret_version": int(row["secret_version"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    @staticmethod
    def _event_delivery(row: Any) -> dict[str, Any]:
        return {
            "delivery_id": str(row["delivery_id"]),
            "trigger_id": str(row["trigger_id"]),
            "user_id": str(row["user_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "payload_hash": str(row["payload_hash"]),
            "event_type": str(row["event_type"]),
            "status": str(row["status"]),
            "attempt": int(row["attempt"]),
            "run_id": row["run_id"],
            "error": row["error"],
            "received_at": _iso(row["received_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def create_event_trigger(self, **values: Any) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO event_triggers
                       (trigger_id,user_id,name,agent_id,event_type_filter,instruction,
                        session_mode,session_id,enabled,secret_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    values["trigger_id"],
                    values["user_id"],
                    values["name"],
                    values["agent_id"],
                    values["event_type_filter"],
                    values["instruction"],
                    values["session_mode"],
                    values.get("session_id"),
                    bool(values.get("enabled", True)),
                    values["secret_hash"],
                ),
            ).fetchone()
        return self._event_trigger(row)

    def list_event_triggers(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM event_triggers WHERE user_id=%s ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self._event_trigger(row) for row in rows]

    def get_event_trigger(
        self, trigger_id: str, *, expected_user_id: str | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM event_triggers WHERE trigger_id=%s"
        params: list[Any] = [trigger_id]
        if expected_user_id is not None:
            query += " AND user_id=%s"
            params.append(expected_user_id)
        with self._pool.connection() as conn:
            row = conn.execute(query, params).fetchone()
        return self._event_trigger(row) if row else None

    def update_event_trigger(self, **values: Any) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE event_triggers SET name=%s,agent_id=%s,event_type_filter=%s,
                       instruction=%s,session_mode=%s,session_id=%s,enabled=%s,
                       updated_at=clock_timestamp()
                   WHERE trigger_id=%s AND user_id=%s RETURNING *""",
                (
                    values["name"],
                    values["agent_id"],
                    values["event_type_filter"],
                    values["instruction"],
                    values["session_mode"],
                    values.get("session_id"),
                    bool(values["enabled"]),
                    values["trigger_id"],
                    values["user_id"],
                ),
            ).fetchone()
        return self._event_trigger(row) if row else None

    def rotate_event_trigger_secret(
        self, trigger_id: str, *, user_id: str, secret_hash: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE event_triggers SET secret_hash=%s,
                       secret_version=secret_version+1,updated_at=clock_timestamp()
                   WHERE trigger_id=%s AND user_id=%s RETURNING *""",
                (secret_hash, trigger_id, user_id),
            ).fetchone()
        return self._event_trigger(row) if row else None

    def delete_event_trigger(self, trigger_id: str, *, user_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            return (
                conn.execute(
                    "DELETE FROM event_triggers WHERE trigger_id=%s AND user_id=%s",
                    (trigger_id, user_id),
                ).rowcount
                == 1
            )

    def begin_event_trigger_delivery(self, **values: Any) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                """SELECT * FROM event_trigger_deliveries
                   WHERE trigger_id=%s AND idempotency_key=%s FOR UPDATE""",
                (values["trigger_id"], values["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                record = self._event_delivery(existing)
                if record["payload_hash"] != values["payload_hash"]:
                    return {"outcome": "conflict", "delivery": record}
                if record["status"] != "failed":
                    return {"outcome": "duplicate", "delivery": record}
                row = conn.execute(
                    """UPDATE event_trigger_deliveries SET status='processing',
                           attempt=attempt+1,error=NULL,updated_at=clock_timestamp()
                       WHERE delivery_id=%s RETURNING *""",
                    (record["delivery_id"],),
                ).fetchone()
                return {"outcome": "retry", "delivery": self._event_delivery(row)}
            row = conn.execute(
                """INSERT INTO event_trigger_deliveries
                       (delivery_id,trigger_id,user_id,idempotency_key,payload_hash,
                        event_type,status)
                   VALUES (%s,%s,%s,%s,%s,%s,'processing') RETURNING *""",
                (
                    values["delivery_id"],
                    values["trigger_id"],
                    values["user_id"],
                    values["idempotency_key"],
                    values["payload_hash"],
                    values["event_type"],
                ),
            ).fetchone()
        return {"outcome": "created", "delivery": self._event_delivery(row)}

    def complete_event_trigger_delivery(
        self, delivery_id: str, *, run_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE event_trigger_deliveries SET status='submitted',run_id=%s,
                       error=NULL,updated_at=clock_timestamp()
                   WHERE delivery_id=%s RETURNING *""",
                (run_id, delivery_id),
            ).fetchone()
        return self._event_delivery(row) if row else None

    def fail_event_trigger_delivery(
        self, delivery_id: str, *, error: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE event_trigger_deliveries SET status='failed',error=%s,
                       updated_at=clock_timestamp()
                   WHERE delivery_id=%s RETURNING *""",
                (error[:2000], delivery_id),
            ).fetchone()
        return self._event_delivery(row) if row else None

    def list_event_trigger_deliveries(
        self, *, user_id: str, trigger_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM event_trigger_deliveries WHERE user_id=%s"
        params: list[Any] = [user_id]
        if trigger_id:
            query += " AND trigger_id=%s"
            params.append(trigger_id)
        query += " ORDER BY received_at DESC LIMIT %s"
        params.append(max(1, min(limit, 500)))
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._event_delivery(row) for row in rows]
