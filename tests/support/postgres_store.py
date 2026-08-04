"""Explicit PostgreSQL store used by the test suite.

Tests historically constructed ``PostgresTestStore(path)``.  The framework is
PostgreSQL-only now, so tests use this named helper while retaining path-based
isolation semantics during the migration.
"""

from __future__ import annotations

import os
import time

from psycopg.errors import DeadlockDetected

from joyhousebot.storage.postgres_store import PostgresRuntimeStore

TEST_DATABASE_URL = (
    os.environ.get("JOYHOUSEBOT_TEST_POSTGRES_URL")
    or "postgresql://postgres:postgres@127.0.0.1:5432/joyhousebot_test"
)


class PostgresTestStore(PostgresRuntimeStore):
    """PostgreSQL test store with the old path argument as an isolation key."""

    _last_path: str | None = None
    _last_store: "PostgresTestStore | None" = None

    def __init__(self, isolation_key=None, *args, **kwargs):  # noqa: ANN001
        super().__init__(TEST_DATABASE_URL, auto_migrate=True, *args, **kwargs)
        key = str(isolation_key) if isolation_key is not None else "__default__"
        if key != self.__class__._last_path:
            self._clear_test_rows()
            self._seed_default_agents()
            self.__class__._last_path = key
        self.__class__._last_store = self

    def _clear_test_rows(self) -> None:
        # The suite intentionally uses one PostgreSQL database.  A few tests
        # create Stores from background event loops, so serialise destructive
        # fixture cleanup and retry the rare lock race with already-running
        # transaction work.
        for attempt in range(4):
            try:
                with self._pool.connection() as connection, connection.transaction():
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (90501177,))
                    row = connection.execute(
                        """SELECT string_agg(format('%I.%I', table_schema, table_name), ', ')
                           AS tables
                           FROM information_schema.tables
                           WHERE table_schema='public' AND table_type='BASE TABLE'
                             AND table_name NOT LIKE '%schema_migrations'"""
                    ).fetchone()
                    if row and row["tables"]:
                        connection.execute(f"TRUNCATE TABLE {row['tables']} CASCADE")
                return
            except DeadlockDetected:
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
