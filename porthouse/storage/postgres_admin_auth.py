"""PostgreSQL persistence for administrator password, session, and MFA state."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from porthouse.storage.json_codec import Jsonb


class PostgresAdminAuthStoreMixin:
    def set_admin_password(
        self,
        *,
        user_id: str,
        password_hash: str,
        must_change_password: bool,
        actor_id: str,
        only_if_missing: bool = False,
    ) -> bool:
        """Create or replace an administrator password without storing plaintext."""
        with self._pool.connection() as conn, conn.transaction():
            if only_if_missing:
                row = conn.execute(
                    """INSERT INTO admin_login_credentials
                           (user_id,password_hash,must_change_password,password_changed_at)
                       SELECT user_id,%s,%s,clock_timestamp() FROM platform_admins
                       WHERE user_id=%s
                       ON CONFLICT(user_id) DO NOTHING
                       RETURNING user_id""",
                    (password_hash, must_change_password, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO admin_login_credentials
                           (user_id,password_hash,must_change_password,password_changed_at)
                       SELECT user_id,%s,%s,clock_timestamp() FROM platform_admins
                       WHERE user_id=%s
                       ON CONFLICT(user_id) DO UPDATE SET
                           password_hash=excluded.password_hash,
                           must_change_password=excluded.must_change_password,
                           failed_attempts=0,locked_until=NULL,
                           password_changed_at=clock_timestamp(),updated_at=clock_timestamp()
                       RETURNING user_id""",
                    (password_hash, must_change_password, user_id),
                ).fetchone()
            if row is None:
                return False
            if not only_if_missing:
                conn.execute(
                    """UPDATE admin_auth_sessions SET revoked_at=clock_timestamp()
                       WHERE user_id=%s AND revoked_at IS NULL""",
                    (user_id,),
                )
                conn.execute("DELETE FROM admin_auth_challenges WHERE user_id=%s", (user_id,))
            conn.execute(
                """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                   VALUES (%s,%s,'auth.password_configured',%s)""",
                (
                    user_id,
                    actor_id,
                    Jsonb({"must_change_password": must_change_password, "bootstrap": only_if_missing}),
                ),
            )
        return True

    def get_admin_login_credential(self, user_id: str) -> dict[str, Any] | None:
        from porthouse.storage.postgres_store import _iso

        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT c.*,a.enabled AS admin_enabled,a.role,a.permissions
                   FROM admin_login_credentials c
                   JOIN platform_admins a USING(user_id)
                   WHERE c.user_id=%s""",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": str(row["user_id"]),
            "password_hash": str(row["password_hash"]),
            "must_change_password": bool(row["must_change_password"]),
            "failed_attempts": int(row["failed_attempts"] or 0),
            "locked_until": _iso(row["locked_until"]),
            "is_locked": bool(
                row["locked_until"] is not None
                and row["locked_until"] > datetime.now(row["locked_until"].tzinfo)
            ),
            "totp_enabled": bool(row["totp_enabled"]),
            "totp_secret_ciphertext": row["totp_secret_ciphertext"],
            "totp_pending_secret_ciphertext": row["totp_pending_secret_ciphertext"],
            "totp_pending_expires_at": _iso(row["totp_pending_expires_at"]),
            "totp_last_counter": row["totp_last_counter"],
            "admin_enabled": bool(row["admin_enabled"]),
            "role": str(row["role"]),
            "permissions": [str(item) for item in (row["permissions"] or [])],
        }

    def record_admin_login_failure(
        self,
        user_id: str,
        *,
        max_attempts: int = 5,
        lock_seconds: int = 900,
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_login_credentials SET
                       failed_attempts=CASE
                         WHEN locked_until IS NOT NULL AND locked_until<=clock_timestamp() THEN 1
                         ELSE failed_attempts+1 END,
                       locked_until=CASE
                         WHEN (CASE WHEN locked_until IS NOT NULL AND locked_until<=clock_timestamp()
                               THEN 1 ELSE failed_attempts+1 END)>=%s
                         THEN clock_timestamp()+(%s * interval '1 second')
                         ELSE locked_until END,
                       updated_at=clock_timestamp()
                   WHERE user_id=%s
                   RETURNING failed_attempts,locked_until""",
                (max(1, max_attempts), max(1, lock_seconds), user_id),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                       VALUES (%s,%s,'auth.login_failed',%s)""",
                    (
                        user_id,
                        user_id,
                        Jsonb({"failed_attempts": int(row["failed_attempts"] or 0),
                               "locked": row["locked_until"] is not None}),
                    ),
                )
        return dict(row) if row else None

    def record_admin_login_success(self, user_id: str) -> None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE admin_login_credentials SET failed_attempts=0,locked_until=NULL,
                       last_login_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE user_id=%s""",
                (user_id,),
            )
            conn.execute(
                """INSERT INTO platform_admin_events(user_id,actor_id,event_type)
                   VALUES (%s,%s,'auth.password_verified')""",
                (user_id, user_id),
            )

    def create_admin_auth_challenge(
        self,
        user_id: str,
        *,
        expires_seconds: int = 300,
    ) -> tuple[str, str]:
        from porthouse.security.admin_auth import new_bearer_token, token_digest
        from porthouse.storage.postgres_store import _iso

        token = new_bearer_token("jhc")
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "DELETE FROM admin_auth_challenges WHERE expires_at<=clock_timestamp() OR user_id=%s",
                (user_id,),
            )
            row = conn.execute(
                """INSERT INTO admin_auth_challenges(token_hash,user_id,expires_at)
                   VALUES (%s,%s,clock_timestamp()+(%s * interval '1 second'))
                   RETURNING expires_at""",
                (token_digest(token), user_id, max(30, expires_seconds)),
            ).fetchone()
        assert row is not None
        return token, _iso(row["expires_at"]) or ""

    def get_admin_auth_challenge(self, token: str) -> dict[str, Any] | None:
        from porthouse.security.admin_auth import token_digest

        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT c.*,a.enabled AS admin_enabled
                   FROM admin_auth_challenges c JOIN platform_admins a USING(user_id)
                   WHERE token_hash=%s AND expires_at>clock_timestamp()""",
                (token_digest(token),),
            ).fetchone()
        return dict(row) if row else None

    def record_admin_auth_challenge_failure(self, token: str, *, max_attempts: int = 5) -> None:
        from porthouse.security.admin_auth import token_digest

        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_auth_challenges SET failed_attempts=failed_attempts+1
                   WHERE token_hash=%s RETURNING user_id,failed_attempts""",
                (token_digest(token),),
            ).fetchone()
            if row and int(row["failed_attempts"] or 0) >= max(1, max_attempts):
                conn.execute(
                    "DELETE FROM admin_auth_challenges WHERE token_hash=%s",
                    (token_digest(token),),
                )

    def consume_admin_auth_challenge(self, token: str) -> bool:
        from porthouse.security.admin_auth import token_digest

        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """DELETE FROM admin_auth_challenges
                   WHERE token_hash=%s AND expires_at>clock_timestamp()
                   RETURNING user_id""",
                (token_digest(token),),
            ).fetchone()
        return row is not None

    def create_admin_auth_session(
        self,
        user_id: str,
        *,
        expires_seconds: int = 43200,
        mfa_verified: bool = False,
    ) -> tuple[dict[str, Any], str]:
        from porthouse.security.admin_auth import new_bearer_token, token_digest
        from porthouse.storage.postgres_store import _iso

        token = new_bearer_token("jhs")
        session_id = f"ses_{uuid4().hex}"
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "DELETE FROM admin_auth_sessions WHERE expires_at<=clock_timestamp()",
            )
            row = conn.execute(
                """INSERT INTO admin_auth_sessions
                       (session_id,token_hash,user_id,expires_at,mfa_verified_at)
                   VALUES (%s,%s,%s,clock_timestamp()+(%s * interval '1 second'),
                           CASE WHEN %s THEN clock_timestamp() ELSE NULL END)
                   RETURNING *""",
                (session_id, token_digest(token), user_id, max(300, expires_seconds), mfa_verified),
            ).fetchone()
            conn.execute(
                """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                   VALUES (%s,%s,'auth.session_created',%s)""",
                (user_id, user_id, Jsonb({"session_id": session_id, "mfa": mfa_verified})),
            )
        assert row is not None
        return {
            "session_id": session_id,
            "user_id": user_id,
            "expires_at": _iso(row["expires_at"]),
            "mfa_verified": mfa_verified,
        }, token

    def authenticate_admin_session(self, token: str) -> dict[str, Any] | None:
        from porthouse.security.admin_auth import token_digest
        from porthouse.storage.postgres_store import _iso

        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT s.*,a.role,a.permissions,a.enabled,
                          c.must_change_password,c.totp_enabled
                   FROM admin_auth_sessions s
                   JOIN platform_admins a USING(user_id)
                   JOIN admin_login_credentials c USING(user_id)
                   WHERE s.token_hash=%s AND s.revoked_at IS NULL
                     AND s.expires_at>clock_timestamp() AND a.enabled""",
                (token_digest(token),),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE admin_auth_sessions SET last_used_at=clock_timestamp()
                       WHERE session_id=%s AND (last_used_at IS NULL OR
                         last_used_at<clock_timestamp()-interval '5 minutes')""",
                    (row["session_id"],),
                )
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "user_id": str(row["user_id"]),
            "role": str(row["role"]),
            "permissions": [str(item) for item in (row["permissions"] or [])],
            "expires_at": _iso(row["expires_at"]),
            "mfa_verified": row["mfa_verified_at"] is not None,
            "must_change_password": bool(row["must_change_password"]),
            "totp_enabled": bool(row["totp_enabled"]),
        }

    def revoke_admin_auth_session(self, session_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_auth_sessions SET revoked_at=clock_timestamp()
                   WHERE session_id=%s AND revoked_at IS NULL RETURNING user_id""",
                (session_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                       VALUES (%s,%s,'auth.session_revoked',%s)""",
                    (row["user_id"], actor_id, Jsonb({"session_id": session_id})),
                )
        return row is not None

    def set_admin_totp_pending(
        self,
        user_id: str,
        *,
        secret_ciphertext: str,
        expires_seconds: int = 600,
    ) -> str | None:
        from porthouse.storage.postgres_store import _iso

        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_login_credentials SET
                       totp_pending_secret_ciphertext=%s,
                       totp_pending_expires_at=clock_timestamp()+(%s * interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND NOT totp_enabled
                   RETURNING totp_pending_expires_at""",
                (secret_ciphertext, max(60, expires_seconds), user_id),
            ).fetchone()
        return _iso(row["totp_pending_expires_at"]) if row else None

    def activate_admin_totp(
        self,
        user_id: str,
        *,
        secret_ciphertext: str,
        counter: int,
        recovery_code_hashes: list[str],
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_login_credentials SET totp_enabled=TRUE,
                       totp_secret_ciphertext=%s,totp_last_counter=%s,
                       totp_pending_secret_ciphertext=NULL,totp_pending_expires_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND totp_pending_expires_at>clock_timestamp()
                   RETURNING user_id""",
                (secret_ciphertext, counter, user_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM admin_recovery_codes WHERE user_id=%s", (user_id,))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO admin_recovery_codes(code_hash,user_id) VALUES (%s,%s)",
                    [(digest, user_id) for digest in recovery_code_hashes],
                )
            conn.execute(
                """INSERT INTO platform_admin_events(user_id,actor_id,event_type,data)
                   VALUES (%s,%s,'auth.totp_enabled',%s)""",
                (user_id, user_id, Jsonb({"recovery_code_count": len(recovery_code_hashes)})),
            )
        return True

    def accept_admin_totp_counter(self, user_id: str, counter: int) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_login_credentials SET totp_last_counter=%s,
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND totp_enabled
                     AND (totp_last_counter IS NULL OR totp_last_counter<%s)
                   RETURNING user_id""",
                (counter, user_id, counter),
            ).fetchone()
        return row is not None

    def consume_admin_recovery_code(self, user_id: str, code_hash: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_recovery_codes SET used_at=clock_timestamp()
                   WHERE user_id=%s AND code_hash=%s AND used_at IS NULL
                   RETURNING code_hash""",
                (user_id, code_hash),
            ).fetchone()
            if row:
                conn.execute(
                    """INSERT INTO platform_admin_events(user_id,actor_id,event_type)
                       VALUES (%s,%s,'auth.recovery_code_used')""",
                    (user_id, user_id),
                )
        return row is not None

    def disable_admin_totp(self, user_id: str, *, actor_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE admin_login_credentials SET totp_enabled=FALSE,
                       totp_secret_ciphertext=NULL,totp_pending_secret_ciphertext=NULL,
                       totp_pending_expires_at=NULL,totp_last_counter=NULL,
                       updated_at=clock_timestamp()
                   WHERE user_id=%s AND totp_enabled RETURNING user_id""",
                (user_id,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM admin_recovery_codes WHERE user_id=%s", (user_id,))
                conn.execute(
                    """INSERT INTO platform_admin_events(user_id,actor_id,event_type)
                       VALUES (%s,%s,'auth.totp_disabled')""",
                    (user_id, actor_id),
                )
        return row is not None

    def get_admin_auth_status(self, user_id: str) -> dict[str, Any] | None:
        from porthouse.storage.postgres_store import _iso

        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT c.user_id,c.must_change_password,c.totp_enabled,
                          c.password_changed_at,c.last_login_at,
                          count(r.code_hash) FILTER (WHERE r.used_at IS NULL) AS recovery_codes_remaining
                   FROM admin_login_credentials c
                   LEFT JOIN admin_recovery_codes r USING(user_id)
                   WHERE c.user_id=%s
                   GROUP BY c.user_id,c.must_change_password,c.totp_enabled,
                            c.password_changed_at,c.last_login_at""",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": str(row["user_id"]),
            "must_change_password": bool(row["must_change_password"]),
            "totp_enabled": bool(row["totp_enabled"]),
            "password_changed_at": _iso(row["password_changed_at"]),
            "last_login_at": _iso(row["last_login_at"]),
            "recovery_codes_remaining": int(row["recovery_codes_remaining"] or 0),
        }
