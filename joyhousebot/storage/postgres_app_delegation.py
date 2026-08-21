"""PostgreSQL App clients, user grants, short-lived tokens, and audit."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from joyhousebot.domain.app_delegation import (
    installation_scope_ceiling,
    normalize_app_scopes,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresAppDelegationStoreMixin:
    def migrate_app_delegation(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS app_clients (
            client_id TEXT PRIMARY KEY,
            app_id TEXT NOT NULL REFERENCES app_definitions(app_id),
            name TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            allowed_scopes JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            last_used_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            revoked_by TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_app_clients_app
            ON app_clients(app_id,enabled,created_at DESC);
        CREATE TABLE IF NOT EXISTS app_client_events (
            sequence BIGSERIAL PRIMARY KEY,
            client_id TEXT NOT NULL,
            app_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS app_delegation_grants (
            grant_id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES app_clients(client_id),
            installation_id TEXT NOT NULL REFERENCES app_installations(installation_id),
            user_id TEXT NOT NULL,
            scopes JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            revoked_at TIMESTAMPTZ,
            revoked_by TEXT,
            UNIQUE(client_id,installation_id)
        );
        CREATE INDEX IF NOT EXISTS ix_app_delegation_grants_owner
            ON app_delegation_grants(user_id,installation_id,enabled,created_at DESC);
        CREATE TABLE IF NOT EXISTS app_delegation_events (
            sequence BIGSERIAL PRIMARY KEY,
            grant_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            installation_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS app_client_id TEXT;
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS delegation_grant_id TEXT;
        ALTER TABLE api_access_tokens
            ADD COLUMN IF NOT EXISTS app_installation_id TEXT;
        CREATE INDEX IF NOT EXISTS ix_api_access_tokens_delegation
            ON api_access_tokens(delegation_grant_id,enabled)
            WHERE delegation_grant_id IS NOT NULL;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="app_delegation",
                version=1,
                ddl=ddl,
                description="App clients, owner grants, subject-bound tokens, and audit",
            )

    def create_app_client(
        self,
        *,
        app_id: str,
        name: str,
        allowed_scopes: list[str] | tuple[str, ...],
        actor_id: str,
    ) -> tuple[dict[str, Any], str]:
        scopes = normalize_app_scopes(allowed_scopes)
        client_id = f"appclient_{uuid4().hex}"
        plaintext = f"jhapp_{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            app = conn.execute(
                "SELECT app_id FROM app_definitions WHERE app_id=%s AND status='active'",
                (app_id,),
            ).fetchone()
            if app is None:
                raise ValueError("active App definition not found")
            row = conn.execute(
                """INSERT INTO app_clients
                       (client_id,app_id,name,secret_hash,allowed_scopes,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    client_id,
                    app_id,
                    str(name).strip()[:160],
                    digest,
                    Jsonb(scopes),
                    actor_id,
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO app_client_events
                       (client_id,app_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,'client.created',%s)""",
                (client_id, app_id, actor_id, Jsonb({"allowed_scopes": list(scopes)})),
            )
        return self._app_client_dict(row), plaintext

    def list_app_clients(self, *, app_id: str | None = None) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            if app_id:
                rows = conn.execute(
                    "SELECT * FROM app_clients WHERE app_id=%s ORDER BY created_at DESC",
                    (app_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM app_clients ORDER BY created_at DESC"
                ).fetchall()
        return [self._app_client_dict(row) for row in rows]

    def rotate_app_client_secret(
        self, client_id: str, *, actor_id: str
    ) -> tuple[dict[str, Any], str] | None:
        plaintext = f"jhapp_{secrets.token_urlsafe(32)}"
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_clients SET secret_hash=%s,updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING *""",
                (digest, client_id),
            ).fetchone()
            if row is None:
                return None
            revoked = conn.execute(
                """UPDATE api_access_tokens SET enabled=FALSE,
                          revoked_at=clock_timestamp(),revoked_by=%s
                   WHERE app_client_id=%s AND enabled RETURNING token_id""",
                (actor_id, client_id),
            ).fetchall()
            conn.execute(
                """INSERT INTO app_client_events
                       (client_id,app_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,'client.secret_rotated',%s)""",
                (
                    client_id,
                    row["app_id"],
                    actor_id,
                    Jsonb({"revoked_token_count": len(revoked)}),
                ),
            )
        return self._app_client_dict(row), plaintext

    def revoke_app_client(self, client_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_clients SET enabled=FALSE,revoked_at=clock_timestamp(),
                          revoked_by=%s,updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING app_id""",
                (actor_id, client_id),
            ).fetchone()
            if row is None:
                return False
            grants = conn.execute(
                """UPDATE app_delegation_grants SET enabled=FALSE,
                          revoked_at=clock_timestamp(),revoked_by=%s,updated_at=clock_timestamp()
                   WHERE client_id=%s AND enabled RETURNING grant_id""",
                (actor_id, client_id),
            ).fetchall()
            grant_ids = [str(item["grant_id"]) for item in grants]
            if grant_ids:
                conn.execute(
                    """UPDATE api_access_tokens SET enabled=FALSE,
                              revoked_at=clock_timestamp(),revoked_by=%s
                       WHERE delegation_grant_id=ANY(%s) AND enabled""",
                    (actor_id, grant_ids),
                )
            conn.execute(
                """INSERT INTO app_client_events
                       (client_id,app_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,'client.revoked',%s)""",
                (client_id, row["app_id"], actor_id, Jsonb({"grant_ids": grant_ids})),
            )
        return True

    def create_app_delegation_grant(
        self,
        *,
        client_id: str,
        installation_id: str,
        user_id: str,
        scopes: list[str] | tuple[str, ...],
        expires_at: str,
        actor_id: str,
    ) -> dict[str, Any]:
        normalized = normalize_app_scopes(scopes)
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("App delegation expiry is invalid") from exc
        if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("App delegation expiry must be in the future")
        grant_id = f"appgrant_{uuid4().hex}"
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT c.app_id,c.allowed_scopes,i.granted_permissions,
                          i.status,i.user_id,i.app_id AS installed_app_id
                   FROM app_clients c JOIN app_installations i ON i.installation_id=%s
                   WHERE c.client_id=%s AND c.enabled FOR UPDATE""",
                (installation_id, client_id),
            ).fetchone()
            if row is None or str(row["user_id"]) != user_id:
                raise ValueError("App client or owner installation not found")
            if str(row["status"]) != "active":
                raise ValueError("App installation must be active before delegation")
            if str(row["installed_app_id"]) != str(row["app_id"]):
                raise ValueError("App client does not match the installation")
            client_scopes = set(str(item) for item in row["allowed_scopes"])
            ceiling = set(installation_scope_ceiling(row["granted_permissions"] or []))
            if not set(normalized) <= client_scopes & ceiling:
                raise ValueError("delegated scopes exceed the client or installation grant")
            saved = conn.execute(
                """INSERT INTO app_delegation_grants
                       (grant_id,client_id,installation_id,user_id,scopes,expires_at,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s::timestamptz,%s)
                   ON CONFLICT(client_id,installation_id) DO UPDATE SET
                     scopes=EXCLUDED.scopes,
                     expires_at=EXCLUDED.expires_at,enabled=TRUE,revoked_at=NULL,
                     revoked_by=NULL,updated_at=clock_timestamp(),created_by=EXCLUDED.created_by
                   RETURNING *""",
                (
                    grant_id,
                    client_id,
                    installation_id,
                    user_id,
                    Jsonb(normalized),
                    expires_at,
                    actor_id,
                ),
            ).fetchone()
            grant_id = str(saved["grant_id"])
            # Re-authorizing a grant can narrow scopes or expiry. Existing bearer
            # tokens must not retain the previous authority after that change.
            conn.execute(
                """UPDATE api_access_tokens SET enabled=FALSE,
                          revoked_at=clock_timestamp(),revoked_by=%s
                   WHERE delegation_grant_id=%s AND enabled""",
                (actor_id, grant_id),
            )
            conn.execute(
                """INSERT INTO app_delegation_events
                       (grant_id,client_id,installation_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,%s,%s,'grant.authorized',%s)""",
                (
                    grant_id,
                    client_id,
                    installation_id,
                    user_id,
                    actor_id,
                    Jsonb({"scopes": list(normalized), "expires_at": expires_at}),
                ),
            )
        return self._app_grant_dict(saved)

    def list_app_delegation_grants(
        self, *, installation_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM app_delegation_grants
                   WHERE installation_id=%s AND user_id=%s ORDER BY created_at DESC""",
                (installation_id, user_id),
            ).fetchall()
        return [self._app_grant_dict(row) for row in rows]

    def revoke_app_delegation_grant(
        self, grant_id: str, *, user_id: str, actor_id: str
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE app_delegation_grants SET enabled=FALSE,
                          revoked_at=clock_timestamp(),revoked_by=%s,updated_at=clock_timestamp()
                   WHERE grant_id=%s AND user_id=%s AND enabled RETURNING *""",
                (actor_id, grant_id, user_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """UPDATE api_access_tokens SET enabled=FALSE,
                          revoked_at=clock_timestamp(),revoked_by=%s
                   WHERE delegation_grant_id=%s AND enabled""",
                (actor_id, grant_id),
            )
            conn.execute(
                """INSERT INTO app_delegation_events
                       (grant_id,client_id,installation_id,user_id,actor_id,event_type)
                   VALUES (%s,%s,%s,%s,%s,'grant.revoked')""",
                (grant_id, row["client_id"], row["installation_id"], user_id, actor_id),
            )
        return True

    def revoke_app_installation_authorization(
        self,
        *,
        installation_id: str,
        client_id: str,
        user_id: str,
        actor_id: str,
    ) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT grant_id FROM app_delegation_grants
                   WHERE installation_id=%s AND client_id=%s AND user_id=%s AND enabled""",
                (installation_id, client_id, user_id),
            ).fetchone()
        if row is None:
            return False
        return self.revoke_app_delegation_grant(
            str(row["grant_id"]), user_id=user_id, actor_id=actor_id
        )

    def issue_app_delegated_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        grant_id: str | None = None,
        installation_id: str | None = None,
        requested_scopes: list[str] | tuple[str, ...],
        ttl_seconds: int,
    ) -> tuple[dict[str, Any], str] | None:
        if bool(grant_id) == bool(installation_id):
            raise ValueError("exactly one App grant or installation selector is required")
        requested = normalize_app_scopes(requested_scopes)
        ttl = max(60, min(int(ttl_seconds), 3600))
        plaintext = secrets.token_urlsafe(32)
        token_id = f"tok_{uuid4().hex}"
        token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        secret_hash = hashlib.sha256(str(client_secret).encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            selector = (
                ("g.grant_id=%s", grant_id)
                if grant_id
                else ("g.installation_id=%s", installation_id)
            )
            row = conn.execute(
                f"""SELECT g.*,c.app_id,c.secret_hash,c.allowed_scopes,
                           c.enabled AS client_enabled,i.status AS installation_status
                    FROM app_delegation_grants g
                    JOIN app_clients c USING(client_id)
                    JOIN app_installations i USING(installation_id)
                    WHERE {selector[0]} AND g.client_id=%s FOR UPDATE""",  # noqa: S608
                (selector[1], client_id),
            ).fetchone()
            if (
                row is None
                or not row["client_enabled"]
                or not row["enabled"]
                or str(row["installation_status"]) != "active"
                or row["expires_at"] <= datetime.now(row["expires_at"].tzinfo)
                or not hmac.compare_digest(str(row["secret_hash"]), secret_hash)
                or not set(requested) <= set(str(item) for item in row["scopes"])
            ):
                return None
            resolved_grant_id = str(row["grant_id"])
            token_row = conn.execute(
                """INSERT INTO api_access_tokens
                       (token_id,token_hash,user_id,label,expires_at,scopes,token_type,
                        principal_kind,created_by,app_client_id,delegation_grant_id,
                        app_installation_id)
                   VALUES (%s,%s,%s,%s,
                     LEAST(clock_timestamp()+(%s || ' seconds')::interval,%s),
                     %s,'service','installation',%s,%s,%s,%s) RETURNING *""",
                (
                    token_id,
                    token_hash,
                    row["user_id"],
                    f"delegated:{client_id}",
                    ttl,
                    row["expires_at"],
                    Jsonb(requested),
                    f"app-client:{client_id}",
                    client_id,
                    resolved_grant_id,
                    row["installation_id"],
                ),
            ).fetchone()
            conn.execute(
                "UPDATE app_clients SET last_used_at=clock_timestamp() WHERE client_id=%s",
                (client_id,),
            )
            conn.execute(
                """INSERT INTO api_access_token_events
                       (token_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,'token.delegated',%s)""",
                (
                    token_id,
                    row["user_id"],
                    f"app-client:{client_id}",
                    Jsonb(
                        {
                            "client_id": client_id,
                            "grant_id": resolved_grant_id,
                            "installation_id": row["installation_id"],
                            "scopes": list(requested),
                        }
                    ),
                ),
            )
            conn.execute(
                """INSERT INTO app_delegation_events
                       (grant_id,client_id,installation_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,%s,%s,'token.exchanged',%s)""",
                (
                    resolved_grant_id,
                    client_id,
                    row["installation_id"],
                    row["user_id"],
                    f"app-client:{client_id}",
                    Jsonb({"token_id": token_id, "scopes": list(requested)}),
                ),
            )
        return self._api_access_token(token_row), plaintext

    @staticmethod
    def _app_client_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "client_id": str(row["client_id"]),
            "app_id": str(row["app_id"]),
            "name": str(row["name"]),
            "allowed_scopes": [str(item) for item in row["allowed_scopes"]],
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "last_used_at": _iso(row["last_used_at"]),
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_by": row["revoked_by"],
        }

    @staticmethod
    def _app_grant_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "grant_id": str(row["grant_id"]),
            "client_id": str(row["client_id"]),
            "installation_id": str(row["installation_id"]),
            "user_id": str(row["user_id"]),
            "scopes": [str(item) for item in row["scopes"]],
            "enabled": bool(row["enabled"]),
            "expires_at": _iso(row["expires_at"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_by": row["revoked_by"],
        }


__all__ = ["PostgresAppDelegationStoreMixin"]
