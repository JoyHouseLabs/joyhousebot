import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from joyhousebot.runtime.models import AgentEvent
from joyhousebot.storage.content_blobs import LocalContentBlobStore
from tests.support.postgres_store import PostgresTestStore


def _create_run(store, run_id: str, *, kind: str = "agent"):
    return store.create_runtime_run(
        run_id=run_id,
        user_id="test-user",
        session_id="main",
        agent_id="default",
        kind=kind,
        prompt="test",
        options={},
    )[0]


def test_work_probes_reflect_pending_runs_and_tasks(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "probes.db")
    assert store.has_incomplete_runtime_work() is False
    assert store.has_claimable_runtime_task() is False

    run = _create_run(store, "probe-run")
    assert store.has_incomplete_runtime_work() is True
    assert store.has_claimable_runtime_task() is False

    store.create_runtime_task(
        task_id="probe-task", run_id=run.run_id, name="probe", payload={}
    )
    assert store.has_claimable_runtime_task() is True

    claimed = store.claim_runtime_task(worker_id="w1", lease_seconds=30)
    assert claimed is not None and claimed.task_id == "probe-task"
    assert store.has_claimable_runtime_task() is False

    store.update_runtime_run(run.run_id, status="completed")
    assert store.has_incomplete_runtime_work() is False

    # A queued run whose cancel was requested still counts as pending work.
    queued = _create_run(store, "probe-cancel")
    store.request_runtime_cancel(queued.run_id, reason="test")
    assert store.has_incomplete_runtime_work() is True


def test_lease_sweep_is_throttled_per_worker(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "lease-sweep.db")
    run = _create_run(store, "sweep-run")
    store.create_runtime_task(
        task_id="sweep-task", run_id=run.run_id, name="s", payload={}, max_attempts=3
    )

    first = store.claim_runtime_task(worker_id="w1", lease_seconds=30)
    assert first is not None
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            "UPDATE runtime_tasks SET lease_expires_at=clock_timestamp()-interval '1 minute'"
            " WHERE task_id='sweep-task'"
        )

    # A sweep ran moments ago: the throttled claim does not requeue yet.
    store._lease_sweep_at = time.monotonic()
    assert store.claim_runtime_task(worker_id="w2", lease_seconds=30) is None
    assert store.get_runtime_task("sweep-task").status == "running"

    # Once the throttle window passes, the sweep requeues the expired lease.
    store._lease_sweep_at = 0.0
    recovered = store.claim_runtime_task(worker_id="w2", lease_seconds=30)
    assert recovered is not None
    assert recovered.task_id == "sweep-task"
    assert recovered.attempt == first.attempt + 1


@pytest.mark.asyncio
async def test_purge_covers_occurrences_invocations_and_artifacts(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "purge-coverage.db")
    # schedule_occurrences is created lazily by the scheduling repository;
    # mirror its DDL here without pulling in the cron import cycle.
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schedule_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scheduled_for_ms BIGINT NOT NULL,
                status TEXT NOT NULL,
                worker_id TEXT,
                lease_version BIGINT NOT NULL,
                run_id TEXT,
                error TEXT,
                started_at_ms BIGINT NOT NULL,
                finished_at_ms BIGINT,
                UNIQUE(schedule_id, scheduled_for_ms)
            )"""
        )
    run = _create_run(store, "coverage-run")
    store.add_runtime_artifact(
        artifact_id="coverage-run:out",
        run_id=run.run_id,
        name="out",
        media_type="text/plain",
        content="done",
    )
    now_ms = int(time.time() * 1000)
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO capability_invocations
                   (invocation_id,capability_id,capability_version,capability_kind,
                    user_id,agent_id,session_id,run_id,trace_id,status,
                    idempotency_key,timeout_seconds,finished_at)
               VALUES ('ci-finished','cap','v1','tool','test-user','default','main',%s,
                       'trace','succeeded','k1',30,clock_timestamp())""",
            (run.run_id,),
        )
        conn.execute(
            """INSERT INTO capability_invocations
                   (invocation_id,capability_id,capability_version,capability_kind,
                    user_id,agent_id,session_id,run_id,trace_id,status,
                    idempotency_key,timeout_seconds)
               VALUES ('ci-active','cap','v1','tool','test-user','default','main',%s,
                       'trace','running','k2',30)""",
            (run.run_id,),
        )
        conn.execute(
            """INSERT INTO schedule_occurrences
                   (occurrence_id,schedule_id,user_id,scheduled_for_ms,status,
                    lease_version,started_at_ms,finished_at_ms)
               VALUES ('occ-finished','sched','test-user',%s,'completed',0,%s,%s)""",
            (now_ms, now_ms, now_ms),
        )
        conn.execute(
            """INSERT INTO schedule_occurrences
                   (occurrence_id,schedule_id,user_id,scheduled_for_ms,status,
                    lease_version,started_at_ms)
               VALUES ('occ-running','sched','test-user',%s,'running',0,%s)""",
            (now_ms + 60_000, now_ms),
        )

    future_ms = now_ms + 60_000
    counts = await store.purge_old_runtime_data(future_ms)
    assert counts["runtime_artifacts"] == 1
    assert counts["capability_invocations"] == 1
    assert counts["schedule_occurrences"] == 1

    assert store.list_runtime_artifacts(run.run_id) == []
    with store._pool.connection() as conn:
        remaining = conn.execute(
            "SELECT invocation_id FROM capability_invocations ORDER BY invocation_id"
        ).fetchall()
        occurrences = conn.execute(
            "SELECT occurrence_id FROM schedule_occurrences ORDER BY occurrence_id"
        ).fetchall()
    # Unfinished rows are operational state, not history: they survive purge.
    assert [row["invocation_id"] for row in remaining] == ["ci-active"]
    assert [row["occurrence_id"] for row in occurrences] == ["occ-running"]


@pytest.mark.asyncio
async def test_diagnostics_retention_is_independent(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "purge-diagnostics.db")
    run = _create_run(store, "diag-run")
    span = store.start_execution_span(
        span_id="span-1", trace_id="trace-1", run_id=run.run_id,
        span_kind="model", name="call",
    )
    assert span is not None
    store.create_model_invocation(
        invocation_id="inv-1", run_id=run.run_id, span_id="span-1",
        model="test-model", operation="chat",
    )
    store.append_reasoning_segment(
        invocation_id="inv-1", run_id=run.run_id, source="model", content="thinking"
    )
    store.put_trace_blob(run_id=run.run_id, kind="request", content={"prompt": "hi"})
    store.append_runtime_event(AgentEvent(run_id=run.run_id, type="run.queued", data={}))

    future_ms = int(time.time() * 1000) + 60_000
    # Global retention expires, diagnostics retention does not: model traces
    # survive while operational events are already purged.
    counts = await store.purge_old_runtime_data(future_ms, 0)
    assert counts["runtime_events"] == 1
    assert counts["model_invocations"] == 0
    assert counts["model_reasoning_segments"] == 0
    assert counts["execution_spans"] == 0
    assert counts["trace_blobs"] == 0
    assert store.list_model_invocations(run.run_id)
    assert store.list_reasoning_segments(run.run_id)
    assert store.list_trace_blobs(run.run_id)
    assert store.list_execution_spans(run.run_id)

    # The diagnostics cutoff expires the whole trace cluster in FK order.
    counts = await store.purge_old_runtime_data(future_ms, future_ms)
    assert counts["model_invocations"] == 1
    assert counts["model_reasoning_segments"] == 1
    assert counts["execution_spans"] == 1
    assert counts["trace_blobs"] == 1
    assert store.list_model_invocations(run.run_id) == []
    assert store.list_trace_blobs(run.run_id) == []


@pytest.mark.asyncio
async def test_trace_blob_expires_at_is_enforced(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "trace-expiry.db")
    run = _create_run(store, "blob-run")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    store.put_trace_blob(
        blob_id="blob-expired", run_id=run.run_id, kind="request",
        content={"a": 1}, expires_at=past,
    )
    store.put_trace_blob(
        blob_id="blob-live", run_id=run.run_id, kind="request",
        content={"b": 2}, expires_at=future,
    )

    # Expired blobs read as absent even before any purge runs.
    assert store.get_trace_blob("blob-expired") is None
    assert store.get_trace_blob("blob-live") is not None
    assert [b.blob_id for b in store.list_trace_blobs(run.run_id)] == ["blob-live"]

    # Purge drops expired blobs regardless of the created_at cutoff.
    counts = await store.purge_old_runtime_data(0, 0)
    assert counts["trace_blobs"] == 1
    with store._pool.connection() as conn:
        remaining = conn.execute("SELECT blob_id FROM trace_blobs").fetchall()
    assert [row["blob_id"] for row in remaining] == ["blob-live"]


@pytest.mark.asyncio
async def test_content_blob_gc_is_two_phase_and_follows_database_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import joyhousebot.storage.postgres_maintenance as maintenance

    monkeypatch.setattr(maintenance, "_CONTENT_BLOB_GC_GRACE_SECONDS", 0)
    store = PostgresTestStore(tmp_path / "content-blob-gc.db")
    store.blob_store = LocalContentBlobStore(tmp_path / "blobs")
    store.blob_inline_threshold_bytes = 32
    run = _create_run(store, "content-blob-gc")
    trace = store.put_trace_blob(
        run_id=run.run_id,
        kind="request",
        content={"payload": "externalized-" * 100},
    )
    path = store.blob_store._path(trace.sha256)
    assert path.exists()

    future_ms = int(time.time() * 1000) + 60_000
    first = await store.purge_old_runtime_data(future_ms, future_ms)
    assert first["trace_blobs"] == 1
    assert first["content_blobs"] == 0
    assert path.exists()

    second = await store.purge_old_runtime_data(future_ms, future_ms)
    assert second["content_blobs"] == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_events_purge_tombstone_reaches_sse_replay(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "tombstone.db")
    run = _create_run(store, "tombstone-run")
    store.append_runtime_event(AgentEvent(run_id=run.run_id, type="run.queued", data={}))
    store.append_runtime_log(run_id=run.run_id, stage="test", message="old")

    future_ms = int(time.time() * 1000) + 60_000
    counts = await store.purge_old_runtime_data(future_ms)
    assert counts["runtime_runs_tombstoned"] == 1

    from joyhousebot.runtime.events import EventBroker

    broker = EventBroker(store)
    stream = broker.subscribe(run.run_id)
    first = await asyncio.wait_for(anext(stream), timeout=2)
    assert first.type == "run.history_purged"
    await stream.aclose()

    # The tombstone fires once; untombstoned runs replay without it.
    fresh = _create_run(store, "fresh-run")
    store.append_runtime_event(AgentEvent(run_id=fresh.run_id, type="run.queued", data={}))
    stream = broker.subscribe(fresh.run_id)
    first = await asyncio.wait_for(anext(stream), timeout=2)
    assert first.type == "run.queued"
    await stream.aclose()


def test_session_state_message_tail_is_bounded() -> None:
    from joyhousebot.session.models import Session
    from joyhousebot.session.runtime_manager import (
        SESSION_STATE_MAX_MESSAGES,
        RuntimeSessionManager,
    )

    class _Store:
        def __init__(self) -> None:
            self.state = None

        def save_session_state(self, storage_key, *, session_key, namespace, state):
            self.state = state

    store = _Store()
    manager = RuntimeSessionManager(store)
    session = Session(key="k")
    session.messages = [
        {"role": "user", "content": f"m{index}"} for index in range(250)
    ]
    session.last_consolidated = 120
    manager.save(session)

    assert len(store.state["messages"]) == SESSION_STATE_MAX_MESSAGES
    assert store.state["messages"][0]["content"] == "m50"
    assert store.state["messages"][-1]["content"] == "m249"
    # The consolidation offset stays a valid index into the truncated list.
    assert store.state["last_consolidated"] == 70
    # The live session object is not mutated by persistence.
    assert len(session.messages) == 250


def test_session_state_message_tail_has_serialized_byte_limit() -> None:
    from joyhousebot.domain.identity import canonical_json
    from joyhousebot.session.models import Session
    from joyhousebot.session.runtime_manager import (
        SESSION_STATE_MAX_MESSAGE_BYTES,
        RuntimeSessionManager,
    )

    class _Store:
        state = None

        def save_session_state(self, storage_key, *, session_key, namespace, state):
            self.state = state

    store = _Store()
    manager = RuntimeSessionManager(store)
    session = Session(key="large")
    session.messages = [
        {"role": "tool", "content": f"{index}:" + ("大" * 50_000)}
        for index in range(6)
    ]
    session.last_consolidated = 5
    manager.save(session)

    persisted = canonical_json(store.state["messages"]).encode("utf-8")
    assert len(persisted) <= SESSION_STATE_MAX_MESSAGE_BYTES
    assert store.state["messages"][-1]["content"].startswith("5:")
    assert store.state["last_consolidated"] < len(store.state["messages"])
    assert len(session.messages) == 6
