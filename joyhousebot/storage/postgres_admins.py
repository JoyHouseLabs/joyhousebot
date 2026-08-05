"""PostgreSQL persistence for platform administrator membership and audit."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any
from uuid import uuid4

from joyhousebot.application.permissions import normalize_permissions
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.platform_records import PlatformAdminRecord

_ROLES = {"admin", "operator", "viewer"}


class PostgresAdminStoreMixin:
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
        token: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        plaintext = str(token or secrets.token_urlsafe(32))
        if len(plaintext) < 6:
            raise ValueError("API access tokens must contain at least 6 characters")
        token_id = f"tok_{uuid4().hex}"
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO api_access_tokens
                       (token_id,token_hash,user_id,label,expires_at,created_by)
                   VALUES (%s,%s,%s,%s,%s::timestamptz,%s) RETURNING *""",
                (token_id, digest, user_id, label, expires_at, actor_id),
            ).fetchone()
            conn.execute(
                """INSERT INTO api_access_token_events
                       (token_id,user_id,actor_id,event_type)
                   VALUES (%s,%s,%s,'token.issued')""",
                (token_id, user_id, actor_id),
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
                """UPDATE api_access_tokens SET enabled=FALSE WHERE token_id=%s AND enabled
                   RETURNING user_id""",
                (token_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO api_access_token_events
                           (token_id,user_id,actor_id,event_type)
                       VALUES (%s,%s,%s,'token.revoked')""",
                    (token_id, row["user_id"], actor_id),
                )
        return row is not None

    @staticmethod
    def _api_access_token(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "token_id": str(row["token_id"]),
            "user_id": str(row["user_id"]),
            "label": str(row["label"]),
            "enabled": bool(row["enabled"]),
            "expires_at": _iso(row["expires_at"]),
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
