"""Cluster-wide health state for shared model authentication profiles."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


class ProfileHealthRepository:
    """Serialize profile success/failure transitions across worker replicas."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("ProfileHealthRepository requires PostgreSQL runtime store")
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def migrate(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS provider_profile_health (
            profile_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_failure_ms BIGINT,
            last_used_ms BIGINT,
            cooldown_until_ms BIGINT NOT NULL DEFAULT 0,
            disabled_until_ms BIGINT NOT NULL DEFAULT 0,
            updated_at_ms BIGINT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_provider_profile_recovery
            ON provider_profile_health(provider, cooldown_until_ms, disabled_until_ms);
        """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341914,))
                connection.execute(ddl)

    @staticmethod
    def _stats(row: Any) -> dict[str, Any]:
        return {
            "failure_count": int(row["failure_count"]),
            "last_failure_ms": row["last_failure_ms"],
            "last_used_ms": row["last_used_ms"],
            "cooldown_until_ms": int(row["cooldown_until_ms"]),
            "disabled_until_ms": int(row["disabled_until_ms"]),
        }

    def load(self) -> dict[str, dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM provider_profile_health").fetchall()
        return {str(row["profile_id"]): self._stats(row) for row in rows}

    def mark_success(
        self, profile_id: str, provider: str, *, now_ms: int | None = None
    ) -> dict[str, Any]:
        now = now_ms or int(time.time() * 1000)
        query = """INSERT INTO provider_profile_health
               (profile_id,provider,failure_count,last_used_ms,cooldown_until_ms,
                disabled_until_ms,updated_at_ms)
               VALUES (%s,%s,0,%s,0,0,%s)
               ON CONFLICT(profile_id) DO UPDATE SET provider=EXCLUDED.provider,
                 failure_count=0,last_used_ms=EXCLUDED.last_used_ms,
                 cooldown_until_ms=0,disabled_until_ms=0,
                 updated_at_ms=EXCLUDED.updated_at_ms RETURNING *"""
        with self._connection() as connection:
            row = connection.execute(query, (profile_id, provider, now, now)).fetchone()
        return self._stats(row)

    def mark_failure(
        self,
        profile_id: str,
        provider: str,
        reason: str,
        config: Any,
        *,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        now = now_ms or int(time.time() * 1000)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_profile_health WHERE profile_id=%s FOR UPDATE",
                (profile_id,),
            ).fetchone()
            stats = self._stats(row) if row else {}
            cooldowns = getattr(getattr(config, "auth", None), "cooldowns", None)
            failure_window_h = float(getattr(cooldowns, "failure_window_hours", 24.0) or 24.0)
            last_failure = int(stats.get("last_failure_ms") or 0)
            count = (
                0
                if now - last_failure > failure_window_h * 3_600_000
                else int(stats.get("failure_count") or 0)
            ) + 1
            cooldown_until = int(stats.get("cooldown_until_ms") or 0)
            disabled_until = int(stats.get("disabled_until_ms") or 0)
            if reason == "billing":
                base_h = float(getattr(cooldowns, "billing_backoff_hours", 5.0) or 5.0)
                by_provider = getattr(cooldowns, "billing_backoff_hours_by_provider", {}) or {}
                if isinstance(by_provider, dict) and provider in by_provider:
                    base_h = float(by_provider.get(provider) or base_h)
                max_h = float(getattr(cooldowns, "billing_max_hours", 24.0) or 24.0)
                disabled_until = int(now + min(max_h, base_h * 2 ** max(0, count - 1)) * 3_600_000)
            else:
                cooldown_until = int(now + min(1800.0, 15.0 * 2 ** max(0, count - 1)) * 1000)
            values = (
                profile_id,
                provider,
                count,
                now,
                cooldown_until,
                disabled_until,
                now,
            )
            query = """INSERT INTO provider_profile_health
                   (profile_id,provider,failure_count,last_failure_ms,cooldown_until_ms,
                    disabled_until_ms,updated_at_ms) VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(profile_id) DO UPDATE SET provider=EXCLUDED.provider,
                     failure_count=EXCLUDED.failure_count,
                     last_failure_ms=EXCLUDED.last_failure_ms,
                     cooldown_until_ms=EXCLUDED.cooldown_until_ms,
                     disabled_until_ms=EXCLUDED.disabled_until_ms,
                     updated_at_ms=EXCLUDED.updated_at_ms RETURNING *"""
            updated = connection.execute(query, values).fetchone()
        return self._stats(updated)
