"""Delivery outbox projections for the Device Host transport.

Split from ``postgres_device_hosts`` by responsibility: registrations and
capability declarations stay with the host identity mixin, while frozen
operation deliveries, claims, events and completions live here. Both mixins
are composed into one PostgreSQL repository set and share the same connection pool.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from joyhousebot.contracts import OperationProgressEvent
from joyhousebot.contracts.events import AgentEvent, EventType, EventVisibility
from joyhousebot.domain.operation_progress import (
    MAX_OPERATION_EVENTS_RETAINED,
    validated_operation_events,
)
from joyhousebot.storage.device_host_records import (
    DeviceOperationDeliveryEventRecord,
    DeviceOperationDeliveryRecord,
)
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_device_host_records import (
    device_delivery_event_record,
    device_delivery_record,
)
from joyhousebot.storage.postgres_event_writes import append_runtime_event_in_transaction


class PostgresDeviceDeliveryStoreMixin:
    def find_device_delivery_candidates(
        self, *, limit: int, created_within_seconds: int
    ) -> list[dict[str, Any]]:
        """Frozen operations an active device could execute but has no delivery for.

        Auto-delivery only ever matches capabilities the device explicitly
        declared, and only reconciliations that are still unresolved; the
        UNIQUE(reconciliation_id) constraint on deliveries makes enqueueing
        idempotent if this scan races a manual delivery creation.
        """
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT rec.reconciliation_id,rec.run_id,rec.user_id,rec.operation,
                          host.device_id,
                          rec.capability_ref->>'capability_id' AS capability_id,
                          rec.capability_ref->>'version' AS capability_version
                   FROM operation_reconciliations rec
                   JOIN LATERAL (
                       SELECT host.device_id
                       FROM device_host_registrations host
                       JOIN device_host_capabilities cap
                         ON cap.user_id=host.user_id AND cap.device_id=host.device_id
                       WHERE host.user_id=rec.user_id AND host.status='active'
                         AND cap.capability_id=rec.capability_ref->>'capability_id'
                         AND cap.version=rec.capability_ref->>'version'
                       ORDER BY host.is_default DESC,host.created_at,host.device_id
                       LIMIT 1
                   ) host ON TRUE
                   WHERE rec.status IN ('pending','manual_required')
                     AND rec.created_at > clock_timestamp()-make_interval(secs => %s)
                     AND NOT EXISTS (
                         SELECT 1 FROM device_operation_deliveries d
                         WHERE d.reconciliation_id=rec.reconciliation_id
                     )
                   ORDER BY rec.created_at
                   LIMIT %s""",
                (created_within_seconds, limit),
            ).fetchall()
            return [
                {
                    "reconciliation_id": str(row["reconciliation_id"]),
                    "run_id": str(row["run_id"]),
                    "user_id": str(row["user_id"]),
                    "device_id": str(row["device_id"]),
                    "capability_id": str(row["capability_id"]),
                    "capability_version": str(row["capability_version"]),
                    "provider_operation_id": (dict(row["operation"] or {}) or {}).get(
                        "provider_operation_id"
                    ),
                }
                for row in rows
            ]

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
                   WHERE delivery_id=%s AND claim_expires_at>clock_timestamp() FOR UPDATE""",
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
                     AND claim_version=%s AND claim_expires_at>clock_timestamp()
                   RETURNING *""",
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
                     AND claim_version=%s AND claim_expires_at>clock_timestamp()
                   RETURNING *""",
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
        )
