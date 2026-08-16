"""PostgreSQL atomic API admission counters shared by all gateway replicas."""

from __future__ import annotations


class PostgresRateLimitStoreMixin:
    def migrate_rate_limits(self) -> None:
        ddl = """CREATE TABLE IF NOT EXISTS api_rate_limits (
            rate_key TEXT PRIMARY KEY,
            window_start BIGINT NOT NULL,
            request_count INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS embedding_rate_limits (
            rate_key TEXT PRIMARY KEY,
            window_start BIGINT NOT NULL,
            usage_count BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341911,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="rate_limits",
                version=2,
                ddl=ddl,
                description="atomic API and embedding admission counters",
            )

    def check_embedding_rate_limit(
        self,
        profile_revision_id: str,
        *,
        requests: int,
        input_tokens: int,
        requests_per_minute: int,
        tokens_per_minute: int,
    ) -> bool:
        """Reserve one cluster-wide embedding admission atomically.

        Failed reservations roll the complete transaction back so request and token
        counters cannot diverge across Worker replicas.
        """

        class _ExceededError(Exception):
            pass

        def reserve(conn, key: str, amount: int, limit: int) -> None:  # noqa: ANN001
            row = conn.execute(
                """INSERT INTO embedding_rate_limits(rate_key,window_start,usage_count)
                   VALUES (%s,floor(extract(epoch FROM clock_timestamp()))::bigint,%s)
                   ON CONFLICT(rate_key) DO UPDATE SET
                       window_start=CASE
                           WHEN excluded.window_start-embedding_rate_limits.window_start>=60
                                OR excluded.window_start<embedding_rate_limits.window_start
                           THEN excluded.window_start ELSE embedding_rate_limits.window_start END,
                       usage_count=CASE
                           WHEN excluded.window_start-embedding_rate_limits.window_start>=60
                                OR excluded.window_start<embedding_rate_limits.window_start
                           THEN excluded.usage_count
                           ELSE embedding_rate_limits.usage_count+excluded.usage_count END,
                       updated_at=clock_timestamp()
                   RETURNING usage_count""",
                (key, max(0, int(amount))),
            ).fetchone()
            if int(row["usage_count"]) > max(1, int(limit)):
                raise _ExceededError

        try:
            with self._pool.connection() as conn, conn.transaction():
                prefix = f"embedding:{profile_revision_id}"
                reserve(conn, f"{prefix}:requests", requests, requests_per_minute)
                reserve(conn, f"{prefix}:tokens", input_tokens, tokens_per_minute)
        except _ExceededError:
            return False
        return True

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
                    """SELECT request_count,
                              floor(extract(epoch FROM clock_timestamp()))::bigint
                                  - window_start AS window_age
                       FROM api_rate_limits WHERE rate_key=%s""",
                    (rate_key,),
                ).fetchone()
                return (
                    row is None
                    or int(row["window_age"]) >= divisor
                    or int(row["window_age"]) < 0
                    or int(row["request_count"]) < max(1, int(limit))
                )
            row = conn.execute(
                """INSERT INTO api_rate_limits(rate_key,window_start,request_count)
                   VALUES (
                       %s,
                       floor(extract(epoch FROM clock_timestamp()))::bigint,
                       1
                   ) ON CONFLICT(rate_key) DO UPDATE SET
                       window_start=CASE
                           WHEN excluded.window_start-api_rate_limits.window_start >= %s
                                OR excluded.window_start < api_rate_limits.window_start
                           THEN excluded.window_start ELSE api_rate_limits.window_start END,
                       request_count=CASE
                           WHEN excluded.window_start-api_rate_limits.window_start >= %s
                                OR excluded.window_start < api_rate_limits.window_start
                           THEN 1 ELSE api_rate_limits.request_count+1 END,
                       updated_at=clock_timestamp()
                   RETURNING request_count""",
                (rate_key, divisor, divisor),
            ).fetchone()
            return int(row["request_count"]) <= max(1, int(limit))
