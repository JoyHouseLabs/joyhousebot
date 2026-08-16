"""Durable child Action queue for tools requested by a Host Agent loop."""

from __future__ import annotations

from typing import Any

from porthouse.storage.host_tool_records import HostToolGrantRecord, HostToolRequestRecord
from porthouse.storage.json_codec import Jsonb


class PostgresHostToolStoreMixin:
    def migrate_host_tools(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS host_tool_requests (
            request_id TEXT PRIMARY KEY,
            host_request_id TEXT NOT NULL,
            delivery_id TEXT NOT NULL REFERENCES device_operation_deliveries(delivery_id)
                ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            agent_id TEXT NOT NULL,
            capability_ref JSONB NOT NULL,
            input JSONB NOT NULL,
            input_hash TEXT NOT NULL,
            action_id TEXT NOT NULL UNIQUE,
            turn_id TEXT NOT NULL UNIQUE,
            turn_index INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','waiting_approval','waiting_external',
                                  'succeeded','failed','manual_required','cancelled')),
            result JSONB,
            error JSONB,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(delivery_id,host_request_id)
        );
        CREATE INDEX IF NOT EXISTS ix_host_tool_requests_claim
            ON host_tool_requests(status,created_at)
            WHERE status IN ('queued','waiting_approval','waiting_external','running');
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            conn.execute(
                "ALTER TABLE host_tool_requests "
                "ADD COLUMN IF NOT EXISTS turn_index INTEGER NOT NULL DEFAULT 0"
            )
            self._record_migration(
                conn,
                name="host_tool_broker",
                version=1,
                ddl=ddl,
                description="durable governed child Actions requested by Host Agent loops",
            )
            grant_ddl = """
            CREATE TABLE IF NOT EXISTS host_tool_grants (
                grant_id TEXT PRIMARY KEY,
                token_fingerprint TEXT NOT NULL UNIQUE,
                delivery_id TEXT NOT NULL REFERENCES device_operation_deliveries(delivery_id)
                    ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                claim_session_id TEXT NOT NULL,
                claim_version INTEGER NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                UNIQUE(delivery_id,claim_version)
            );
            CREATE INDEX IF NOT EXISTS ix_host_tool_grants_token
                ON host_tool_grants(token_fingerprint,expires_at);
            """
            conn.execute(grant_ddl)
            self._record_migration(
                conn,
                name="host_tool_broker",
                version=2,
                ddl=grant_ddl,
                description="short-lived scoped grants for untrusted Host child processes",
            )

    def create_host_tool_grant(self, **values: Any) -> HostToolGrantRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO host_tool_grants
                       (grant_id,token_fingerprint,delivery_id,user_id,device_id,
                        claim_session_id,claim_version,expires_at)
                   SELECT %s,%s,delivery_id,user_id,device_id,%s,%s,
                          LEAST(%s,claim_expires_at,deadline_at)
                   FROM device_operation_deliveries
                   WHERE delivery_id=%s AND user_id=%s AND device_id=%s
                     AND status='claimed' AND claim_session_id=%s AND claim_version=%s
                     AND claim_expires_at>clock_timestamp()
                     AND jsonb_array_length(
                       COALESCE(request->'authorization'->'tool_access','[]'::jsonb)
                     )>0
                   ON CONFLICT(delivery_id,claim_version) DO UPDATE SET
                       token_fingerprint=excluded.token_fingerprint,
                       claim_session_id=excluded.claim_session_id,
                       expires_at=excluded.expires_at
                   RETURNING *""",
                (
                    values["grant_id"],
                    values["token_fingerprint"],
                    values["claim_session_id"],
                    values["claim_version"],
                    values["expires_at"],
                    values["delivery_id"],
                    values["user_id"],
                    values["device_id"],
                    values["claim_session_id"],
                    values["claim_version"],
                ),
            ).fetchone()
        return self._host_tool_grant_record(row) if row else None

    def authenticate_host_tool_grant(
        self, *, token_fingerprint: str
    ) -> HostToolGrantRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT tool_grant.* FROM host_tool_grants tool_grant
                   JOIN device_operation_deliveries delivery
                     ON delivery.delivery_id=tool_grant.delivery_id
                   JOIN device_host_registrations device
                     ON device.user_id=tool_grant.user_id
                    AND device.device_id=tool_grant.device_id
                   JOIN runtime_runs run ON run.run_id=delivery.run_id
                   WHERE tool_grant.token_fingerprint=%s
                     AND tool_grant.expires_at>clock_timestamp()
                     AND device.status='active' AND delivery.status='claimed'
                     AND delivery.claim_session_id=tool_grant.claim_session_id
                     AND delivery.claim_version=tool_grant.claim_version
                     AND delivery.claim_expires_at>clock_timestamp()
                     AND run.status NOT IN ('completed','failed','cancelled','timed_out')""",
                (token_fingerprint,),
            ).fetchone()
        return self._host_tool_grant_record(row) if row else None

    def create_host_tool_request(self, **values: Any) -> tuple[HostToolRequestRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            delivery = conn.execute(
                """SELECT * FROM device_operation_deliveries
                   WHERE delivery_id=%s AND user_id=%s AND device_id=%s
                     AND status='claimed' AND claim_session_id=%s AND claim_version=%s
                     AND claim_expires_at>clock_timestamp() FOR UPDATE""",
                (
                    values["delivery_id"], values["user_id"], values["device_id"],
                    values["claim_session_id"], values["claim_version"],
                ),
            ).fetchone()
            if delivery is None:
                raise ValueError("Device delivery claim is stale")
            run = conn.execute(
                "SELECT * FROM runtime_runs WHERE run_id=%s FOR SHARE",
                (delivery["run_id"],),
            ).fetchone()
            if run is None or run["status"] in {"completed", "failed", "cancelled", "timed_out"}:
                raise ValueError("parent Run cannot accept Host tool requests")
            authorization = dict(delivery["request"] or {}).get("authorization") or {}
            allowed = {
                (str(item.get("capability_id") or ""), str(item.get("version") or ""))
                for item in authorization.get("tool_access") or ()
                if isinstance(item, dict)
            }
            identity = (
                str(values["capability_ref"]["capability_id"]),
                str(values["capability_ref"]["version"]),
            )
            if identity not in allowed:
                raise ValueError("Host tool is outside the frozen allowlist")
            existing = conn.execute(
                """SELECT *,FALSE AS created FROM host_tool_requests
                   WHERE delivery_id=%s AND host_request_id=%s""",
                (values["delivery_id"], values["host_request_id"]),
            ).fetchone()
            if existing is not None:
                if (
                    existing["input_hash"] != values["input_hash"]
                    or dict(existing["capability_ref"]) != values["capability_ref"]
                ):
                    raise ValueError("Host tool request identity conflict")
                return self._host_tool_record(existing), False
            request_count = conn.execute(
                "SELECT count(*) AS value FROM host_tool_requests WHERE delivery_id=%s",
                (values["delivery_id"],),
            ).fetchone()
            if request_count is not None and int(request_count["value"]) >= 64:
                raise ValueError("Host tool request budget is exhausted")
            row = conn.execute(
                """INSERT INTO host_tool_requests
                       (request_id,host_request_id,delivery_id,user_id,run_id,task_id,
                        agent_id,capability_ref,input,input_hash,action_id,turn_id,turn_index)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *,TRUE AS created""",
                (
                    values["request_id"], values["host_request_id"], values["delivery_id"],
                    values["user_id"], delivery["run_id"], delivery["task_id"],
                    dict(delivery["request"] or {}).get("subject", {}).get("agent_id") or run["agent_id"],
                    Jsonb(values["capability_ref"]), Jsonb(values["input"]), values["input_hash"],
                    values["action_id"], values["turn_id"], values["turn_index"],
                ),
            ).fetchone()
            assert row is not None
        return self._host_tool_record(row), bool(row["created"])

    def get_host_tool_request(self, request_id: str, **scope: Any) -> HostToolRequestRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM host_tool_requests WHERE request_id=%s
                     AND (%s::text IS NULL OR user_id=%s)
                     AND (%s::text IS NULL OR delivery_id=%s)""",
                (
                    request_id,
                    scope.get("user_id"),
                    scope.get("user_id"),
                    scope.get("delivery_id"),
                    scope.get("delivery_id"),
                ),
            ).fetchone()
        return self._host_tool_record(row) if row else None

    def claim_host_tool_request(self, *, worker_id: str, lease_seconds: int) -> HostToolRequestRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """WITH candidate AS (
                       SELECT req.request_id FROM host_tool_requests req
                       JOIN runtime_runs run ON run.run_id=req.run_id
                       WHERE req.status IN ('queued','waiting_approval','waiting_external','running')
                         AND (req.lease_expires_at IS NULL OR req.lease_expires_at<=clock_timestamp())
                         AND (
                           req.status <> 'waiting_approval'
                           OR EXISTS (
                             SELECT 1 FROM approval_requests approval
                             WHERE approval.action_id=req.action_id
                               AND approval.status <> 'pending'
                           )
                         )
                         AND run.status NOT IN ('completed','failed','cancelled','timed_out')
                       ORDER BY req.created_at FOR UPDATE OF req SKIP LOCKED LIMIT 1
                   )
                   UPDATE host_tool_requests target SET status='running',lease_owner=%s,
                       lease_expires_at=clock_timestamp()+make_interval(secs => %s),
                       lease_version=lease_version+1,updated_at=clock_timestamp()
                   FROM candidate WHERE target.request_id=candidate.request_id RETURNING target.*""",
                (worker_id, lease_seconds),
            ).fetchone()
        return self._host_tool_record(row) if row else None

    def finish_host_tool_request(self, request_id: str, **values: Any) -> HostToolRequestRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE host_tool_requests SET status=%s,result=%s,error=%s,
                       lease_owner=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE request_id=%s AND lease_owner=%s AND lease_version=%s RETURNING *""",
                (
                    values["status"], Jsonb(values["result"]) if values.get("result") else None,
                    Jsonb(values["error"]) if values.get("error") else None,
                    request_id, values["worker_id"], values["lease_version"],
                ),
            ).fetchone()
        return self._host_tool_record(row) if row else None

    @staticmethod
    def _host_tool_record(row: dict[str, Any]) -> HostToolRequestRecord:
        from porthouse.storage.postgres_store import _iso

        return HostToolRequestRecord(
            request_id=str(row["request_id"]),
            host_request_id=str(row["host_request_id"]),
            delivery_id=str(row["delivery_id"]),
            user_id=str(row["user_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            agent_id=str(row["agent_id"]),
            capability_ref=dict(row["capability_ref"]),
            input=dict(row["input"]),
            input_hash=str(row["input_hash"]),
            action_id=str(row["action_id"]),
            turn_id=str(row["turn_id"]),
            turn_index=int(row["turn_index"]),
            status=str(row["status"]),
            result=dict(row["result"]) if row["result"] else None,
            error=dict(row["error"]) if row["error"] else None,
            lease_version=int(row["lease_version"]),
            created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",
        )

    @staticmethod
    def _host_tool_grant_record(row: dict[str, Any]) -> HostToolGrantRecord:
        from porthouse.storage.postgres_store import _iso

        return HostToolGrantRecord(
            grant_id=str(row["grant_id"]),
            delivery_id=str(row["delivery_id"]),
            user_id=str(row["user_id"]),
            device_id=str(row["device_id"]),
            claim_session_id=str(row["claim_session_id"]),
            claim_version=int(row["claim_version"]),
            expires_at=_iso(row["expires_at"]) or "",
            created_at=_iso(row["created_at"]) or "",
        )
