"""PostgreSQL outbox and fencing state for outbound Device Hosts."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.device_host_records import (
    DeviceHostRegistrationRecord,
)
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_device_host_records import (
    device_capabilities,
    device_host_record,
)


class PostgresDeviceHostStoreMixin:
    def migrate_device_hosts(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS device_host_registrations (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','revoked')),
            token_fingerprint TEXT NOT NULL UNIQUE,
            public_key_fingerprint TEXT,
            host_revision TEXT NOT NULL,
            host_manifest_digest TEXT NOT NULL
                CHECK (host_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            last_seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            PRIMARY KEY (user_id,device_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_device_host_default
            ON device_host_registrations(user_id) WHERE is_default AND status='active';
        CREATE INDEX IF NOT EXISTS ix_device_host_token
            ON device_host_registrations(token_fingerprint) WHERE status='active';

        CREATE TABLE IF NOT EXISTS device_host_capabilities (
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            capability_id TEXT NOT NULL,
            version TEXT NOT NULL,
            implementation_digest TEXT NOT NULL
                CHECK (implementation_digest ~ '^sha256:[0-9a-f]{64}$'),
            portable BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (user_id,device_id,capability_id,version),
            FOREIGN KEY (user_id,device_id)
                REFERENCES device_host_registrations(user_id,device_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS device_operation_deliveries (
            delivery_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            reconciliation_id TEXT NOT NULL UNIQUE
                REFERENCES operation_reconciliations(reconciliation_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            action_id TEXT NOT NULL REFERENCES action_intents(action_id) ON DELETE CASCADE,
            invocation_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            capability_ref JSONB NOT NULL,
            capability_implementation_digest TEXT NOT NULL,
            host_revision TEXT NOT NULL,
            request_digest TEXT NOT NULL CHECK (request_digest ~ '^sha256:[0-9a-f]{64}$'),
            request JSONB NOT NULL,
            portable BOOLEAN NOT NULL DEFAULT FALSE,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','claimed','completed','failed','cancelled','manual_required')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 20 CHECK (max_attempts BETWEEN 1 AND 100),
            delivery_cursor BIGINT NOT NULL DEFAULT -1,
            claim_session_id TEXT,
            claim_expires_at TIMESTAMPTZ,
            claim_version BIGINT NOT NULL DEFAULT 0,
            result JSONB,
            result_digest TEXT,
            error JSONB,
            deadline_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            claimed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (user_id,device_id)
                REFERENCES device_host_registrations(user_id,device_id)
        );
        CREATE INDEX IF NOT EXISTS ix_device_operation_claim
            ON device_operation_deliveries(device_id,created_at)
            WHERE status IN ('queued','claimed');
        CREATE INDEX IF NOT EXISTS ix_device_operation_owner
            ON device_operation_deliveries(user_id,run_id,created_at DESC);

        CREATE TABLE IF NOT EXISTS device_operation_delivery_events (
            delivery_id TEXT NOT NULL
                REFERENCES device_operation_deliveries(delivery_id) ON DELETE CASCADE,
            event_id TEXT NOT NULL,
            sequence BIGINT NOT NULL CHECK (sequence >= 0),
            claim_version BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (delivery_id,event_id),
            UNIQUE (delivery_id,sequence)
        );
        CREATE INDEX IF NOT EXISTS ix_device_delivery_events_created
            ON device_operation_delivery_events(delivery_id,created_at);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="device_host_transport",
                version=1,
                ddl=ddl,
                description="outbound device identities and fenced operation delivery outbox",
            )

    def register_device_host(self, **values: Any) -> DeviceHostRegistrationRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO device_host_registrations
                       (user_id,device_id,display_name,token_fingerprint,
                        public_key_fingerprint,host_revision,host_manifest_digest,is_default)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,device_id) DO NOTHING RETURNING *""",
                (
                    values["user_id"],
                    values["device_id"],
                    values["display_name"],
                    values["token_fingerprint"],
                    values.get("public_key_fingerprint"),
                    values["host_revision"],
                    values["host_manifest_digest"],
                    False,
                ),
            ).fetchone()
            if row is None:
                return None
            for capability in values["capabilities"]:
                conn.execute(
                    """INSERT INTO device_host_capabilities
                           (user_id,device_id,capability_id,version,implementation_digest,portable)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        values["user_id"],
                        values["device_id"],
                        capability["capability_id"],
                        capability["version"],
                        capability["implementation_digest"],
                        bool(capability.get("portable")),
                    ),
                )
            if values.get("is_default"):
                conn.execute(
                    """UPDATE device_host_registrations SET is_default=FALSE,
                           updated_at=clock_timestamp()
                       WHERE user_id=%s AND device_id<>%s AND status='active'""",
                    (values["user_id"], values["device_id"]),
                )
                row = conn.execute(
                    """UPDATE device_host_registrations SET is_default=TRUE,
                           updated_at=clock_timestamp()
                       WHERE user_id=%s AND device_id=%s RETURNING *""",
                    (values["user_id"], values["device_id"]),
                ).fetchone()
                assert row is not None
        return device_host_record(row, tuple(values["capabilities"]))

    def rotate_device_host_token(self, **values: Any) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE device_host_registrations SET token_fingerprint=%s,
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND device_id=%s AND status='active' RETURNING device_id""",
                (values["token_fingerprint"], values["user_id"], values["device_id"]),
            ).fetchone()
        return row is not None

    def authenticate_device_host(
        self, *, token_fingerprint: str, device_id: str
    ) -> DeviceHostRegistrationRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM device_host_registrations
                   WHERE token_fingerprint=%s AND device_id=%s AND status='active'""",
                (token_fingerprint, device_id),
            ).fetchone()
            if row is None:
                return None
            capabilities = device_capabilities(conn, str(row["user_id"]), device_id)
        return device_host_record(row, capabilities)

    def heartbeat_device_host(self, **values: Any) -> DeviceHostRegistrationRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE device_host_registrations SET last_seen_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE token_fingerprint=%s AND device_id=%s AND status='active'
                     AND host_revision=%s AND host_manifest_digest=%s RETURNING *""",
                (
                    values["token_fingerprint"],
                    values["device_id"],
                    values["host_revision"],
                    values["host_manifest_digest"],
                ),
            ).fetchone()
            if row is None:
                return None
            capabilities = device_capabilities(
                conn, str(row["user_id"]), str(row["device_id"])
            )
        return device_host_record(row, capabilities)

    def list_device_hosts(self, *, user_id: str) -> list[DeviceHostRegistrationRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM device_host_registrations
                   WHERE user_id=%s ORDER BY is_default DESC,created_at,device_id""",
                (user_id,),
            ).fetchall()
            return [
                device_host_record(
                    row,
                    device_capabilities(conn, user_id, str(row["device_id"])),
                )
                for row in rows
            ]

    def revoke_device_host(self, **values: Any) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE device_host_registrations SET status='revoked',is_default=FALSE,
                       token_fingerprint='revoked:'||user_id||':'||device_id,
                       revoked_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE user_id=%s AND device_id=%s AND status='active' RETURNING device_id""",
                (values["user_id"], values["device_id"]),
            ).fetchone()
            if row is None:
                return False
            reconciliations = conn.execute(
                """UPDATE device_operation_deliveries SET status='cancelled',
                       claim_session_id=NULL,claim_expires_at=NULL,
                       error=%s,updated_at=clock_timestamp()
                   WHERE user_id=%s AND device_id=%s AND status IN ('queued','claimed')
                   RETURNING reconciliation_id""",
                (
                    Jsonb({"code": "DEVICE_REVOKED", "message": "device was revoked"}),
                    values["user_id"],
                    values["device_id"],
                ),
            ).fetchall()
            if reconciliations:
                conn.execute(
                    """UPDATE operation_reconciliations SET next_attempt_at=clock_timestamp(),
                           updated_at=clock_timestamp()
                       WHERE reconciliation_id=ANY(%s) AND status='pending'""",
                    ([str(item["reconciliation_id"]) for item in reconciliations],),
                )
        return True

