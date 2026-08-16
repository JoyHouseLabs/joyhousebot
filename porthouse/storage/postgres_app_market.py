"""PostgreSQL projection for trusted Markets and local App acquisitions."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb


class PostgresAppMarketStoreMixin:
    def migrate_app_market(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS app_market_registries (
            registry_id TEXT PRIMARY KEY,market_id TEXT NOT NULL UNIQUE,base_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',protocol_version TEXT NOT NULL DEFAULT '1.0',
            trusted_root JSONB NOT NULL,discovery JSONB NOT NULL DEFAULT '{}'::jsonb,
            auth_token_ref TEXT NOT NULL DEFAULT '',policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),last_refreshed_at TIMESTAMPTZ,
            CHECK(status IN ('active','disabled','compromised'))
        );
        CREATE TABLE IF NOT EXISTS app_market_installation_keys (
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,key_id TEXT NOT NULL,public_key TEXT NOT NULL,
            key_thumbprint TEXT NOT NULL,private_ciphertext TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),rotated_at TIMESTAMPTZ,
            PRIMARY KEY(registry_id,user_id),CHECK(status IN ('active','retired','revoked'))
        );
        CREATE TABLE IF NOT EXISTS app_acquisitions (
            acquisition_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id),
            publisher_id TEXT NOT NULL,app_id TEXT NOT NULL,requested_version TEXT,
            resolved_version TEXT,channel TEXT NOT NULL DEFAULT 'stable',offer_id TEXT,
            acquisition_policy TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'requested',request_key TEXT NOT NULL,
            resolution JSONB NOT NULL DEFAULT '{}'::jsonb,release_descriptor JSONB NOT NULL DEFAULT '{}'::jsonb,
            app_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,verification_report JSONB NOT NULL DEFAULT '{}'::jsonb,
            permission_diff JSONB NOT NULL DEFAULT '{}'::jsonb,bundle_digest TEXT,bundle BYTEA,
            entitlement_id TEXT,provided_entitlement JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),finished_at TIMESTAMPTZ,
            lease_owner TEXT,lease_expires_at TIMESTAMPTZ,lease_version BIGINT NOT NULL DEFAULT 0,
            UNIQUE(user_id,registry_id,request_key),
            CHECK(channel IN ('stable','beta','security')),
            CHECK(status IN ('requested','resolving','fetching','verifying','staged',
                'awaiting_acceptance','imported','rejected','quarantined','failed'))
        );
        CREATE INDEX IF NOT EXISTS ix_app_acquisitions_claim
            ON app_acquisitions(status,created_at)
            WHERE status IN ('requested','resolving','fetching','verifying');
        CREATE INDEX IF NOT EXISTS ix_app_acquisitions_owner
            ON app_acquisitions(user_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS app_acquisition_events (
            event_id BIGSERIAL PRIMARY KEY,acquisition_id TEXT NOT NULL,
            user_id TEXT NOT NULL,event_type TEXT NOT NULL,actor_id TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_app_acquisition_events
            ON app_acquisition_events(acquisition_id,event_id);
        CREATE TABLE IF NOT EXISTS app_market_entitlements (
            entitlement_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id),
            publisher_id TEXT NOT NULL,app_id TEXT NOT NULL,status TEXT NOT NULL,
            installation_key_thumbprint TEXT NOT NULL,payload JSONB NOT NULL,envelope JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,offline_until TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK(status IN ('active','expired','suspended','refunded','chargeback','revoked'))
        );
        CREATE INDEX IF NOT EXISTS ix_app_market_entitlements_owner
            ON app_market_entitlements(user_id,registry_id,app_id,status);
        CREATE TABLE IF NOT EXISTS app_update_subscriptions (
            subscription_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,installation_id TEXT NOT NULL,
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id),
            publisher_id TEXT NOT NULL,app_id TEXT NOT NULL,channel TEXT NOT NULL,
            version_constraint TEXT NOT NULL DEFAULT '*',policy TEXT NOT NULL DEFAULT 'notify',
            allow_security_patch_download BOOLEAN NOT NULL DEFAULT TRUE,
            allow_auto_stage BOOLEAN NOT NULL DEFAULT FALSE,allow_auto_activate BOOLEAN NOT NULL DEFAULT FALSE,
            cursor TEXT NOT NULL DEFAULT '0',last_snapshot_version BIGINT NOT NULL DEFAULT 0,
            latest_release JSONB NOT NULL DEFAULT '{}'::jsonb,last_error JSONB,
            last_checked_at TIMESTAMPTZ,next_check_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            lease_owner TEXT,lease_expires_at TIMESTAMPTZ,lease_version BIGINT NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(user_id,installation_id),CHECK(channel IN ('stable','beta','security')),
            CHECK(policy IN ('notify','download','stage','activate_safe')),
            CHECK(status IN ('active','paused','removed'))
        );
        CREATE TABLE IF NOT EXISTS app_update_subscription_events (
            event_id BIGSERIAL PRIMARY KEY,subscription_id TEXT NOT NULL,
            user_id TEXT NOT NULL,event_type TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_app_update_subscription_events
            ON app_update_subscription_events(subscription_id,event_id);
        ALTER TABLE app_update_subscriptions
            ADD COLUMN IF NOT EXISTS latest_release JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS last_error JSONB,
            ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS next_check_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN IF NOT EXISTS lease_owner TEXT,
            ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS lease_version BIGINT NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS ix_app_update_subscriptions_due
            ON app_update_subscriptions(status,next_check_at)
            WHERE status='active';
        ALTER TABLE app_releases
            ADD COLUMN IF NOT EXISTS origin_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS bundle_digest TEXT NOT NULL DEFAULT '';
        ALTER TABLE app_acquisitions
            ADD COLUMN IF NOT EXISTS acquisition_policy TEXT NOT NULL DEFAULT 'manual';
        ALTER TABLE app_acquisitions
            ADD COLUMN IF NOT EXISTS provided_entitlement JSONB NOT NULL DEFAULT '{}'::jsonb;
        """
        receipt_signatures_ddl = """
        CREATE TABLE IF NOT EXISTS app_market_installation_receipt_signatures (
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id),
            user_id TEXT NOT NULL,
            receipt_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            payload JSONB NOT NULL,
            envelope JSONB NOT NULL,
            actor_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(registry_id,user_id,receipt_id)
        );
        """
        installation_grants_ddl = """
        CREATE TABLE IF NOT EXISTS app_market_installation_grant_consumptions (
            registry_id TEXT NOT NULL REFERENCES app_market_registries(registry_id),
            user_id TEXT NOT NULL,
            grant_id TEXT NOT NULL,
            acquisition_id TEXT NOT NULL REFERENCES app_acquisitions(acquisition_id),
            request_hash TEXT NOT NULL,
            local_installation_id TEXT NOT NULL,
            result JSONB NOT NULL,
            actor_id TEXT NOT NULL,
            consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(registry_id,user_id,grant_id)
        );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="app_market",
                version=1,
                ddl=ddl,
                description="trusted registries, acquisitions, entitlements, and updates",
            )
            conn.execute(receipt_signatures_ddl)
            self._record_migration(
                conn,
                name="app_market",
                version=2,
                ddl=receipt_signatures_ddl,
                description="durable idempotent installation receipt signatures",
            )
            conn.execute(installation_grants_ddl)
            self._record_migration(
                conn,
                name="app_market",
                version=3,
                ddl=installation_grants_ddl,
                description="durable idempotent Installation Grant consumption",
            )

    def save_app_market_registry(
        self,
        *,
        registry_id: str,
        market_id: str,
        base_url: str,
        trusted_root: dict[str, Any],
        discovery: dict[str, Any],
        auth_token_ref: str,
        policy: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO app_market_registries
                       (registry_id,market_id,base_url,trusted_root,discovery,auth_token_ref,
                        policy,created_by,last_refreshed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())
                   ON CONFLICT(market_id) DO UPDATE SET base_url=EXCLUDED.base_url,
                     trusted_root=EXCLUDED.trusted_root,discovery=EXCLUDED.discovery,
                     auth_token_ref=EXCLUDED.auth_token_ref,policy=EXCLUDED.policy,
                     updated_at=clock_timestamp(),last_refreshed_at=clock_timestamp()""",
                (
                    registry_id,
                    market_id,
                    base_url,
                    Jsonb(trusted_root),
                    Jsonb(discovery),
                    auth_token_ref,
                    Jsonb(policy),
                    actor_id,
                ),
            )
        value = self.get_app_market_registry(market_id=market_id)
        assert value is not None
        return value

    def list_app_market_registries(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM app_market_registries ORDER BY market_id"
            ).fetchall()
        return [self._market_registry_dict(row) for row in rows]

    def record_app_market_refresh(
        self,
        registry_id: str,
        *,
        discovery: dict[str, Any],
        policy: dict[str, Any],
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_market_registries SET discovery=%s,policy=%s,
                     last_refreshed_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE registry_id=%s AND status='active' RETURNING registry_id""",
                (Jsonb(discovery), Jsonb(policy), registry_id),
            ).fetchone()
            if row is None:
                raise ValueError("active Market Registry not found")

    def get_app_market_registry(
        self, *, registry_id: str | None = None, market_id: str | None = None
    ) -> dict[str, Any] | None:
        if not registry_id and not market_id:
            raise ValueError("registry_id or market_id is required")
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM app_market_registries
                   WHERE (%s::text IS NOT NULL AND registry_id=%s)
                      OR (%s::text IS NOT NULL AND market_id=%s) LIMIT 1""",
                (registry_id, registry_id, market_id, market_id),
            ).fetchone()
        return self._market_registry_dict(row) if row else None

    @staticmethod
    def _market_registry_dict(row: Any) -> dict[str, Any]:
        return {
            "registry_id": str(row["registry_id"]),
            "market_id": str(row["market_id"]),
            "base_url": str(row["base_url"]),
            "status": str(row["status"]),
            "protocol_version": str(row["protocol_version"]),
            "trusted_root": dict(row["trusted_root"]),
            "discovery": dict(row["discovery"]),
            "auth_token_ref": str(row["auth_token_ref"]),
            "policy": dict(row["policy"]),
            "updated_at": str(row["updated_at"]),
            "last_refreshed_at": (
                str(row["last_refreshed_at"]) if row["last_refreshed_at"] else None
            ),
        }

    def save_app_market_installation_key(
        self,
        *,
        registry_id: str,
        user_id: str,
        key_id: str,
        public_key: str,
        key_thumbprint: str,
        private_ciphertext: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO app_market_installation_keys
                       (registry_id,user_id,key_id,public_key,key_thumbprint,private_ciphertext)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(registry_id,user_id) DO NOTHING""",
                (
                    registry_id,
                    user_id,
                    key_id,
                    public_key,
                    key_thumbprint,
                    private_ciphertext,
                ),
            )
        value = self.get_app_market_installation_key(registry_id, user_id=user_id)
        assert value is not None
        return value

    def get_app_market_installation_key(
        self, registry_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM app_market_installation_keys
                   WHERE registry_id=%s AND user_id=%s""",
                (registry_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "registry_id": str(row["registry_id"]),
            "user_id": str(row["user_id"]),
            "key_id": str(row["key_id"]),
            "public_key": str(row["public_key"]),
            "key_thumbprint": str(row["key_thumbprint"]),
            "private_ciphertext": str(row["private_ciphertext"]),
            "status": str(row["status"]),
        }

    def get_app_market_receipt_signature(
        self, *, registry_id: str, user_id: str, receipt_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM app_market_installation_receipt_signatures
                   WHERE registry_id=%s AND user_id=%s AND receipt_id=%s""",
                (registry_id, user_id, receipt_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "registry_id": str(row["registry_id"]),
            "user_id": str(row["user_id"]),
            "receipt_id": str(row["receipt_id"]),
            "request_hash": str(row["request_hash"]),
            "payload": dict(row["payload"]),
            "envelope": dict(row["envelope"]),
            "actor_id": str(row["actor_id"]),
        }

    def save_app_market_receipt_signature(
        self,
        *,
        registry_id: str,
        user_id: str,
        receipt_id: str,
        request_hash: str,
        payload: dict[str, Any],
        envelope: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO app_market_installation_receipt_signatures
                       (registry_id,user_id,receipt_id,request_hash,payload,envelope,actor_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(registry_id,user_id,receipt_id) DO NOTHING""",
                (
                    registry_id,
                    user_id,
                    receipt_id,
                    request_hash,
                    Jsonb(payload),
                    Jsonb(envelope),
                    actor_id,
                ),
            )
        saved = self.get_app_market_receipt_signature(
            registry_id=registry_id,
            user_id=user_id,
            receipt_id=receipt_id,
        )
        assert saved is not None
        if saved["request_hash"] != request_hash:
            raise ValueError("installation receipt signing idempotency conflict")
        return saved

    def get_app_market_grant_consumption(
        self, *, registry_id: str, user_id: str, grant_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM app_market_installation_grant_consumptions
                   WHERE registry_id=%s AND user_id=%s AND grant_id=%s""",
                (registry_id, user_id, grant_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "registry_id": str(row["registry_id"]),
            "user_id": str(row["user_id"]),
            "grant_id": str(row["grant_id"]),
            "acquisition_id": str(row["acquisition_id"]),
            "request_hash": str(row["request_hash"]),
            "local_installation_id": str(row["local_installation_id"]),
            "result": dict(row["result"]),
            "actor_id": str(row["actor_id"]),
        }

    def save_app_market_grant_consumption(
        self,
        *,
        registry_id: str,
        user_id: str,
        grant_id: str,
        acquisition_id: str,
        request_hash: str,
        local_installation_id: str,
        result: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO app_market_installation_grant_consumptions
                       (registry_id,user_id,grant_id,acquisition_id,request_hash,
                        local_installation_id,result,actor_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(registry_id,user_id,grant_id) DO NOTHING""",
                (
                    registry_id,
                    user_id,
                    grant_id,
                    acquisition_id,
                    request_hash,
                    local_installation_id,
                    Jsonb(result),
                    actor_id,
                ),
            )
        saved = self.get_app_market_grant_consumption(
            registry_id=registry_id, user_id=user_id, grant_id=grant_id
        )
        assert saved is not None
        if saved["request_hash"] != request_hash:
            raise ValueError("Installation Grant idempotency conflict")
        return saved

    def create_app_acquisition(
        self,
        *,
        acquisition_id: str,
        user_id: str,
        registry_id: str,
        publisher_id: str,
        app_id: str,
        requested_version: str | None,
        channel: str,
        offer_id: str | None,
        acquisition_policy: str,
        request_key: str,
        actor_id: str,
        provided_entitlement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO app_acquisitions
                       (acquisition_id,user_id,registry_id,publisher_id,app_id,
                        requested_version,channel,offer_id,acquisition_policy,request_key,created_by,
                        provided_entitlement)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id,registry_id,request_key) DO UPDATE SET
                     request_key=app_acquisitions.request_key
                   RETURNING *""",
                (
                    acquisition_id,
                    user_id,
                    registry_id,
                    publisher_id,
                    app_id,
                    requested_version,
                    channel,
                    offer_id,
                    acquisition_policy,
                    request_key,
                    actor_id,
                    Jsonb(provided_entitlement or {}),
                ),
            ).fetchone()
            expected = {
                "publisher_id": publisher_id,
                "app_id": app_id,
                "requested_version": requested_version or "",
                "channel": channel,
                "offer_id": offer_id or "",
                "acquisition_policy": acquisition_policy,
            }
            actual = {
                "publisher_id": str(row["publisher_id"]),
                "app_id": str(row["app_id"]),
                "requested_version": str(row["requested_version"] or ""),
                "channel": str(row["channel"]),
                "offer_id": str(row["offer_id"] or ""),
                "acquisition_policy": str(row["acquisition_policy"]),
            }
            if actual != expected or dict(row["provided_entitlement"] or {}) != dict(
                provided_entitlement or {}
            ):
                raise ValueError("Acquisition Idempotency-Key was reused with another request")
            resolved_id = str(row["acquisition_id"])
            self._append_app_acquisition_event(
                conn,
                acquisition_id=resolved_id,
                user_id=user_id,
                event_type="requested",
                actor_id=actor_id,
                details={"request_key": request_key},
                once=True,
            )
        value = self.get_app_acquisition(resolved_id, user_id=user_id)
        assert value is not None
        return value

    def claim_app_acquisition(
        self, *, worker_id: str, lease_seconds: int = 120
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT acquisition_id FROM app_acquisitions
                   WHERE status IN ('requested','resolving','fetching','verifying')
                     AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp())
                   ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            claimed = conn.execute(
                """UPDATE app_acquisitions SET lease_owner=%s,
                     lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                     lease_version=lease_version+1,updated_at=clock_timestamp()
                   WHERE acquisition_id=%s RETURNING *""",
                (worker_id, max(30, lease_seconds), row["acquisition_id"]),
            ).fetchone()
        return self._app_acquisition_dict(claimed)

    def advance_app_acquisition(
        self,
        acquisition_id: str,
        *,
        worker_id: str,
        lease_version: int,
        status: str,
        details: dict[str, Any] | None = None,
        values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed_columns = {
            "resolution",
            "release_descriptor",
            "app_manifest",
            "verification_report",
            "permission_diff",
            "bundle_digest",
            "bundle",
            "resolved_version",
            "entitlement_id",
            "error",
        }
        updates = dict(values or {})
        unknown = set(updates) - allowed_columns
        if unknown:
            raise ValueError(f"unsupported Acquisition fields: {sorted(unknown)}")
        assignments = ["status=%s", "updated_at=clock_timestamp()"]
        parameters: list[Any] = [status]
        for name, value in updates.items():
            assignments.append(f"{name}=%s")
            parameters.append(Jsonb(value) if isinstance(value, (dict, list)) else value)
        if status in {"awaiting_acceptance", "imported", "rejected", "quarantined", "failed"}:
            assignments.extend(
                ["finished_at=clock_timestamp()", "lease_owner=NULL", "lease_expires_at=NULL"]
            )
        parameters.extend([acquisition_id, worker_id, lease_version])
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                f"""UPDATE app_acquisitions SET {', '.join(assignments)}
                    WHERE acquisition_id=%s AND lease_owner=%s AND lease_version=%s
                    RETURNING user_id""",
                parameters,
            ).fetchone()
            if row is None:
                raise ValueError("Acquisition lease was fenced")
            self._append_app_acquisition_event(
                conn,
                acquisition_id=acquisition_id,
                user_id=str(row["user_id"]),
                event_type=status,
                actor_id=f"worker:{worker_id}",
                details=details or {},
            )
        value = self.get_app_acquisition(acquisition_id)
        assert value is not None
        return value

    def finish_app_acquisition_action(
        self,
        acquisition_id: str,
        *,
        user_id: str,
        status: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_acquisitions SET status=%s,finished_at=clock_timestamp(),
                     updated_at=clock_timestamp() WHERE acquisition_id=%s AND user_id=%s
                     AND status='awaiting_acceptance' RETURNING acquisition_id""",
                (status, acquisition_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError("Acquisition is not awaiting acceptance")
            self._append_app_acquisition_event(
                conn,
                acquisition_id=acquisition_id,
                user_id=user_id,
                event_type=status,
                actor_id=actor_id,
                details=details or {},
            )
        value = self.get_app_acquisition(acquisition_id, user_id=user_id)
        assert value is not None
        return value

    def get_app_acquisition(
        self, acquisition_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM app_acquisitions WHERE acquisition_id=%s
                   AND (%s::text IS NULL OR user_id=%s)""",
                (acquisition_id, user_id, user_id),
            ).fetchone()
        return self._app_acquisition_dict(row) if row else None

    def list_app_acquisitions(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM app_acquisitions WHERE user_id=%s
                   ORDER BY updated_at DESC LIMIT 200""",
                (user_id,),
            ).fetchall()
        return [self._app_acquisition_dict(row) for row in rows]

    def get_app_acquisition_bundle(
        self, acquisition_id: str, *, user_id: str
    ) -> bytes | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT bundle FROM app_acquisitions
                   WHERE acquisition_id=%s AND user_id=%s""",
                (acquisition_id, user_id),
            ).fetchone()
        return bytes(row["bundle"]) if row and row["bundle"] is not None else None

    @staticmethod
    def _app_acquisition_dict(row: Any) -> dict[str, Any]:
        return {
            "acquisition_id": str(row["acquisition_id"]),
            "user_id": str(row["user_id"]),
            "registry_id": str(row["registry_id"]),
            "publisher_id": str(row["publisher_id"]),
            "app_id": str(row["app_id"]),
            "requested_version": str(row["requested_version"] or ""),
            "resolved_version": str(row["resolved_version"] or ""),
            "channel": str(row["channel"]),
            "offer_id": str(row["offer_id"] or ""),
            "acquisition_policy": str(row["acquisition_policy"]),
            "status": str(row["status"]),
            "request_key": str(row["request_key"]),
            "resolution": dict(row["resolution"] or {}),
            "release_descriptor": dict(row["release_descriptor"] or {}),
            "app_manifest": dict(row["app_manifest"] or {}),
            "verification_report": dict(row["verification_report"] or {}),
            "permission_diff": dict(row["permission_diff"] or {}),
            "bundle_digest": str(row["bundle_digest"] or ""),
            "entitlement_id": str(row["entitlement_id"] or ""),
            "provided_entitlement": dict(row["provided_entitlement"] or {}),
            "error": dict(row["error"] or {}) if row["error"] else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
            "lease_owner": str(row["lease_owner"] or ""),
            "lease_version": int(row["lease_version"]),
        }

    def list_app_acquisition_events(
        self, acquisition_id: str, *, user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM app_acquisition_events
                   WHERE acquisition_id=%s AND user_id=%s ORDER BY event_id""",
                (acquisition_id, user_id),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "details": dict(row["details"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _append_app_acquisition_event(
        conn: Any,
        *,
        acquisition_id: str,
        user_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any],
        once: bool = False,
    ) -> None:
        if once:
            exists = conn.execute(
                """SELECT 1 FROM app_acquisition_events
                   WHERE acquisition_id=%s AND event_type=%s LIMIT 1""",
                (acquisition_id, event_type),
            ).fetchone()
            if exists:
                return
        conn.execute(
            """INSERT INTO app_acquisition_events
                   (acquisition_id,user_id,event_type,actor_id,details)
               VALUES (%s,%s,%s,%s,%s)""",
            (acquisition_id, user_id, event_type, actor_id, Jsonb(details)),
        )

    def set_app_release_origin(
        self,
        app_id: str,
        version: str,
        *,
        origin_ref: dict[str, Any],
        bundle_digest: str,
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_releases SET origin_ref=%s,bundle_digest=%s,
                     updated_at=clock_timestamp() WHERE app_id=%s AND version=%s
                     AND status='draft' RETURNING app_id""",
                (Jsonb(origin_ref), bundle_digest, app_id, version),
            ).fetchone()
            if row is None:
                raise ValueError("imported App draft not found")

    def save_app_market_entitlement(
        self,
        *,
        user_id: str,
        registry_id: str,
        publisher_id: str,
        app_id: str,
        payload: dict[str, Any],
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        subject = dict(payload["subject"])
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO app_market_entitlements
                       (entitlement_id,user_id,registry_id,publisher_id,app_id,status,
                        installation_key_thumbprint,payload,envelope,expires_at,offline_until)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(entitlement_id) DO UPDATE SET status=EXCLUDED.status,
                     payload=EXCLUDED.payload,envelope=EXCLUDED.envelope,
                     expires_at=EXCLUDED.expires_at,offline_until=EXCLUDED.offline_until,
                     updated_at=clock_timestamp()""",
                (
                    payload["entitlement_id"],
                    user_id,
                    registry_id,
                    publisher_id,
                    app_id,
                    payload["status"],
                    subject["installation_key_thumbprint"],
                    Jsonb(payload),
                    Jsonb(envelope),
                    payload["expires_at"],
                    payload["offline_until"],
                ),
            )
        return {"payload": payload, "envelope": envelope}
