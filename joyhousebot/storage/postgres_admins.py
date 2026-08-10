"""PostgreSQL persistence for platform administrator membership and audit."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime
from typing import Any
from uuid import uuid4

from joyhousebot.domain.permissions import normalize_permissions
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.platform_records import PlatformAdminRecord
from joyhousebot.storage.postgres_admin_auth import PostgresAdminAuthStoreMixin

_ROLES = {"admin", "operator", "viewer"}
_TOKEN_TYPES = {"user", "service"}
_SCOPE_PATTERN = re.compile(r"^(?:\*|[a-z][a-z0-9_-]*(?:\.[a-z0-9_*][a-z0-9_-]*)+)$")


def _normalize_token_scopes(scopes: list[str] | tuple[str, ...]) -> list[str]:
    normalized = sorted({str(scope).strip().lower() for scope in scopes if str(scope).strip()})
    if not normalized:
        raise ValueError("at least one API token scope is required")
    if any(not _SCOPE_PATTERN.fullmatch(scope) for scope in normalized):
        raise ValueError("API token scopes must use namespace.operation syntax")
    return normalized


class PostgresAdminStoreMixin(PostgresAdminAuthStoreMixin):
    def migrate_admins(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS platform_admins (
            user_id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            is_test_user BOOLEAN NOT NULL DEFAULT FALSE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (role IN ('admin','operator','viewer'))
        );
        CREATE INDEX IF NOT EXISTS ix_platform_admins_enabled
            ON platform_admins(enabled, role, user_id);
        CREATE TABLE IF NOT EXISTS platform_admin_events (
            sequence BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_platform_admin_events_user
            ON platform_admin_events(user_id, sequence DESC);
        CREATE TABLE IF NOT EXISTS api_access_tokens (
            token_id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            last_used_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_api_access_tokens_user
            ON api_access_tokens(user_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS api_access_token_events (
            sequence BIGSERIAL PRIMARY KEY,
            token_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341913,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="admins",
                version=1,
                ddl=ddl,
                description="platform admins, API access tokens, and audit events",
            )
            upgrade_ddl = """
            ALTER TABLE api_access_tokens
                ADD COLUMN IF NOT EXISTS scopes JSONB NOT NULL DEFAULT '["*"]'::jsonb;
            ALTER TABLE api_access_tokens
                ADD COLUMN IF NOT EXISTS token_type TEXT NOT NULL DEFAULT 'user';
            ALTER TABLE api_access_tokens
                ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ;
            ALTER TABLE api_access_tokens
                ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
            ALTER TABLE api_access_tokens
                ADD COLUMN IF NOT EXISTS revoked_by TEXT;
            ALTER TABLE api_access_token_events
                ADD COLUMN IF NOT EXISTS data JSONB NOT NULL DEFAULT '{}'::jsonb;
            CREATE INDEX IF NOT EXISTS ix_api_access_tokens_rotation
                ON api_access_tokens(rotation_due_at)
                WHERE enabled AND rotation_due_at IS NOT NULL;
            """
            conn.execute(upgrade_ddl)
            self._record_migration(
                conn,
                name="admins",
                version=2,
                ddl=upgrade_ddl,
                description="scoped and auditable user/service API tokens",
            )
            auth_ddl = """
            CREATE TABLE IF NOT EXISTS admin_login_credentials (
                user_id TEXT PRIMARY KEY REFERENCES platform_admins(user_id) ON DELETE CASCADE,
                password_hash TEXT NOT NULL,
                must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TIMESTAMPTZ,
                last_login_at TIMESTAMPTZ,
                password_changed_at TIMESTAMPTZ,
                totp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                totp_secret_ciphertext TEXT,
                totp_pending_secret_ciphertext TEXT,
                totp_pending_expires_at TIMESTAMPTZ,
                totp_last_counter BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            );
            CREATE TABLE IF NOT EXISTS admin_auth_challenges (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES platform_admins(user_id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'totp_login',
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                CHECK (kind IN ('totp_login'))
            );
            CREATE INDEX IF NOT EXISTS ix_admin_auth_challenges_expiry
                ON admin_auth_challenges(expires_at);
            CREATE TABLE IF NOT EXISTS admin_auth_sessions (
                session_id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL REFERENCES platform_admins(user_id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                mfa_verified_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                last_used_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_user
                ON admin_auth_sessions(user_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_active
                ON admin_auth_sessions(token_hash,expires_at) WHERE revoked_at IS NULL;
            CREATE TABLE IF NOT EXISTS admin_recovery_codes (
                code_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES platform_admins(user_id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                used_at TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS ix_admin_recovery_codes_user
                ON admin_recovery_codes(user_id,used_at);
            """
            conn.execute(auth_ddl)
            self._record_migration(
                conn,
                name="admins",
                version=3,
                ddl=auth_ddl,
                description="administrator password, browser session, and TOTP authentication",
            )


    def upsert_platform_admin(
        self,
        *,
        user_id: str,
        role: str = "admin",
        permissions: list[str] | tuple[str, ...] = ("*",),
        enabled: bool = True,
        is_test_user: bool = False,
        actor_id: str = "system",
    ) -> PlatformAdminRecord:
        user_id = str(user_id).strip()
        role = str(role).strip().lower()
        if not user_id:
            raise ValueError("admin user_id is required")
        if role not in _ROLES:
            raise ValueError("invalid platform admin role")
        normalized = list(normalize_permissions(permissions))
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341915,))
            row = conn.execute(
                """INSERT INTO platform_admins
                       (user_id,role,permissions,enabled,is_test_user,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,
                       permissions=excluded.permissions,enabled=excluded.enabled,
                       is_test_user=excluded.is_test_user,updated_at=clock_timestamp()
                   RETURNING *""",
                (user_id, role, Jsonb(normalized), enabled, is_test_user, actor_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                   VALUES (%s,%s,'admin.upserted',%s)""",
                (user_id, actor_id, Jsonb({"role": role, "permissions": normalized,
                                          "enabled": enabled, "is_test_user": is_test_user})),
            )
            self._assert_admin_authority(conn)
        assert row is not None
        return self._platform_admin(row)

    def get_platform_admin(self, user_id: str) -> PlatformAdminRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM platform_admins WHERE user_id=%s", (user_id,)
            ).fetchone()
        return self._platform_admin(row) if row else None

    def list_platform_admins(self) -> list[PlatformAdminRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM platform_admins ORDER BY is_test_user DESC,user_id"
            ).fetchall()
        return [self._platform_admin(row) for row in rows]

    def delete_platform_admin(self, user_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341915,))
            row = conn.execute(
                "DELETE FROM platform_admins WHERE user_id=%s RETURNING user_id", (user_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO platform_admin_events(user_id,actor_id,event_type)
                       VALUES (%s,%s,'admin.deleted')""",
                    (user_id, actor_id),
                )
                self._assert_admin_authority(conn)
        return row is not None

    def list_platform_admin_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        from joyhousebot.storage.postgres_store import _iso, _json

        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM platform_admin_events
                   ORDER BY sequence DESC LIMIT %s""",
                (max(1, min(2000, limit)),),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "user_id": str(row["user_id"]),
                "actor_id": str(row["actor_id"]),
                "event_type": str(row["event_type"]),
                "data": dict(_json(row["data"], {})),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    def create_api_access_token(
        self,
        *,
        user_id: str,
        label: str = "",
        actor_id: str,
        expires_at: str | None = None,
        rotation_due_at: str | None = None,
        scopes: list[str] | tuple[str, ...] = ("*",),
        token_type: str = "user",
        token: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        token_type = str(token_type).strip().lower()
        if token_type not in _TOKEN_TYPES:
            raise ValueError("API token_type must be user or service")
        normalized_scopes = _normalize_token_scopes(scopes)
        if token_type == "service" and "*" in normalized_scopes:
            raise ValueError("service API tokens cannot use the global wildcard scope")
        if token_type == "service" and expires_at is None:
            raise ValueError("service API tokens require expires_at")
        plaintext = str(token or secrets.token_urlsafe(32))
        if len(plaintext) < 6:
            raise ValueError("API access tokens must contain at least 6 characters")
        token_id = f"tok_{uuid4().hex}"
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO api_access_tokens
                       (token_id,token_hash,user_id,label,expires_at,rotation_due_at,
                        scopes,token_type,created_by)
                   VALUES (%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s,%s,%s)
                   RETURNING *""",
                (
                    token_id,
                    digest,
                    user_id,
                    label,
                    expires_at,
                    rotation_due_at,
                    Jsonb(normalized_scopes),
                    token_type,
                    actor_id,
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO api_access_token_events
                       (token_id,user_id,actor_id,event_type,data)
                   VALUES (%s,%s,%s,'token.issued',%s)""",
                (
                    token_id,
                    user_id,
                    actor_id,
                    Jsonb({
                        "label": label,
                        "scopes": normalized_scopes,
                        "token_type": token_type,
                        "expires_at": expires_at,
                        "rotation_due_at": rotation_due_at,
                    }),
                ),
            )
        return self._api_access_token(row), plaintext

    def authenticate_api_access_token(self, token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(str(token).encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM api_access_tokens WHERE token_hash=%s AND enabled
                     AND (expires_at IS NULL OR expires_at>clock_timestamp())""",
                (digest,),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """UPDATE api_access_tokens SET last_used_at=clock_timestamp()
                       WHERE token_id=%s AND (last_used_at IS NULL OR
                         last_used_at<clock_timestamp()-interval '5 minutes')""",
                    (row["token_id"],),
                )
        return self._api_access_token(row) if row else None

    def list_api_access_tokens(self, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM api_access_tokens ORDER BY created_at DESC LIMIT %s""",
                (max(1, min(5000, limit)),),
            ).fetchall()
        return [self._api_access_token(row) for row in rows]

    def revoke_api_access_token(self, token_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE api_access_tokens SET enabled=FALSE,
                       revoked_at=clock_timestamp(),revoked_by=%s
                   WHERE token_id=%s AND enabled
                   RETURNING user_id""",
                (actor_id, token_id),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO api_access_token_events
                           (token_id,user_id,actor_id,event_type)
                       VALUES (%s,%s,%s,'token.revoked')""",
                    (token_id, row["user_id"], actor_id),
                )
        return row is not None

    def list_api_access_token_events(self, *, limit: int = 500) -> list[dict[str, Any]]:
        from joyhousebot.storage.postgres_store import _iso, _json

        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM api_access_token_events
                   ORDER BY sequence DESC LIMIT %s""",
                (max(1, min(5000, limit)),),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "token_id": str(row["token_id"]),
                "user_id": str(row["user_id"]),
                "actor_id": str(row["actor_id"]),
                "event_type": str(row["event_type"]),
                "data": dict(_json(row["data"], {})),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _api_access_token(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "token_id": str(row["token_id"]),
            "user_id": str(row["user_id"]),
            "label": str(row["label"]),
            "scopes": [str(item) for item in (row["scopes"] or [])],
            "token_type": str(row["token_type"]),
            "enabled": bool(row["enabled"]),
            "expires_at": _iso(row["expires_at"]),
            "rotation_due_at": _iso(row["rotation_due_at"]),
            "rotation_overdue": bool(
                row["rotation_due_at"] is not None
                and row["rotation_due_at"] <= datetime.now(row["rotation_due_at"].tzinfo)
            ),
            "revoked_at": _iso(row["revoked_at"]),
            "revoked_by": row["revoked_by"],
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "last_used_at": _iso(row["last_used_at"]),
        }

    @staticmethod
    def _assert_admin_authority(conn: Any) -> None:
        row = conn.execute(
            """SELECT count(*) AS count FROM platform_admins
               WHERE enabled AND (permissions ? '*' OR permissions ? 'admins.write')"""
        ).fetchone()
        if int(row["count"] or 0) < 1:
            raise ValueError("at least one enabled administrator with admins.write is required")

    @staticmethod
    def _platform_admin(row: dict[str, Any]) -> PlatformAdminRecord:
        from joyhousebot.storage.postgres_store import _iso

        return PlatformAdminRecord(
            user_id=str(row["user_id"]),
            role=str(row["role"]),
            permissions=tuple(str(item) for item in (row["permissions"] or [])),
            enabled=bool(row["enabled"]),
            is_test_user=bool(row["is_test_user"]),
            created_by=str(row["created_by"]),
            created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",
        )
