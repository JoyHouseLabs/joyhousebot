"""Schema migration history, destructive gate, and shared plugin lock tests."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest

from joyhousebot.storage.postgres_locks import SCHEMA_MIGRATION_LOCK_ID
from joyhousebot.storage.postgres_migrations import migration_checksum
from joyhousebot.storage.postgres_store import PostgresRuntimeStore
from joyhousebot.storage.runtime_store import destructive_migrate_enabled
from tests.support.postgres_store import TEST_DATABASE_URL

_CORE_DOMAINS = {
    "runtime",
    "graph_revisions",
    "graph_patches",
    "graph_sagas",
    "graph_event_waits",
    "execution_loop",
    "context_manifests",
    "memory_candidates",
    "loop_decisions",
    "verifications",
    "approvals",
    "operation_reconciliations",
    "admins",
    "agents",
    "capabilities",
    "plugins",
    "scenarios",
    "clarifications",
    "rate_limits",
    "observability",
    "user_workflows",
}


@pytest.fixture()
def store() -> Iterator[PostgresRuntimeStore]:
    runtime_store = PostgresRuntimeStore(
        TEST_DATABASE_URL, application_name="joyhousebot-test-migrations"
    )
    yield runtime_store
    runtime_store.close()


def _history_row(store: PostgresRuntimeStore, name: str, version: int) -> dict | None:
    with store._pool.connection() as conn:
        return conn.execute(
            "SELECT name, version, checksum, description, applied_at"
            " FROM schema_migration_history WHERE name=%s AND version=%s",
            (name, version),
        ).fetchone()


def test_core_migrations_are_recorded(store: PostgresRuntimeStore) -> None:
    with store._pool.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (name) name, version, checksum"
            " FROM schema_migration_history WHERE name = ANY(%s)"
            " ORDER BY name, version DESC",
            (sorted(_CORE_DOMAINS),),
        ).fetchall()
    recorded = {row["name"]: row for row in rows}
    assert set(recorded) == _CORE_DOMAINS
    assert recorded["runtime"]["version"] == 5
    assert recorded["execution_loop"]["version"] == 2
    assert recorded["approvals"]["version"] == 2
    for row in recorded.values():
        assert len(row["checksum"]) == 64


def test_execution_loop_migration_reopens_with_root_turns_in_distinct_scopes(
    store: PostgresRuntimeStore,
) -> None:
    run_id = f"migration-scoped-turns-{uuid4().hex}"
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO runtime_runs(run_id,user_id,session_id,agent_id,status,prompt)
               VALUES (%s,'migration-test','migration-test','default','queued','sentinel')""",
            (run_id,),
        )
    try:
        for suffix in ("first", "second"):
            store.create_runtime_turn(
                turn_id=f"turn-{suffix}-{run_id}",
                run_id=run_id,
                task_id=None,
                scope=f"coordinator_plan:{suffix}",
                turn_index=1,
                model=None,
                request_hash=f"hash-{suffix}",
                worker_id=None,
            )

        reopened = PostgresRuntimeStore(
            TEST_DATABASE_URL,
            application_name="joyhousebot-test-migration-reopen",
        )
        try:
            assert len(reopened.list_runtime_turns(run_id)) == 2
        finally:
            reopened.close()
    finally:
        with store._pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM runtime_runs WHERE run_id=%s", (run_id,))


def test_record_migration_is_idempotent(store: PostgresRuntimeStore) -> None:
    name = f"test:{uuid4().hex}"
    ddl = "CREATE TABLE IF NOT EXISTS t_idempotent (id TEXT PRIMARY KEY);"
    with store._pool.connection() as conn, conn.transaction():
        store._record_migration(conn, name=name, version=1, ddl=ddl)
        store._record_migration(conn, name=name, version=1, ddl=ddl)
    row = _history_row(store, name, 1)
    assert row is not None
    assert row["checksum"] == migration_checksum(ddl)


def test_checksum_drift_logs_warning(
    store: PostgresRuntimeStore, caplog: pytest.LogCaptureFixture
) -> None:
    name = f"test:{uuid4().hex}"
    ddl_v1 = "CREATE TABLE IF NOT EXISTS t_drift (id TEXT PRIMARY KEY);"
    ddl_v2 = "CREATE TABLE IF NOT EXISTS t_drift (id TEXT PRIMARY KEY, extra TEXT);"
    with store._pool.connection() as conn, conn.transaction():
        store._record_migration(conn, name=name, version=1, ddl=ddl_v1)
        with caplog.at_level("WARNING", logger="joyhousebot.storage.postgres_migrations"):
            store._record_migration(conn, name=name, version=1, ddl=ddl_v2)
    assert any("checksum changed" in record.message for record in caplog.records)
    row = _history_row(store, name, 1)
    assert row is not None
    assert row["checksum"] == migration_checksum(ddl_v2)


def test_destructive_gate_requires_exact_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", raising=False)
    assert not destructive_migrate_enabled()
    for legacy_value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", legacy_value)
        assert not destructive_migrate_enabled()
    monkeypatch.setenv("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", "DROP_ALL_TABLES")
    assert destructive_migrate_enabled()


def _insert_sentinel_run(store: PostgresRuntimeStore, run_id: str) -> None:
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO runtime_runs(run_id,user_id,session_id,agent_id,status,prompt)
               VALUES (%s,'migration-test','migration-test','default','queued','sentinel')
               ON CONFLICT(run_id) DO NOTHING""",
            (run_id,),
        )
        conn.execute("DELETE FROM runtime_schema_migrations WHERE version=3")


def _sentinel_exists(store: PostgresRuntimeStore, run_id: str) -> bool:
    with store._pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 AS found FROM runtime_runs WHERE run_id=%s", (run_id,)
        ).fetchone()
    return row is not None


def test_destructive_value_one_keeps_tables(
    store: PostgresRuntimeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = f"mig-sentinel-{uuid4().hex}"
    _insert_sentinel_run(store, run_id)
    monkeypatch.setenv("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", "1")
    store.migrate()
    assert _sentinel_exists(store, run_id)
    with store._pool.connection() as conn, conn.transaction():
        conn.execute("DELETE FROM runtime_runs WHERE run_id=%s", (run_id,))


def test_destructive_phrase_drops_legacy_tables(
    store: PostgresRuntimeStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = f"mig-sentinel-{uuid4().hex}"
    _insert_sentinel_run(store, run_id)
    monkeypatch.setenv("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", "DROP_ALL_TABLES")
    with caplog.at_level("CRITICAL", logger="joyhousebot.storage.postgres_migrations"):
        store.migrate()
    assert not _sentinel_exists(store, run_id)
    critical = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert critical and "runtime_runs" in critical[0].message
    # migrate() always re-records version 3, so later startups stay incremental.
    with store._pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 AS ready FROM runtime_schema_migrations WHERE version=3"
        ).fetchone()
    assert row is not None


def test_schema_migration_lock_blocks_other_sessions(
    store: PostgresRuntimeStore,
) -> None:
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as probe:
        with store.schema_migration_lock():
            row = probe.execute(
                "SELECT pg_try_advisory_lock(%s) AS got", (SCHEMA_MIGRATION_LOCK_ID,)
            ).fetchone()
            assert row[0] is False
        row = probe.execute(
            "SELECT pg_try_advisory_lock(%s) AS got", (SCHEMA_MIGRATION_LOCK_ID,)
        ).fetchone()
        assert row[0] is True
        probe.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_MIGRATION_LOCK_ID,))
