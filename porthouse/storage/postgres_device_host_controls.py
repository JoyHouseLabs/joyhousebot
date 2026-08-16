"""PostgreSQL projection for bounded, non-business Device Host controls."""

from __future__ import annotations

from typing import Any

from porthouse.storage.device_host_records import DeviceHostControlRequestRecord
from porthouse.storage.json_codec import Jsonb


def _control_record(row: Any) -> DeviceHostControlRequestRecord:
    return DeviceHostControlRequestRecord(
        request_id=str(row["request_id"]),
        user_id=str(row["user_id"]),
        device_id=str(row["device_id"]),
        action=str(row["action"]),
        parameters=dict(row["parameters"] or {}),
        status=str(row["status"]),
        request_digest=str(row["request_digest"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        claim_session_id=row["claim_session_id"],
        claim_expires_at=(row["claim_expires_at"].isoformat() if row["claim_expires_at"] else None),
        claim_version=int(row["claim_version"]),
        result=dict(row["result"]) if row["result"] else None,
        error=dict(row["error"]) if row["error"] else None,
        requested_by=str(row["requested_by"]),
        created_at=row["created_at"].isoformat(),
        claimed_at=row["claimed_at"].isoformat() if row["claimed_at"] else None,
        completed_at=row["completed_at"].isoformat() if row["completed_at"] else None,
        updated_at=row["updated_at"].isoformat(),
    )


class PostgresDeviceHostControlStoreMixin:
    def migrate_device_host_controls(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS device_host_control_requests (
            request_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN (
                'preflight','diagnose_opencli','diagnose_pi',
                'enable_opencli','disable_opencli','enable_pi','disable_pi','restart_host'
            )),
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','claimed','succeeded','failed','cancelled','manual_required')),
            request_digest TEXT NOT NULL CHECK (request_digest ~ '^sha256:[0-9a-f]{64}$'),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
            claim_session_id TEXT,
            claim_expires_at TIMESTAMPTZ,
            claim_version BIGINT NOT NULL DEFAULT 0,
            result JSONB,
            error JSONB,
            requested_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            claimed_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (user_id, device_id)
                REFERENCES device_host_registrations(user_id, device_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_device_host_control_claim
            ON device_host_control_requests(device_id, created_at)
            WHERE status IN ('queued','claimed');
        CREATE INDEX IF NOT EXISTS ix_device_host_control_owner
            ON device_host_control_requests(user_id, device_id, created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="device_host_controls",
                version=1,
                ddl=ddl,
                description="bounded and auditable local Host maintenance controls",
            )

    def create_device_host_control_request(self, **values: Any) -> DeviceHostControlRequestRecord:
        with self._pool.connection() as conn, conn.transaction():
            device = conn.execute(
                """SELECT device_id FROM device_host_registrations
                   WHERE user_id=%s AND device_id=%s AND status='active'""",
                (values["user_id"], values["device_id"]),
            ).fetchone()
            if device is None:
                raise ValueError("active Device Host not found")
            row = conn.execute(
                """INSERT INTO device_host_control_requests
                       (request_id,user_id,device_id,action,parameters,request_digest,max_attempts,requested_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    values["request_id"], values["user_id"], values["device_id"],
                    values["action"], Jsonb(values["parameters"]), values["request_digest"],
                    values["max_attempts"], values["requested_by"],
                ),
            ).fetchone()
        return _control_record(row)

    def list_device_host_control_requests(
        self, *, user_id: str, device_id: str, limit: int
    ) -> list[DeviceHostControlRequestRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM device_host_control_requests
                   WHERE user_id=%s AND device_id=%s ORDER BY created_at DESC LIMIT %s""",
                (user_id, device_id, max(1, min(100, int(limit)))),
            ).fetchall()
        return [_control_record(row) for row in rows]

    def claim_device_host_control_requests(self, **values: Any) -> list[DeviceHostControlRequestRecord]:
        limit = max(1, min(10, int(values.get("limit") or 3)))
        lease_seconds = max(10, min(300, int(values.get("lease_seconds") or 60)))
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE device_host_control_requests SET
                    status=CASE WHEN attempt_count>=max_attempts THEN 'manual_required' ELSE 'queued' END,
                    claim_session_id=NULL,claim_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE user_id=%s AND device_id=%s AND status='claimed'
                     AND claim_expires_at<=clock_timestamp()""",
                (values["user_id"], values["device_id"]),
            )
            rows = conn.execute(
                """WITH candidates AS (
                    SELECT request_id FROM device_host_control_requests
                    WHERE user_id=%s AND device_id=%s AND status='queued'
                      AND attempt_count<max_attempts
                    ORDER BY created_at,request_id FOR UPDATE SKIP LOCKED LIMIT %s
                   ) UPDATE device_host_control_requests target SET
                    status='claimed',claim_session_id=%s,
                    claim_expires_at=clock_timestamp()+make_interval(secs => %s),
                    claim_version=claim_version+1,attempt_count=attempt_count+1,
                    claimed_at=COALESCE(claimed_at,clock_timestamp()),updated_at=clock_timestamp()
                   FROM candidates WHERE target.request_id=candidates.request_id RETURNING target.*""",
                (values["user_id"], values["device_id"], limit, values["claim_session_id"], lease_seconds),
            ).fetchall()
        return [_control_record(row) for row in rows]

    def complete_device_host_control_request(
        self, request_id: str, **values: Any
    ) -> DeviceHostControlRequestRecord | None:
        status = values["status"]
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE device_host_control_requests SET status=%s,result=%s,error=%s,
                    completed_at=clock_timestamp(),claim_session_id=NULL,claim_expires_at=NULL,
                    updated_at=clock_timestamp()
                   WHERE request_id=%s AND user_id=%s AND device_id=%s AND status='claimed'
                     AND claim_session_id=%s AND claim_version=%s RETURNING *""",
                (status, Jsonb(values.get("result") or {}), Jsonb(values.get("error") or {}),
                 request_id, values["user_id"], values["device_id"],
                 values["claim_session_id"], values["claim_version"]),
            ).fetchone()
        return _control_record(row) if row is not None else None
