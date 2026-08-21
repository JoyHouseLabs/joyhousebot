"""PostgreSQL-backed first-party Owner delegation and refresh rotation."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from joyhousebot.domain.owner_delegation import (
    OWNER_ASSERTION_ALGORITHMS,
    normalize_owner_scopes,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresOwnerDelegationStoreMixin:
    def migrate_owner_delegation(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS owner_clients (
            client_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            issuer TEXT NOT NULL UNIQUE,
            public_key_pem TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            allowed_scopes JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            revoked_by TEXT
        );
        CREATE TABLE IF NOT EXISTS owner_delegations (
            delegation_id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES owner_clients(client_id),
            user_id TEXT NOT NULL,
            credential_version INTEGER NOT NULL DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            revoked_by TEXT,
            UNIQUE(client_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS owner_assertion_replays (
            client_id TEXT NOT NULL REFERENCES owner_clients(client_id),
            jti TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(client_id,jti)
        );
        CREATE TABLE IF NOT EXISTS owner_refresh_tokens (
            refresh_id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            delegation_id TEXT NOT NULL REFERENCES owner_delegations(delegation_id),
            scopes JSONB NOT NULL,
            credential_version INTEGER NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            consumed_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            replaced_by TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_owner_refresh_active
            ON owner_refresh_tokens(delegation_id,expires_at)
            WHERE consumed_at IS NULL AND revoked_at IS NULL;
        CREATE TABLE IF NOT EXISTS owner_delegation_events (
            sequence BIGSERIAL PRIMARY KEY,
            delegation_id TEXT,
            client_id TEXT NOT NULL,
            user_id TEXT,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS owner_client_id TEXT;
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS owner_delegation_id TEXT;
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS credential_version INTEGER;
        CREATE INDEX IF NOT EXISTS ix_api_access_tokens_owner_delegation
            ON api_access_tokens(owner_delegation_id,enabled)
            WHERE owner_delegation_id IS NOT NULL;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="owner_delegation",
                version=1,
                ddl=ddl,
                description="first-party Owner assertion, access and refresh credentials",
            )

    def create_owner_client(
        self,
        *,
        client_id: str,
        name: str,
        issuer: str,
        public_key_pem: str,
        algorithm: str,
        allowed_scopes: list[str] | tuple[str, ...],
        actor_id: str,
    ) -> dict[str, Any]:
        scopes = normalize_owner_scopes(allowed_scopes)
        if algorithm not in OWNER_ASSERTION_ALGORITHMS:
            raise ValueError("Owner assertion algorithm is not supported")
        if "PUBLIC KEY" not in public_key_pem or len(public_key_pem) > 16_384:
            raise ValueError("Owner client public key must be a bounded PEM public key")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO owner_clients
                       (client_id,name,issuer,public_key_pem,algorithm,allowed_scopes,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    client_id,
                    name.strip(),
                    issuer.strip(),
                    public_key_pem.strip(),
                    algorithm,
                    Jsonb(scopes),
                    actor_id,
                ),
            ).fetchone()
            self._owner_event(
                conn,
                client_id=client_id,
                actor_id=actor_id,
                event_type="client.created",
                data={"issuer": issuer, "allowed_scopes": list(scopes)},
            )
        return self._owner_client_dict(row)

    def list_owner_clients(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_clients ORDER BY created_at DESC"
            ).fetchall()
        # Public verification keys are intentionally returned so launchers can
        # reconcile first-party client policy without rotating credentials on
        # every restart. Private key material never enters Runtime storage.
        return [self._owner_client_dict(row, include_key=True) for row in rows]

    def get_owner_client_for_exchange(self, client_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM owner_clients WHERE client_id=%s AND enabled", (client_id,)
            ).fetchone()
        return self._owner_client_dict(row, include_key=True) if row else None

    def rotate_owner_client_key(
        self,
        client_id: str,
        *,
        public_key_pem: str,
        algorithm: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        if algorithm not in OWNER_ASSERTION_ALGORITHMS or "PUBLIC KEY" not in public_key_pem:
            raise ValueError("Owner client public key or algorithm is invalid")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE owner_clients SET public_key_pem=%s,algorithm=%s,
                          updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING *""",
                (public_key_pem.strip(), algorithm, client_id),
            ).fetchone()
            if row is None:
                return None
            self._revoke_client_credentials(conn, client_id, actor_id=actor_id)
            self._owner_event(
                conn,
                client_id=client_id,
                actor_id=actor_id,
                event_type="client.key_rotated",
            )
        return self._owner_client_dict(row)

    def update_owner_client(
        self,
        client_id: str,
        *,
        name: str,
        issuer: str,
        public_key_pem: str,
        algorithm: str,
        allowed_scopes: list[str] | tuple[str, ...],
        actor_id: str,
    ) -> dict[str, Any] | None:
        scopes = normalize_owner_scopes(allowed_scopes)
        if algorithm not in OWNER_ASSERTION_ALGORITHMS:
            raise ValueError("Owner assertion algorithm is not supported")
        if "PUBLIC KEY" not in public_key_pem or len(public_key_pem) > 16_384:
            raise ValueError("Owner client public key must be a bounded PEM public key")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE owner_clients
                   SET name=%s,issuer=%s,public_key_pem=%s,algorithm=%s,
                       allowed_scopes=%s,updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING *""",
                (
                    name.strip(),
                    issuer.strip(),
                    public_key_pem.strip(),
                    algorithm,
                    Jsonb(scopes),
                    client_id,
                ),
            ).fetchone()
            if row is None:
                return None
            self._revoke_client_credentials(conn, client_id, actor_id=actor_id)
            self._owner_event(
                conn,
                client_id=client_id,
                actor_id=actor_id,
                event_type="client.updated",
                data={"issuer": issuer.strip(), "allowed_scopes": list(scopes)},
            )
        return self._owner_client_dict(row)

    def revoke_owner_client(self, client_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE owner_clients SET enabled=FALSE,revoked_at=clock_timestamp(),
                          revoked_by=%s,updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING client_id""",
                (actor_id, client_id),
            ).fetchone()
            if row is None:
                return False
            self._revoke_client_credentials(conn, client_id, actor_id=actor_id)
            self._owner_event(
                conn,
                client_id=client_id,
                actor_id=actor_id,
                event_type="client.revoked",
            )
        return True

    def issue_owner_delegated_token(
        self,
        *,
        client_id: str,
        user_id: str,
        assertion_jti: str,
        assertion_expires_at: float,
        requested_scopes: list[str] | tuple[str, ...],
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> tuple[dict[str, Any], str, str] | None:
        scopes = normalize_owner_scopes(requested_scopes)
        with self._pool.connection() as conn, conn.transaction():
            client = conn.execute(
                "SELECT * FROM owner_clients WHERE client_id=%s AND enabled FOR UPDATE",
                (client_id,),
            ).fetchone()
            if client is None or not set(scopes) <= set(client["allowed_scopes"] or []):
                return None
            consumed = conn.execute(
                """INSERT INTO owner_assertion_replays(client_id,jti,expires_at)
                   VALUES (%s,%s,to_timestamp(%s)) ON CONFLICT DO NOTHING RETURNING jti""",
                (client_id, assertion_jti, assertion_expires_at),
            ).fetchone()
            if consumed is None:
                return None
            delegation = conn.execute(
                """INSERT INTO owner_delegations(delegation_id,client_id,user_id)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(client_id,user_id) DO UPDATE SET
                     credential_version=CASE WHEN owner_delegations.enabled
                       THEN owner_delegations.credential_version
                       ELSE owner_delegations.credential_version+1 END,
                     enabled=TRUE,revoked_at=NULL,revoked_by=NULL,
                     updated_at=clock_timestamp()
                   RETURNING *""",
                (f"ownergrant_{uuid4().hex}", client_id, user_id),
            ).fetchone()
            result = self._insert_owner_tokens(
                conn,
                client_id=client_id,
                delegation=delegation,
                scopes=scopes,
                ttl_seconds=ttl_seconds,
                refresh_ttl_seconds=refresh_ttl_seconds,
            )
            self._owner_event(
                conn,
                delegation_id=str(delegation["delegation_id"]),
                client_id=client_id,
                user_id=user_id,
                actor_id=f"owner-client:{client_id}",
                event_type="token.exchanged",
                data={"token_id": result[0]["token_id"], "scopes": list(scopes)},
            )
        return result

    def refresh_owner_delegated_token(
        self,
        *,
        client_id: str,
        refresh_token: str,
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> tuple[dict[str, Any], str, str] | None:
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT refresh.*,delegation.user_id,delegation.client_id,
                          delegation.enabled AS delegation_enabled,
                          delegation.credential_version AS current_version,
                          client.enabled AS client_enabled
                   FROM owner_refresh_tokens refresh
                   JOIN owner_delegations delegation USING(delegation_id)
                   JOIN owner_clients client ON client.client_id=delegation.client_id
                   WHERE refresh.token_hash=%s FOR UPDATE""",
                (digest,),
            ).fetchone()
            if row is None or str(row["client_id"]) != client_id:
                return None
            reusable = (
                row["consumed_at"] is None
                and row["revoked_at"] is None
                and row["expires_at"] > datetime.now(row["expires_at"].tzinfo)
                and row["delegation_enabled"]
                and row["client_enabled"]
                and int(row["credential_version"]) == int(row["current_version"])
            )
            if not reusable:
                if row["consumed_at"] is not None:
                    self._revoke_delegation_credentials(
                        conn,
                        str(row["delegation_id"]),
                        actor_id=f"refresh-reuse:{client_id}",
                    )
                return None
            delegation = conn.execute(
                "SELECT * FROM owner_delegations WHERE delegation_id=%s FOR UPDATE",
                (row["delegation_id"],),
            ).fetchone()
            result = self._insert_owner_tokens(
                conn,
                client_id=client_id,
                delegation=delegation,
                scopes=tuple(str(item) for item in row["scopes"]),
                ttl_seconds=ttl_seconds,
                refresh_ttl_seconds=refresh_ttl_seconds,
            )
            conn.execute(
                """UPDATE owner_refresh_tokens SET consumed_at=clock_timestamp(),replaced_by=%s
                   WHERE refresh_id=%s""",
                (result[0]["refresh_id"], row["refresh_id"]),
            )
            self._owner_event(
                conn,
                delegation_id=str(row["delegation_id"]),
                client_id=client_id,
                user_id=str(row["user_id"]),
                actor_id=f"owner-client:{client_id}",
                event_type="token.refreshed",
                data={"token_id": result[0]["token_id"]},
            )
        return result

    def revoke_owner_delegation(
        self, *, client_id: str, user_id: str, actor_id: str
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT delegation_id FROM owner_delegations
                   WHERE client_id=%s AND user_id=%s AND enabled FOR UPDATE""",
                (client_id, user_id),
            ).fetchone()
            if row is None:
                return False
            delegation_id = str(row["delegation_id"])
            self._revoke_delegation_credentials(conn, delegation_id, actor_id=actor_id)
            self._owner_event(
                conn,
                delegation_id=delegation_id,
                client_id=client_id,
                user_id=user_id,
                actor_id=actor_id,
                event_type="delegation.revoked",
            )
        return True

    def _insert_owner_tokens(
        self,
        conn: Any,
        *,
        client_id: str,
        delegation: Any,
        scopes: tuple[str, ...],
        ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> tuple[dict[str, Any], str, str]:
        now = datetime.now(timezone.utc)
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(48)
        token_id = f"tok_{uuid4().hex}"
        refresh_id = f"refresh_{uuid4().hex}"
        access_expiry = now + timedelta(seconds=max(60, min(ttl_seconds, 3600)))
        refresh_expiry = now + timedelta(
            seconds=max(300, min(refresh_ttl_seconds, 30 * 24 * 3600))
        )
        token_row = conn.execute(
            """INSERT INTO api_access_tokens
                   (token_id,token_hash,user_id,label,expires_at,scopes,token_type,
                    principal_kind,created_by,owner_client_id,owner_delegation_id,
                    credential_version)
               VALUES (%s,%s,%s,%s,%s,%s,'service','owner',%s,%s,%s,%s) RETURNING *""",
            (
                token_id,
                hashlib.sha256(access.encode()).hexdigest(),
                delegation["user_id"],
                f"owner:{client_id}",
                access_expiry,
                Jsonb(scopes),
                f"owner-client:{client_id}",
                client_id,
                delegation["delegation_id"],
                delegation["credential_version"],
            ),
        ).fetchone()
        conn.execute(
            """INSERT INTO owner_refresh_tokens
                   (refresh_id,token_hash,delegation_id,scopes,credential_version,expires_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                refresh_id,
                hashlib.sha256(refresh.encode()).hexdigest(),
                delegation["delegation_id"],
                Jsonb(scopes),
                delegation["credential_version"],
                refresh_expiry,
            ),
        )
        record = self._api_access_token(token_row)
        record["refresh_id"] = refresh_id
        record["refresh_expires_at"] = refresh_expiry.isoformat()
        return record, access, refresh

    def _revoke_client_credentials(self, conn: Any, client_id: str, *, actor_id: str) -> None:
        rows = conn.execute(
            "SELECT delegation_id FROM owner_delegations WHERE client_id=%s FOR UPDATE",
            (client_id,),
        ).fetchall()
        for row in rows:
            self._revoke_delegation_credentials(
                conn, str(row["delegation_id"]), actor_id=actor_id
            )

    @staticmethod
    def _revoke_delegation_credentials(
        conn: Any, delegation_id: str, *, actor_id: str
    ) -> None:
        conn.execute(
            """UPDATE owner_delegations SET enabled=FALSE,
                      credential_version=credential_version+1,revoked_at=clock_timestamp(),
                      revoked_by=%s,updated_at=clock_timestamp()
               WHERE delegation_id=%s""",
            (actor_id, delegation_id),
        )
        conn.execute(
            """UPDATE api_access_tokens SET enabled=FALSE,revoked_at=clock_timestamp(),
                      revoked_by=%s WHERE owner_delegation_id=%s AND enabled""",
            (actor_id, delegation_id),
        )
        conn.execute(
            """UPDATE owner_refresh_tokens SET revoked_at=clock_timestamp()
               WHERE delegation_id=%s AND revoked_at IS NULL""",
            (delegation_id,),
        )

    @staticmethod
    def _owner_event(
        conn: Any,
        *,
        client_id: str,
        actor_id: str,
        event_type: str,
        delegation_id: str | None = None,
        user_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO owner_delegation_events
                   (delegation_id,client_id,user_id,actor_id,event_type,data)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (delegation_id, client_id, user_id, actor_id, event_type, Jsonb(data or {})),
        )

    @staticmethod
    def _owner_client_dict(row: Any, *, include_key: bool = False) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        value = {
            "client_id": str(row["client_id"]),
            "name": str(row["name"]),
            "issuer": str(row["issuer"]),
            "algorithm": str(row["algorithm"]),
            "allowed_scopes": [str(item) for item in row["allowed_scopes"]],
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_by": row["revoked_by"],
        }
        if include_key:
            value["public_key_pem"] = str(row["public_key_pem"])
        return value


__all__ = ["PostgresOwnerDelegationStoreMixin"]
