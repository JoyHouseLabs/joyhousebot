"""PostgreSQL outbox and fencing state for outbound Device Hosts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from joyhousebot.contracts import OperationProgressEvent
from joyhousebot.contracts.events import AgentEvent, EventType, EventVisibility
from joyhousebot.domain.operation_progress import (
    MAX_OPERATION_EVENTS_RETAINED,
    validated_operation_events,
)
from joyhousebot.storage.device_host_records import (
    DeviceHostRegistrationRecord,
    DeviceOperationDeliveryEventRecord,
    DeviceOperationDeliveryRecord,
)
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_device_host_records import (
    device_capabilities,
    device_delivery_event_record,
    device_delivery_record,
    device_host_record,
)
from joyhousebot.storage.postgres_event_writes import append_runtime_event_in_transaction


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

    def enqueue_device_operation(self, **values: Any) -> DeviceOperationDeliveryRecord:
        with self._pool.connection() as conn, conn.transaction():
            scope = conn.execute(
                """SELECT rec.*,intent.task_id,intent.input,intent.input_hash,
                          host.host_revision,cap.implementation_digest,cap.portable AS declared_portable
                   FROM operation_reconciliations rec
                   JOIN action_intents intent ON intent.action_id=rec.action_id
                   JOIN device_host_registrations host
                     ON host.user_id=rec.user_id AND host.device_id=%s AND host.status='active'
                   JOIN device_host_capabilities cap
                     ON cap.user_id=host.user_id AND cap.device_id=host.device_id
                    AND cap.capability_id=rec.capability_ref->>'capability_id'
                    AND cap.version=rec.capability_ref->>'version'
                   WHERE rec.reconciliation_id=%s AND rec.user_id=%s AND rec.run_id=%s
                     AND rec.status IN ('pending','manual_required')
                   FOR UPDATE OF rec,intent,host,cap""",
                (
                    values["device_id"],
                    values["reconciliation_id"],
                    values["user_id"],
                    values["run_id"],
                ),
            ).fetchone()
            if scope is None:
                raise ValueError("Device delivery scope or exact capability is unavailable")
            portable = bool(values.get("portable"))
            if portable and not bool(scope["declared_portable"]):
                raise ValueError("Device capability is not declared portable")
            operation = dict(scope["operation"] or {})
            frozen_operation_id = str(
                operation.get("remote_operation_id")
                or operation.get("provider_operation_id")
                or operation.get("operation_id")
                or ""
            )
            if frozen_operation_id != values["operation_id"]:
                raise ValueError("Device delivery operation identity is invalid")
            row = conn.execute(
                """INSERT INTO device_operation_deliveries
                       (delivery_id,user_id,device_id,reconciliation_id,run_id,task_id,
                        action_id,invocation_id,operation_id,capability_ref,
                        capability_implementation_digest,host_revision,request_digest,
                        request,portable,max_attempts,deadline_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           LEAST(%s,COALESCE(%s,clock_timestamp()+interval '1 day')))
                   ON CONFLICT(reconciliation_id) DO NOTHING RETURNING *""",
                (
                    values["delivery_id"],
                    values["user_id"],
                    values["device_id"],
                    values["reconciliation_id"],
                    values["run_id"],
                    scope["task_id"],
                    scope["action_id"],
                    scope["invocation_id"],
                    values["operation_id"],
                    Jsonb(dict(scope["capability_ref"])),
                    scope["implementation_digest"],
                    scope["host_revision"],
                    values["request_digest"],
                    Jsonb(values["request"]),
                    portable,
                    max(1, min(100, int(values.get("max_attempts") or 20))),
                    values["deadline_at"],
                    scope["deadline_at"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM device_operation_deliveries WHERE reconciliation_id=%s",
                    (values["reconciliation_id"],),
                ).fetchone()
                assert row is not None
                if (
                    str(row["device_id"]) != values["device_id"]
                    or str(row["request_digest"]) != values["request_digest"]
                ):
                    raise ValueError("Device delivery idempotency identity conflict")
            if str(scope["status"]) == "pending":
                conn.execute(
                    """UPDATE operation_reconciliations SET next_attempt_at=deadline_at,
                           updated_at=clock_timestamp()
                       WHERE reconciliation_id=%s AND status='pending'""",
                    (values["reconciliation_id"],),
                )
        return device_delivery_record(row)

    def claim_device_operations(self, **values: Any) -> list[DeviceOperationDeliveryRecord]:
        limit = max(1, min(20, int(values.get("limit") or 5)))
        lease_seconds = max(10, min(300, int(values.get("lease_seconds") or 60)))
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE device_operation_deliveries SET
                       status=CASE WHEN attempt_count>=max_attempts
                                        OR deadline_at<=clock_timestamp()
                                   THEN 'manual_required' ELSE 'queued' END,
                       claim_session_id=NULL,claim_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE user_id=%s AND device_id=%s AND status='claimed'
                     AND claim_expires_at<=clock_timestamp()""",
                (values["user_id"], values["device_id"]),
            )
            rows = conn.execute(
                """WITH candidates AS (
                       SELECT delivery_id FROM device_operation_deliveries
                       WHERE user_id=%s AND device_id=%s AND status='queued'
                         AND attempt_count<max_attempts AND deadline_at>clock_timestamp()
                       ORDER BY created_at,delivery_id FOR UPDATE SKIP LOCKED LIMIT %s
                   )
                   UPDATE device_operation_deliveries target SET status='claimed',
                       claim_session_id=%s,
                       claim_expires_at=clock_timestamp()+make_interval(secs => %s),
                       claim_version=claim_version+1,attempt_count=attempt_count+1,
                       claimed_at=COALESCE(claimed_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   FROM candidates WHERE target.delivery_id=candidates.delivery_id
                   RETURNING target.*""",
                (
                    values["user_id"],
                    values["device_id"],
                    limit,
                    values["claim_session_id"],
                    lease_seconds,
                ),
            ).fetchall()
        return [device_delivery_record(row) for row in rows]

    def append_device_operation_events(
        self, delivery_id: str, **values: Any
    ) -> DeviceOperationDeliveryRecord | None:
        events = validated_operation_events(
            OperationProgressEvent(**event) if isinstance(event, dict) else event
            for event in (values.get("events") or ())
        )
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM device_operation_deliveries
                   WHERE delivery_id=%s FOR UPDATE""",
                (delivery_id,),
            ).fetchone()
            if not self._valid_device_claim(row, values):
                return None
            retained = int(
                conn.execute(
                    "SELECT count(*) AS count FROM device_operation_delivery_events "
                    "WHERE delivery_id=%s",
                    (delivery_id,),
                ).fetchone()["count"]
            )
            inserted = 0
            cursor = int(row["delivery_cursor"])
            for event in events:
                existing = conn.execute(
                    """SELECT sequence,event_type,summary,payload
                       FROM device_operation_delivery_events
                       WHERE delivery_id=%s AND event_id=%s""",
                    (delivery_id, event.event_id),
                ).fetchone()
                if existing is not None:
                    if (
                        int(existing["sequence"]) != event.sequence
                        or str(existing["event_type"]) != event.event_type
                        or str(existing["summary"]) != event.summary
                        or dict(existing["payload"]) != event.payload
                    ):
                        raise ValueError("Device event identity was reused with new content")
                    continue
                if retained + inserted >= MAX_OPERATION_EVENTS_RETAINED:
                    raise ValueError("Device event retention limit exceeded")
                if event.sequence <= cursor:
                    raise ValueError("Device event sequence must advance the delivery cursor")
                conn.execute(
                    """INSERT INTO device_operation_delivery_events
                           (delivery_id,event_id,sequence,claim_version,event_type,summary,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        delivery_id,
                        event.event_id,
                        event.sequence,
                        values["claim_version"],
                        event.event_type,
                        event.summary,
                        Jsonb(event.payload),
                    ),
                )
                cursor = event.sequence
                inserted += 1
                reconciliation_event_id = "device_" + sha256(
                    f"{delivery_id}\0{event.event_id}".encode()
                ).hexdigest()
                reconciliation_sequence = int(
                    conn.execute(
                        """SELECT COALESCE(max(sequence),-1)+1 AS sequence
                           FROM operation_reconciliation_events
                           WHERE reconciliation_id=%s""",
                        (row["reconciliation_id"],),
                    ).fetchone()["sequence"]
                )
                conn.execute(
                    """INSERT INTO operation_reconciliation_events
                           (reconciliation_id,event_id,sequence,event_type,summary,payload)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (reconciliation_id,event_id) DO NOTHING""",
                    (
                        row["reconciliation_id"],
                        reconciliation_event_id,
                        reconciliation_sequence,
                        f"device.{event.event_type}",
                        event.summary,
                        Jsonb(event.payload),
                    ),
                )
                append_runtime_event_in_transaction(
                    conn,
                    AgentEvent(
                        event_id=f"device_progress_{reconciliation_event_id}",
                        run_id=str(row["run_id"]),
                        task_id=row["task_id"],
                        type=EventType.OPERATION_RECONCILIATION_PROGRESS.value,
                        phase="execution",
                        status="waiting_external",
                        visibility=EventVisibility.PRIVATE.value,
                        summary=(event.summary or event.event_type)[:500],
                        worker_id=f"device:{values['device_id']}",
                        lease_version=values["claim_version"],
                        data={
                            "delivery_id": delivery_id,
                            "reconciliation_id": str(row["reconciliation_id"]),
                            "device_id": values["device_id"],
                            "device_event_id": event.event_id,
                            "device_sequence": event.sequence,
                        },
                    ),
                )
            saved = conn.execute(
                """UPDATE device_operation_deliveries SET delivery_cursor=%s,
                       updated_at=clock_timestamp()
                   WHERE delivery_id=%s AND status='claimed' AND claim_session_id=%s
                     AND claim_version=%s RETURNING *""",
                (
                    cursor,
                    delivery_id,
                    values["claim_session_id"],
                    values["claim_version"],
                ),
            ).fetchone()
            if inserted:
                conn.execute(
                    """UPDATE operation_reconciliations SET progress_summary=%s,
                           last_provider_event_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE reconciliation_id=%s AND status IN ('pending','manual_required')""",
                    (events[-1].summary or events[-1].event_type, row["reconciliation_id"]),
                )
        return device_delivery_record(saved) if saved else None

    def heartbeat_device_operation(
        self, delivery_id: str, **values: Any
    ) -> DeviceOperationDeliveryRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE device_operation_deliveries SET
                       claim_expires_at=clock_timestamp()+make_interval(secs => %s),
                       updated_at=clock_timestamp()
                   WHERE delivery_id=%s AND user_id=%s AND device_id=%s
                     AND status='claimed' AND claim_session_id=%s AND claim_version=%s
                     AND claim_expires_at>clock_timestamp()
                     AND deadline_at>clock_timestamp() RETURNING *""",
                (
                    max(10, min(300, int(values.get("lease_seconds") or 60))),
                    delivery_id,
                    values["user_id"],
                    values["device_id"],
                    values["claim_session_id"],
                    values["claim_version"],
                ),
            ).fetchone()
        return device_delivery_record(row) if row else None

    def complete_device_operation(
        self, delivery_id: str, **values: Any
    ) -> DeviceOperationDeliveryRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM device_operation_deliveries WHERE delivery_id=%s FOR UPDATE",
                (delivery_id,),
            ).fetchone()
            if row is None or str(row["user_id"]) != values["user_id"] or str(
                row["device_id"]
            ) != values["device_id"]:
                return None
            if row["status"] in {"completed", "failed"}:
                if str(row["result_digest"] or "") != values["result_digest"]:
                    raise ValueError("Device completion was replayed with different content")
                return device_delivery_record(row)
            if not self._valid_device_claim(row, values):
                return None
            status = "completed" if values["result"]["status"] == "succeeded" else "failed"
            saved = conn.execute(
                """UPDATE device_operation_deliveries SET status=%s,result=%s,
                       result_digest=%s,error=%s,claim_session_id=NULL,claim_expires_at=NULL,
                       completed_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE delivery_id=%s AND status='claimed' AND claim_session_id=%s
                     AND claim_version=%s RETURNING *""",
                (
                    status,
                    Jsonb(values["result"]),
                    values["result_digest"],
                    Jsonb(values["result"].get("error"))
                    if values["result"].get("error")
                    else None,
                    delivery_id,
                    values["claim_session_id"],
                    values["claim_version"],
                ),
            ).fetchone()
            if saved is not None:
                append_runtime_event_in_transaction(
                    conn,
                    AgentEvent(
                        event_id=f"device_delivery_completed_{delivery_id}",
                        run_id=str(saved["run_id"]),
                        task_id=saved["task_id"],
                        type=EventType.OPERATION_RECONCILIATION_PROGRESS.value,
                        phase="execution",
                        status=str(saved["status"]),
                        visibility=EventVisibility.PRIVATE.value,
                        summary=str(values["result"].get("summary") or "设备执行已结束")[:500],
                        worker_id=f"device:{values['device_id']}",
                        lease_version=values["claim_version"],
                        data={
                            "delivery_id": delivery_id,
                            "reconciliation_id": str(saved["reconciliation_id"]),
                            "device_id": values["device_id"],
                            "result_digest": values["result_digest"],
                        },
                    ),
                )
        return device_delivery_record(saved) if saved else None

    def get_device_operation_delivery(
        self,
        delivery_id: str,
        *,
        expected_user_id: str | None = None,
        expected_device_id: str | None = None,
    ) -> DeviceOperationDeliveryRecord | None:
        clauses = ["delivery_id=%s"]
        params: list[Any] = [delivery_id]
        if expected_user_id is not None:
            clauses.append("user_id=%s")
            params.append(expected_user_id)
        if expected_device_id is not None:
            clauses.append("device_id=%s")
            params.append(expected_device_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM device_operation_deliveries WHERE " + " AND ".join(clauses),
                params,
            ).fetchone()
        return device_delivery_record(row) if row else None

    def list_device_operation_events(
        self,
        delivery_id: str,
        *,
        expected_user_id: str,
        after_sequence: int = -1,
        limit: int = 200,
    ) -> list[DeviceOperationDeliveryEventRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT evt.* FROM device_operation_delivery_events evt
                   JOIN device_operation_deliveries delivery
                     ON delivery.delivery_id=evt.delivery_id
                   WHERE evt.delivery_id=%s AND delivery.user_id=%s AND evt.sequence>%s
                   ORDER BY evt.sequence LIMIT %s""",
                (
                    delivery_id,
                    expected_user_id,
                    max(-1, int(after_sequence)),
                    max(1, min(500, int(limit))),
                ),
            ).fetchall()
        return [device_delivery_event_record(row) for row in rows]

    @staticmethod
    def _valid_device_claim(row: Any, values: dict[str, Any]) -> bool:
        return bool(
            row is not None
            and str(row["user_id"]) == values["user_id"]
            and str(row["device_id"]) == values["device_id"]
            and str(row["status"]) == "claimed"
            and str(row["claim_session_id"] or "") == values["claim_session_id"]
            and int(row["claim_version"]) == int(values["claim_version"])
            and row["claim_expires_at"] is not None
            and row["claim_expires_at"] > datetime.now(UTC)
        )
