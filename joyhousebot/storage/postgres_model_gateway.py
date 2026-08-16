"""Transactional model budget grants for untrusted Host processes."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.model_gateway_records import (
    HostModelGrantRecord,
    HostModelReservationRecord,
)


class PostgresModelGatewayStoreMixin:
    def migrate_model_gateway(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS host_model_grants (
            grant_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            action_id TEXT NOT NULL REFERENCES action_intents(action_id) ON DELETE CASCADE,
            delivery_id TEXT NOT NULL REFERENCES device_operation_deliveries(delivery_id)
                ON DELETE CASCADE,
            device_id TEXT NOT NULL,
            capability_ref JSONB NOT NULL,
            provider_id TEXT NOT NULL,
            provider_revision_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            token_budget BIGINT NOT NULL CHECK (token_budget > 0),
            cost_budget_micros BIGINT NOT NULL CHECK (cost_budget_micros >= 0),
            reserved_tokens BIGINT NOT NULL DEFAULT 0,
            used_tokens BIGINT NOT NULL DEFAULT 0,
            reserved_cost_micros BIGINT NOT NULL DEFAULT 0,
            used_cost_micros BIGINT NOT NULL DEFAULT 0,
            active_reservations INTEGER NOT NULL DEFAULT 0,
            max_concurrent INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrent BETWEEN 1 AND 32),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','revoked','expired','exhausted')),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            UNIQUE(delivery_id,model_id)
        );
        CREATE INDEX IF NOT EXISTS ix_host_model_grants_owner
            ON host_model_grants(user_id,run_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_host_model_grants_token
            ON host_model_grants(token_fingerprint) WHERE status='active';

        CREATE TABLE IF NOT EXISTS host_model_reservations (
            reservation_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES host_model_grants(grant_id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            reserved_tokens BIGINT NOT NULL CHECK (reserved_tokens > 0),
            reserved_cost_micros BIGINT NOT NULL CHECK (reserved_cost_micros >= 0),
            actual_tokens BIGINT,
            actual_cost_micros BIGINT,
            status TEXT NOT NULL DEFAULT 'reserved'
                CHECK (status IN ('reserved','settled','released')),
            usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            response JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ NOT NULL,
            settled_at TIMESTAMPTZ,
            UNIQUE(grant_id,request_id)
        );
        CREATE INDEX IF NOT EXISTS ix_host_model_reservations_active
            ON host_model_reservations(grant_id,created_at) WHERE status='reserved';
        ALTER TABLE host_model_reservations
            ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
        ALTER TABLE host_model_reservations
            ADD COLUMN IF NOT EXISTS response JSONB;
        UPDATE host_model_reservations
           SET expires_at=created_at+interval '10 minutes'
         WHERE expires_at IS NULL;
        ALTER TABLE host_model_reservations
            ALTER COLUMN expires_at SET NOT NULL;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="host_model_gateway",
                version=1,
                ddl=ddl,
                description="short-lived Host model grants and transactional budget reservations",
            )

    def create_host_model_grant(self, **values: Any) -> HostModelGrantRecord:
        with self._pool.connection() as conn, conn.transaction():
            scope = conn.execute(
                """SELECT delivery.user_id,delivery.run_id,delivery.task_id,
                          delivery.action_id,delivery.device_id,delivery.capability_ref,
                          delivery.deadline_at,run.status AS run_status,
                          revision.configuration,revision.status AS revision_status,
                          provider.current_revision_id
                   FROM device_operation_deliveries delivery
                   JOIN runtime_runs run ON run.run_id=delivery.run_id
                   JOIN model_providers provider ON provider.provider_id=%s
                   JOIN model_provider_revisions revision
                     ON revision.provider_id=provider.provider_id AND revision.revision_id=%s
                   WHERE delivery.delivery_id=%s AND delivery.user_id=%s
                     AND delivery.status IN ('queued','claimed') FOR SHARE""",
                (
                    values["provider_id"],
                    values["provider_revision_id"],
                    values["delivery_id"],
                    values["user_id"],
                ),
            ).fetchone()
            if scope is None:
                raise ValueError("Host model grant scope is unavailable")
            if scope["run_status"] in {"completed", "failed", "cancelled", "timed_out"}:
                raise ValueError("Host model grant cannot target a terminal Run")
            if (
                scope["revision_status"] != "published"
                or scope["current_revision_id"] != values["provider_revision_id"]
            ):
                raise ValueError("Host model grant requires the active exact Provider revision")
            configuration = dict(scope["configuration"] or {})
            model = next(
                (
                    dict(item)
                    for item in configuration.get("models") or ()
                    if item.get("model_id") == values["model_id"]
                    and item.get("kind", "llm") == "llm"
                    and item.get("enabled", True)
                ),
                None,
            )
            if model is None:
                raise ValueError("Host model grant requires an active exact LLM model")
            row = conn.execute(
                """INSERT INTO host_model_grants
                       (grant_id,token_fingerprint,user_id,run_id,task_id,action_id,
                        delivery_id,device_id,capability_ref,provider_id,
                        provider_revision_id,model_id,token_budget,cost_budget_micros,
                        max_concurrent,expires_at)
                   SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          LEAST(%s,delivery.deadline_at)
                   FROM device_operation_deliveries delivery
                   WHERE delivery.delivery_id=%s
                   ON CONFLICT(delivery_id,model_id) DO NOTHING RETURNING *""",
                (
                    values["grant_id"],
                    values["token_fingerprint"],
                    values["user_id"],
                    scope["run_id"],
                    scope["task_id"],
                    scope["action_id"],
                    values["delivery_id"],
                    scope["device_id"],
                    Jsonb(dict(scope["capability_ref"])),
                    values["provider_id"],
                    values["provider_revision_id"],
                    values["model_id"],
                    values["token_budget"],
                    values["cost_budget_micros"],
                    values["max_concurrent"],
                    values["expires_at"],
                    values["delivery_id"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT * FROM host_model_grants
                       WHERE delivery_id=%s AND model_id=%s""",
                    (values["delivery_id"], values["model_id"]),
                ).fetchone()
                assert row is not None
        return self._host_model_grant(row)

    def authenticate_host_model_grant(
        self, *, token_fingerprint: str
    ) -> HostModelGrantRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE host_model_grants SET status='expired',updated_at=clock_timestamp()
                   WHERE token_fingerprint=%s AND status='active'
                     AND expires_at<=clock_timestamp()""",
                (token_fingerprint,),
            )
            row = conn.execute(
                """SELECT g.* FROM host_model_grants g
                   JOIN device_operation_deliveries delivery
                     ON delivery.delivery_id=g.delivery_id
                   JOIN device_host_registrations device
                     ON device.user_id=g.user_id AND device.device_id=g.device_id
                   JOIN runtime_runs run ON run.run_id=g.run_id
                   WHERE g.token_fingerprint=%s AND g.status='active'
                     AND g.expires_at>clock_timestamp() AND device.status='active'
                     AND delivery.status='claimed'
                     AND delivery.claim_expires_at>clock_timestamp()
                     AND run.status NOT IN ('completed','failed','cancelled','timed_out')""",
                (token_fingerprint,),
            ).fetchone()
        return self._host_model_grant(row) if row else None

    def rotate_device_host_model_grant_token(
        self, **values: Any
    ) -> HostModelGrantRecord | None:
        """Rotate a raw-token fingerprint only for the current fenced Device claim."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE host_model_grants g SET token_fingerprint=%s,
                           updated_at=clock_timestamp()
                       FROM device_operation_deliveries delivery
                       WHERE g.grant_id=%s AND g.user_id=%s AND g.device_id=%s
                         AND g.delivery_id=%s AND g.status='active'
                         AND delivery.delivery_id=g.delivery_id
                         AND delivery.status='claimed'
                         AND delivery.claim_session_id=%s
                         AND delivery.claim_version=%s
                         AND delivery.claim_expires_at>clock_timestamp()
                       RETURNING g.*""",
                (
                    values["token_fingerprint"],
                    values["grant_id"],
                    values["user_id"],
                    values["device_id"],
                    values["delivery_id"],
                    values["claim_session_id"],
                    values["claim_version"],
                ),
            ).fetchone()
        return self._host_model_grant(row) if row else None

    def reserve_host_model_budget(
        self, grant_id: str, **values: Any
    ) -> tuple[HostModelReservationRecord | None, bool]:
        with self._pool.connection() as conn, conn.transaction():
            grant = conn.execute(
                """SELECT g.* FROM host_model_grants g
                   JOIN device_operation_deliveries delivery
                     ON delivery.delivery_id=g.delivery_id
                   JOIN device_host_registrations device
                     ON device.user_id=g.user_id AND device.device_id=g.device_id
                   JOIN runtime_runs run ON run.run_id=g.run_id
                   WHERE g.grant_id=%s
                     AND device.status='active'
                     AND delivery.status='claimed'
                     AND delivery.claim_expires_at>clock_timestamp()
                     AND run.status NOT IN ('completed','failed','cancelled','timed_out')
                   FOR UPDATE OF g""",
                (grant_id,),
            ).fetchone()
            if grant is None:
                return None, False
            self._settle_stale_host_model_reservations(conn, grant_id)
            grant = conn.execute(
                "SELECT * FROM host_model_grants WHERE grant_id=%s FOR UPDATE",
                (grant_id,),
            ).fetchone()
            assert grant is not None
            existing = conn.execute(
                """SELECT * FROM host_model_reservations
                   WHERE grant_id=%s AND request_id=%s""",
                (grant_id, values["request_id"]),
            ).fetchone()
            if existing is not None:
                if (
                    int(existing["reserved_tokens"]) != values["reserved_tokens"]
                    or int(existing["reserved_cost_micros"])
                    != values["reserved_cost_micros"]
                ):
                    raise ValueError("Model request id was reused with a different reservation")
                return self._host_model_reservation(existing), False
            valid = (
                grant["status"] == "active"
                and grant["expires_at"] > conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                and int(grant["active_reservations"]) < int(grant["max_concurrent"])
                and int(grant["used_tokens"])
                + int(grant["reserved_tokens"])
                + values["reserved_tokens"]
                <= int(grant["token_budget"])
                and int(grant["used_cost_micros"])
                + int(grant["reserved_cost_micros"])
                + values["reserved_cost_micros"]
                <= int(grant["cost_budget_micros"])
            )
            if not valid:
                return None, False
            row = conn.execute(
                """INSERT INTO host_model_reservations
                       (reservation_id,grant_id,request_id,reserved_tokens,
                        reserved_cost_micros,expires_at)
                   VALUES (%s,%s,%s,%s,%s,clock_timestamp()+(%s * interval '1 second'))
                   RETURNING *""",
                (
                    values["reservation_id"],
                    grant_id,
                    values["request_id"],
                    values["reserved_tokens"],
                    values["reserved_cost_micros"],
                    values["reservation_seconds"],
                ),
            ).fetchone()
            conn.execute(
                """UPDATE host_model_grants SET reserved_tokens=reserved_tokens+%s,
                       reserved_cost_micros=reserved_cost_micros+%s,
                       active_reservations=active_reservations+1,updated_at=clock_timestamp()
                   WHERE grant_id=%s""",
                (values["reserved_tokens"], values["reserved_cost_micros"], grant_id),
            )
        return self._host_model_reservation(row), True

    def settle_host_model_budget(
        self, reservation_id: str, **values: Any
    ) -> HostModelReservationRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            reservation = conn.execute(
                """SELECT * FROM host_model_reservations
                   WHERE reservation_id=%s FOR UPDATE""",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                return None
            if reservation["status"] == "settled":
                if (
                    int(reservation["actual_tokens"] or 0) != values["actual_tokens"]
                    or int(reservation["actual_cost_micros"] or 0)
                    != values["actual_cost_micros"]
                ):
                    raise ValueError("Model reservation was settled with different usage")
                return self._host_model_reservation(reservation)
            if reservation["status"] != "reserved":
                return None
            if values["actual_tokens"] > int(reservation["reserved_tokens"]):
                raise ValueError("Actual model tokens exceed the reserved upper bound")
            if values["actual_cost_micros"] > int(reservation["reserved_cost_micros"]):
                raise ValueError("Actual model cost exceeds the reserved upper bound")
            row = conn.execute(
                """UPDATE host_model_reservations SET status='settled',actual_tokens=%s,
                       actual_cost_micros=%s,usage=%s,response=%s,
                       settled_at=clock_timestamp()
                   WHERE reservation_id=%s RETURNING *""",
                (
                    values["actual_tokens"],
                    values["actual_cost_micros"],
                    Jsonb(values.get("usage") or {}),
                    Jsonb(values["response"]) if values.get("response") else None,
                    reservation_id,
                ),
            ).fetchone()
            grant = conn.execute(
                """UPDATE host_model_grants SET
                       reserved_tokens=reserved_tokens-%s,
                       reserved_cost_micros=reserved_cost_micros-%s,
                       used_tokens=used_tokens+%s,used_cost_micros=used_cost_micros+%s,
                       active_reservations=active_reservations-1,
                       status=CASE WHEN used_tokens+%s>=token_budget
                                        OR (cost_budget_micros>0 AND
                                            used_cost_micros+%s>=cost_budget_micros)
                                   THEN 'exhausted' ELSE status END,
                       updated_at=clock_timestamp()
                   WHERE grant_id=%s RETURNING *""",
                (
                    reservation["reserved_tokens"],
                    reservation["reserved_cost_micros"],
                    values["actual_tokens"],
                    values["actual_cost_micros"],
                    values["actual_tokens"],
                    values["actual_cost_micros"],
                    reservation["grant_id"],
                ),
            ).fetchone()
            assert grant is not None
        return self._host_model_reservation(row)

    def release_host_model_budget(self, reservation_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            reservation = conn.execute(
                """UPDATE host_model_reservations SET status='released',
                       settled_at=clock_timestamp()
                   WHERE reservation_id=%s AND status='reserved' RETURNING *""",
                (reservation_id,),
            ).fetchone()
            if reservation is None:
                return False
            conn.execute(
                """UPDATE host_model_grants SET reserved_tokens=reserved_tokens-%s,
                       reserved_cost_micros=reserved_cost_micros-%s,
                       active_reservations=active_reservations-1,
                       updated_at=clock_timestamp() WHERE grant_id=%s""",
                (
                    reservation["reserved_tokens"],
                    reservation["reserved_cost_micros"],
                    reservation["grant_id"],
                ),
            )
        return True

    def revoke_host_model_grant(self, **values: Any) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE host_model_grants SET status='revoked',
                       token_fingerprint='revoked:'||grant_id,revoked_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE grant_id=%s AND user_id=%s AND status='active' RETURNING grant_id""",
                (values["grant_id"], values["user_id"]),
            ).fetchone()
        return row is not None

    def get_host_model_grant(
        self, grant_id: str, *, expected_user_id: str | None = None
    ) -> HostModelGrantRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM host_model_grants WHERE grant_id=%s
                     AND (%s::text IS NULL OR user_id=%s)""",
                (grant_id, expected_user_id, expected_user_id),
            ).fetchone()
        return self._host_model_grant(row) if row else None

    def list_host_model_grants(
        self, *, user_id: str, delivery_id: str | None = None, limit: int = 100
    ) -> list[HostModelGrantRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM host_model_grants WHERE user_id=%s
                     AND (%s::text IS NULL OR delivery_id=%s)
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, delivery_id, delivery_id, limit),
            ).fetchall()
        return [self._host_model_grant(row) for row in rows]

    @staticmethod
    def _settle_stale_host_model_reservations(conn: Any, grant_id: str) -> None:
        stale = conn.execute(
            """UPDATE host_model_reservations SET status='settled',
                       actual_tokens=reserved_tokens,
                       actual_cost_micros=reserved_cost_micros,
                       usage=jsonb_build_object('usage_status','missing',
                                                'billing_status','conservative_upper',
                                                'reason','gateway_reservation_expired'),
                       settled_at=clock_timestamp()
                 WHERE grant_id=%s AND status='reserved'
                   AND expires_at<=clock_timestamp()
                 RETURNING reserved_tokens,reserved_cost_micros""",
            (grant_id,),
        ).fetchall()
        if not stale:
            return
        tokens = sum(int(row["reserved_tokens"]) for row in stale)
        cost = sum(int(row["reserved_cost_micros"]) for row in stale)
        conn.execute(
            """UPDATE host_model_grants SET
                       reserved_tokens=GREATEST(0,reserved_tokens-%s),
                       reserved_cost_micros=GREATEST(0,reserved_cost_micros-%s),
                       used_tokens=used_tokens+%s,used_cost_micros=used_cost_micros+%s,
                       active_reservations=GREATEST(0,active_reservations-%s),
                       status=CASE WHEN used_tokens+%s>=token_budget
                                      OR (cost_budget_micros>0 AND
                                          used_cost_micros+%s>=cost_budget_micros)
                                   THEN 'exhausted' ELSE status END,
                       updated_at=clock_timestamp()
                 WHERE grant_id=%s""",
            (tokens, cost, tokens, cost, len(stale), tokens, cost, grant_id),
        )

    @staticmethod
    def _host_model_grant(row: dict[str, Any]) -> HostModelGrantRecord:
        from joyhousebot.storage.postgres_store import _iso

        return HostModelGrantRecord(
            grant_id=str(row["grant_id"]),user_id=str(row["user_id"]),
            run_id=str(row["run_id"]),task_id=row["task_id"],action_id=str(row["action_id"]),
            delivery_id=str(row["delivery_id"]),device_id=str(row["device_id"]),
            capability_ref=dict(row["capability_ref"] or {}),provider_id=str(row["provider_id"]),
            provider_revision_id=str(row["provider_revision_id"]),model_id=str(row["model_id"]),
            token_budget=int(row["token_budget"]),cost_budget_micros=int(row["cost_budget_micros"]),
            reserved_tokens=int(row["reserved_tokens"]),used_tokens=int(row["used_tokens"]),
            reserved_cost_micros=int(row["reserved_cost_micros"]),
            used_cost_micros=int(row["used_cost_micros"]),
            active_reservations=int(row["active_reservations"]),
            max_concurrent=int(row["max_concurrent"]),status=str(row["status"]),
            expires_at=_iso(row["expires_at"]) or "",created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",revoked_at=_iso(row["revoked_at"]),
        )

    @staticmethod
    def _host_model_reservation(row: dict[str, Any]) -> HostModelReservationRecord:
        from joyhousebot.storage.postgres_store import _iso

        return HostModelReservationRecord(
            reservation_id=str(row["reservation_id"]),grant_id=str(row["grant_id"]),
            request_id=str(row["request_id"]),reserved_tokens=int(row["reserved_tokens"]),
            reserved_cost_micros=int(row["reserved_cost_micros"]),
            actual_tokens=(int(row["actual_tokens"]) if row["actual_tokens"] is not None else None),
            actual_cost_micros=(int(row["actual_cost_micros"]) if row["actual_cost_micros"] is not None else None),
            status=str(row["status"]),usage=dict(row["usage"] or {}),
            response=(dict(row["response"]) if row["response"] is not None else None),
            created_at=_iso(row["created_at"]) or "",
            expires_at=_iso(row["expires_at"]) or "",
            settled_at=_iso(row["settled_at"]),
        )
