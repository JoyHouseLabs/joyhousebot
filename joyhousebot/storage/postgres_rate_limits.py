"""PostgreSQL atomic API admission counters shared by all gateway replicas."""

from __future__ import annotations


class PostgresRateLimitStoreMixin:
    def migrate_rate_limits(self) -> None:
        ddl = """CREATE TABLE IF NOT EXISTS api_rate_limits (
            rate_key TEXT PRIMARY KEY,
            window_start BIGINT NOT NULL,
            request_count INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )"""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341911,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="rate_limits",
                version=1,
                ddl=ddl,
                description="atomic API admission counters",
            )

    def check_api_rate_limit(
        self,
        rate_key: str,
        *,
        limit: int,
        window_seconds: int = 60,
        increment: bool = True,
    ) -> bool:
        divisor = max(1, int(window_seconds))
        with self._pool.connection() as conn, conn.transaction():
            if not increment:
                row = conn.execute(
                    """SELECT request_count FROM api_rate_limits
                       WHERE rate_key=%s AND window_start=
                           floor(extract(epoch FROM clock_timestamp())/%s)::bigint""",
                    (rate_key, divisor),
                ).fetchone()
                return row is None or int(row["request_count"]) < max(1, int(limit))
            row = conn.execute(
                """INSERT INTO api_rate_limits(rate_key,window_start,request_count)
                   VALUES (
                       %s,
                       floor(extract(epoch FROM clock_timestamp())/%s)::bigint,
                       1
                   ) ON CONFLICT(rate_key) DO UPDATE SET
                       window_start=excluded.window_start,
                       request_count=CASE
                           WHEN api_rate_limits.window_start=excluded.window_start
                           THEN api_rate_limits.request_count+1 ELSE 1 END,
                       updated_at=clock_timestamp()
                   RETURNING request_count""",
                (rate_key, divisor),
            ).fetchone()
            return int(row["request_count"]) <= max(1, int(limit))
