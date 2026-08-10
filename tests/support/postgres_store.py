"""Explicit PostgreSQL store used by the test suite.

Tests historically constructed ``PostgresTestStore(path)``.  The framework is
PostgreSQL-only now, so tests use this named helper while retaining path-based
isolation semantics during the migration.
"""

from __future__ import annotations

import os
import time

import psycopg
import pytest
from psycopg.errors import DeadlockDetected

from joyhousebot.storage.postgres_store import PostgresRuntimeStore

TEST_DATABASE_URL = (
    os.environ.get("JOYHOUSEBOT_TEST_POSTGRES_URL")
    or "postgresql://postgres:postgres@127.0.0.1:5432/joyhousebot_test"
)

# One connectivity probe per database URL per test run, so a missing
# PostgreSQL turns every dependent test into a skip instead of an error.
_probe_failures: dict[str, str | None] = {}


def require_postgres(database_url: str | None = None) -> str:
    """Return a usable test database URL, or skip when PostgreSQL is down.

    Tests that only guarded on the environment variable used to *fail* when
    the URL was set but no server was listening, while other tests skipped.
    This probe makes the whole suite behave the same way: no reachable
    PostgreSQL means a consistent skip, never a silent pass.
    """
    target = (database_url or TEST_DATABASE_URL or "").strip()
    if not target:
        pytest.skip("JOYHOUSEBOT_TEST_POSTGRES_URL is not configured")
    if target not in _probe_failures:
        try:
            with psycopg.connect(target, connect_timeout=3, autocommit=True):
                _probe_failures[target] = None
        except Exception as exc:  # refused connection, DNS, auth, timeout...
            _probe_failures[target] = f"{type(exc).__name__}: {exc}"
    failure = _probe_failures[target]
    if failure is not None:
        pytest.skip(f"PostgreSQL unavailable for tests at {target!r} ({failure})")
    return target


class PostgresTestStore(PostgresRuntimeStore):
    """PostgreSQL test store with the old path argument as an isolation key."""

    _last_path: str | None = None
    _last_store: "PostgresTestStore | None" = None

    def __init__(self, isolation_key=None, *args, **kwargs):  # noqa: ANN001
        require_postgres()
        kwargs.setdefault("bootstrap_model", "test/default")
        super().__init__(TEST_DATABASE_URL, auto_migrate=True, *args, **kwargs)
        key = str(isolation_key) if isolation_key is not None else "__default__"
        if key != self.__class__._last_path:
            self._clear_test_rows()
            self._seed_default_agents()
            self.__class__._last_path = key
        self.__class__._last_store = self

    def publish_capability(self, definition, *, actor_id: str = "test:fixture") -> None:  # noqa: ANN001
        """Install an active Capability fixture without adding a Runtime bypass API."""
        self.discover_capability_release(definition, actor_id=actor_id)
        with self._pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT status FROM capability_versions
                   WHERE capability_id=%s AND version=%s FOR UPDATE""",
                (definition.ref.capability_id, definition.ref.version),
            ).fetchone()
            if row is None:
                raise AssertionError("discovered Capability fixture is missing")
            if str(row["status"]) == "published":
                return
            connection.execute(
                """UPDATE capability_versions
                   SET status='published', published_at=clock_timestamp()
                   WHERE capability_id=%s AND version=%s""",
                (definition.ref.capability_id, definition.ref.version),
            )
            connection.execute(
                """UPDATE capability_definitions
                   SET current_version=%s, updated_at=clock_timestamp()
                   WHERE capability_id=%s""",
                (definition.ref.version, definition.ref.capability_id),
            )
            connection.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('capability',%s,%s,'published',%s)""",
                (definition.ref.capability_id, definition.ref.version, actor_id),
            )

    def finish_runtime_run(self, run_id: str, **kwargs):  # noqa: ANN003, ANN201
        """Return only the terminal event for test fixtures that do not need the bundle."""
        bundle = self.finish_runtime_run_bundle(run_id, **kwargs)
        return bundle[1] if bundle is not None else None

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
