"""PostgreSQL row projections for Device Host transport."""

from __future__ import annotations

from typing import Any

from porthouse.storage.device_host_records import (
    DeviceHostRegistrationRecord,
    DeviceOperationDeliveryEventRecord,
    DeviceOperationDeliveryRecord,
)


def device_capabilities(
    conn: Any, user_id: str, device_id: str
) -> tuple[dict[str, Any], ...]:
    rows = conn.execute(
        """SELECT capability_id,version,implementation_digest,portable
           FROM device_host_capabilities WHERE user_id=%s AND device_id=%s
           ORDER BY capability_id,version""",
        (user_id, device_id),
    ).fetchall()
    return tuple(dict(row) for row in rows)


def device_host_record(
    row: dict[str, Any], capabilities: tuple[dict[str, Any], ...]
) -> DeviceHostRegistrationRecord:
    from porthouse.storage.postgres_store import _iso

    return DeviceHostRegistrationRecord(
        user_id=str(row["user_id"]),
        device_id=str(row["device_id"]),
        display_name=str(row["display_name"]),
        status=str(row["status"]),
        host_revision=str(row["host_revision"]),
        host_manifest_digest=str(row["host_manifest_digest"]),
        public_key_fingerprint=row["public_key_fingerprint"],
        is_default=bool(row["is_default"]),
        last_seen_at=_iso(row["last_seen_at"]),
        created_at=_iso(row["created_at"]) or "",
        updated_at=_iso(row["updated_at"]) or "",
        revoked_at=_iso(row["revoked_at"]),
        capabilities=capabilities,
    )


def device_delivery_record(row: dict[str, Any]) -> DeviceOperationDeliveryRecord:
    from porthouse.storage.postgres_store import _iso, _json

    return DeviceOperationDeliveryRecord(
        delivery_id=str(row["delivery_id"]),
        user_id=str(row["user_id"]),
        device_id=str(row["device_id"]),
        reconciliation_id=str(row["reconciliation_id"]),
        run_id=str(row["run_id"]),
        task_id=row["task_id"],
        action_id=str(row["action_id"]),
        invocation_id=str(row["invocation_id"]),
        operation_id=str(row["operation_id"]),
        capability_ref=dict(_json(row["capability_ref"], {})),
        capability_implementation_digest=str(row["capability_implementation_digest"]),
        host_revision=str(row["host_revision"]),
        request_digest=str(row["request_digest"]),
        request=dict(_json(row["request"], {})),
        portable=bool(row["portable"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        delivery_cursor=int(row["delivery_cursor"]),
        claim_session_id=row["claim_session_id"],
        claim_expires_at=_iso(row["claim_expires_at"]),
        claim_version=int(row["claim_version"]),
        result=_json(row["result"]),
        result_digest=row["result_digest"],
        error=_json(row["error"]),
        deadline_at=_iso(row["deadline_at"]) or "",
        created_at=_iso(row["created_at"]) or "",
        claimed_at=_iso(row["claimed_at"]),
        completed_at=_iso(row["completed_at"]),
        updated_at=_iso(row["updated_at"]) or "",
    )


def device_delivery_event_record(
    row: dict[str, Any],
) -> DeviceOperationDeliveryEventRecord:
    from porthouse.storage.postgres_store import _iso, _json

    return DeviceOperationDeliveryEventRecord(
        delivery_id=str(row["delivery_id"]),
        event_id=str(row["event_id"]),
        sequence=int(row["sequence"]),
        claim_version=int(row["claim_version"]),
        event_type=str(row["event_type"]),
        summary=str(row["summary"]),
        payload=dict(_json(row["payload"], {})),
        created_at=_iso(row["created_at"]) or "",
    )
