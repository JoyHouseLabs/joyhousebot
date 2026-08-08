import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from joyhousebot.config.schema import Config
from joyhousebot.runtime.models import AgentEvent, GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.narrative import redact_runtime_value
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.runtime.tracking import safe_trace_data
from joyhousebot.storage.factory import create_runtime_store
from tests.support.postgres_store import PostgresTestStore, require_postgres


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


def test_postgres_connection_context_releases_pool_slot(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "closed-connections.db")
    with store._pool.connection() as conn:
        assert conn.execute("SELECT 1 AS value").fetchone()["value"] == 1

    # Exercise the high-frequency polling path without leaking pool slots.
    for _ in range(400):
        assert store.healthcheck()["ok"] is True


def test_runtime_artifacts_preserve_plain_text_jsonb_scalars(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "plain-artifact.db")
    run = _create_run(store, "plain-artifact")
    store.add_runtime_artifact(
        artifact_id="plain-artifact:final",
        run_id=run.run_id,
        name="final-output",
        media_type="text/plain",
        content="A plain-text final answer",
    )
    assert store.list_runtime_artifacts(run.run_id)[0]["content"] == "A plain-text final answer"


def test_postgres_runtime_fencing_logs_and_graph_reconciliation(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "runtime.db")
    _create_run(store, "fenced")
    first = store.claim_runtime_run("fenced", worker_id="old", lease_seconds=5)
    assert first is not None

    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            "UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 minute' WHERE run_id='fenced'"
        )
    second = store.claim_runtime_run("fenced", worker_id="new", lease_seconds=5)
    assert second is not None
    assert second.lease_version == first.lease_version + 1
    assert not store.update_runtime_run(
        "fenced",
        status="completed",
        worker_id="old",
        lease_version=first.lease_version,
    )
    assert store.update_runtime_run(
        "fenced",
        status="completed",
        worker_id="new",
        lease_version=second.lease_version,
    )

    log = store.append_runtime_log(
        run_id="fenced", stage="test", message="persisted", data={"value": 1}
    )
    assert store.list_runtime_logs("fenced", after_sequence=log.sequence - 1) == [log]

    _create_run(store, "graph", kind="graph")
    store.create_runtime_task(task_id="graph:a", run_id="graph", name="a", payload={"spec_id": "a"})
    store.create_runtime_task(
        task_id="graph:b",
        run_id="graph",
        name="b",
        payload={"spec_id": "b"},
        dependencies=["graph:a"],
    )
    store.update_runtime_task("graph:a", status="failed", error={"message": "boom"})
    counts = store.reconcile_runtime_graph("graph")
    assert counts == {"failed": 1, "skipped": 1}


def test_runtime_event_envelope_projection_and_redaction(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "observable.db")
    run = _create_run(store, "observable")
    event = store.append_runtime_event(
        AgentEvent(
            run_id=run.run_id,
            type="capability.started",
            turn_id="turn-1",
            span_id="span-1",
            tool_call_id="call-1",
            phase="acting",
            summary="正在执行测试工具",
            data={"tool": "exec", "args": {"command": "true"}},
        )
    )
    projected = store.get_runtime_run(run.run_id)
    assert projected is not None
    assert projected.current_phase == "acting"
    assert projected.status_summary == "正在执行测试工具"
    assert projected.active_turn_id == "turn-1"
    assert projected.active_span_count == 1
    assert projected.last_event_sequence == event.sequence


def test_full_fidelity_model_observability_round_trip(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "model-observability.db")
    run = _create_run(store, "observed-model")
    request = store.put_trace_blob(
        run_id=run.run_id,
        invocation_id="model-1",
        kind="model.request",
        content={"messages": [{"role": "user", "content": "explain"}]},
    )
    span = store.start_execution_span(
        span_id="span-model-1",
        trace_id="trace-1",
        run_id=run.run_id,
        turn_id="turn-1",
        span_kind="model",
        name="openai:chat.completion",
    )
    invocation = store.create_model_invocation(
        invocation_id="model-1",
        run_id=run.run_id,
        turn_id="turn-1",
        span_id=span.span_id,
        provider="openai",
        model="gpt-test",
        operation="chat.completion",
        request_blob_id=request.blob_id,
        request_hash=request.sha256,
    )
    segment = store.append_reasoning_segment(
        invocation_id=invocation.invocation_id,
        run_id=run.run_id,
        source="provider_native",
        kind="analysis",
        content="inspect the evidence",
        fidelity="exact",
    )
    response = store.put_trace_blob(
        run_id=run.run_id,
        invocation_id=invocation.invocation_id,
        kind="model.response",
        content={"content": "done", "reasoning_content": segment.content},
    )
    assert store.mark_model_invocation_first_token(invocation.invocation_id)
    assert store.finish_model_invocation(
        invocation.invocation_id,
        response_blob_id=response.blob_id,
        response_hash=response.sha256,
        status="completed",
        finish_reason="stop",
        reasoning_availability="provider_native",
        usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    )

    restored = store.list_model_invocations(run.run_id)[0]
    assert restored.reasoning_availability == "provider_native"
    assert restored.ttft_ms is not None
    assert store.list_reasoning_segments(run.run_id) == [segment]
    assert store.get_trace_blob(request.blob_id).content["messages"][0]["content"] == "explain"
    assert store.list_execution_spans(run.run_id)[0].status == "completed"

    replay = store.create_replay_run(
        source_run_id=run.run_id,
        mode="offline",
        created_by="test",
        status="completed",
        comparison={"content_equal": True},
    )
    assert store.list_replay_runs(run.run_id) == [replay]


def test_graph_holds_same_conversation_fifo_lane(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-session-lane.db")
    store.create_runtime_run(
        run_id="graph-lane",
        user_id="user-a",
        session_id="shared-session",
        agent_id="default",
        kind="graph",
        prompt="first",
        options={"max_concurrent": 1},
        total_task_count=1,
    )
    store.create_runtime_task(
        task_id="graph-lane:task",
        run_id="graph-lane",
        name="task",
        payload={"spec_id": "task", "prompt": "work"},
    )
    store.create_runtime_run(
        run_id="agent-after-graph",
        user_id="user-a",
        session_id="shared-session",
        agent_id="default",
        kind="agent",
        prompt="second",
        options={},
    )

    task = store.claim_runtime_task(worker_id="graph-worker", run_id="graph-lane")
    assert task is not None
    assert store.start_runtime_graph("graph-lane")
    assert (
        store.claim_runtime_run("agent-after-graph", worker_id="agent-worker", lease_seconds=30)
        is None
    )
    assert store.update_runtime_task(
        task.task_id,
        status="completed",
        result={"status": "completed"},
        worker_id="graph-worker",
        lease_version=task.lease_version,
    )
    assert store.update_runtime_run("graph-lane", status="completed")
    assert (
        store.claim_runtime_run("agent-after-graph", worker_id="agent-worker", lease_seconds=30)
        is not None
    )


def test_postgres_serializes_top_level_session_but_allows_child_agents(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "session-claims.db")
    first = _create_run(store, "root-first")
    second = _create_run(store, "root-second")
    child = store.create_runtime_run(
        run_id="child",
        user_id=first.user_id,
        session_id=first.session_id,
        agent_id="researcher",
        kind="agent",
        prompt="child task",
        options={},
        root_run_id=first.run_id,
        parent_run_id=first.run_id,
    )[0]

    first_claim = store.claim_runtime_run(first.run_id, worker_id="worker-a", lease_seconds=30)
    assert first_claim is not None
    assert store.claim_runtime_run(second.run_id, worker_id="worker-b", lease_seconds=30) is None

    # A child belongs to the same conversation but must be able to run while
    # its parent is waiting for delegated work.
    child_claim = store.claim_runtime_run(child.run_id, worker_id="worker-b", lease_seconds=30)
    assert child_claim is not None
    assert store.update_runtime_run(
        child.run_id,
        status="completed",
        worker_id="worker-b",
        lease_version=child_claim.lease_version,
    )
    assert store.update_runtime_run(
        first.run_id,
        status="completed",
        worker_id="worker-a",
        lease_version=first_claim.lease_version,
    )
    assert store.claim_runtime_run(second.run_id, worker_id="worker-b", lease_seconds=30)


def test_postgres_atomically_commits_terminal_state_and_event(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "atomic-finish.db")
    run = _create_run(store, "atomic")
    claimed = store.claim_runtime_run(run.run_id, worker_id="owner", lease_seconds=30)
    assert claimed is not None
    terminal = store.finish_runtime_run(
        run.run_id,
        status="completed",
        event=AgentEvent(
            run_id=run.run_id,
            type="run.completed",
            status="completed",
            summary="任务已完成",
            data={"content": "done"},
        ),
        result={"content": "done"},
        worker_id="owner",
        lease_version=claimed.lease_version,
    )
    assert terminal is not None and terminal.sequence is not None
    projected = store.get_runtime_run(run.run_id)
    assert projected is not None
    assert projected.status == "completed"
    assert projected.result == {"content": "done"}
    assert projected.last_event_sequence == terminal.sequence
    assert [event.type for event in store.list_runtime_events(run.run_id)] == ["run.completed"]

    # A stale worker cannot write a second terminal event after ownership ends.
    stale = store.finish_runtime_run(
        run.run_id,
        status="failed",
        event=AgentEvent(run_id=run.run_id, type="run.failed", data={"error": "stale"}),
        error={"message": "stale"},
        worker_id="owner",
        lease_version=claimed.lease_version,
    )
    assert stale is None
    assert [event.type for event in store.list_runtime_events(run.run_id)] == ["run.completed"]


def test_heartbeat_fails_once_lease_has_expired(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "heartbeat-expiry.db")
    run = _create_run(store, "heartbeat-expiry")
    claimed = store.claim_runtime_run(run.run_id, worker_id="owner", lease_seconds=30)
    assert claimed is not None

    # A live lease renews normally.
    assert store.heartbeat_runtime_run(
        run.run_id,
        worker_id="owner",
        lease_seconds=30,
        lease_version=claimed.lease_version,
    )

    # A zombie worker whose lease already expired must not resurrect it.
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE runtime_runs
               SET lease_expires_at=clock_timestamp() - interval '1 second'
               WHERE run_id=%s""",
            (run.run_id,),
        )
    assert not store.heartbeat_runtime_run(
        run.run_id,
        worker_id="owner",
        lease_seconds=30,
        lease_version=claimed.lease_version,
    )

    # The expired lease stays expired, so a fresh worker can take over.
    recovered = store.claim_runtime_run(run.run_id, worker_id="recovery", lease_seconds=30)
    assert recovered is not None and recovered.lease_owner == "recovery"


def test_cancel_request_fences_live_lease_and_blocks_execution_start(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "cancel-fencing.db")
    run = _create_run(store, "cancel-fencing")
    claimed = store.claim_runtime_run(run.run_id, worker_id="owner", lease_seconds=30)
    assert claimed is not None

    request = store.request_runtime_cancel(run.run_id, reason="stop")
    assert request == {"status": "running", "lease_alive": True}
    record = store.get_runtime_run(run.run_id)
    assert record.cancel_requested_at is not None
    assert record.cancel_reason == "stop"

    # A cancel-requested run cannot be re-claimed, heartbeated, or started.
    assert store.claim_runtime_run(run.run_id, worker_id="other", lease_seconds=30) is None
    assert not store.heartbeat_runtime_run(
        run.run_id,
        worker_id="owner",
        lease_seconds=30,
        lease_version=claimed.lease_version,
    )
    assert not store.update_runtime_run(
        run.run_id,
        status="running",
        worker_id="owner",
        lease_version=claimed.lease_version,
    )

    # A non-owner cannot force a terminal state while the lease is alive.
    forced = store.finish_runtime_run(
        run.run_id,
        status="cancelled",
        event=AgentEvent(run_id=run.run_id, type="run.cancelled", data={"reason": "stop"}),
        error={"message": "stop"},
    )
    assert forced is None

    # The owning worker still commits the terminal state with fencing.
    finished = store.finish_runtime_run(
        run.run_id,
        status="cancelled",
        event=AgentEvent(run_id=run.run_id, type="run.cancelled", data={"reason": "stop"}),
        error={"message": "stop"},
        worker_id="owner",
        lease_version=claimed.lease_version,
    )
    assert finished is not None
    assert store.get_runtime_run(run.run_id).status == "cancelled"


def test_cancel_request_on_dead_lease_can_be_finished_by_recovery(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "cancel-recovery.db")
    run = _create_run(store, "cancel-recovery")
    claimed = store.claim_runtime_run(run.run_id, worker_id="dead", lease_seconds=5)
    assert claimed is not None
    request = store.request_runtime_cancel(run.run_id, reason="stop")
    assert request == {"status": "running", "lease_alive": True}

    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            "UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 minute'"
            " WHERE run_id='cancel-recovery'"
        )

    # The dead worker's run is never re-claimed for execution ...
    assert store.claim_runtime_run(run.run_id, worker_id="recovery", lease_seconds=5) is None
    # ... and a non-owner may finish it only after the lease expired.
    finished = store.finish_runtime_run(
        run.run_id,
        status="cancelled",
        event=AgentEvent(run_id=run.run_id, type="run.cancelled", data={"reason": "stop"}),
        error={"message": "stop"},
    )
    assert finished is not None
    assert store.get_runtime_run(run.run_id).status == "cancelled"


def test_reset_runtime_run_clears_cancel_request(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "cancel-reset.db")
    run = _create_run(store, "cancel-reset")
    request = store.request_runtime_cancel(run.run_id, reason="stop")
    assert request == {"status": "queued", "lease_alive": False}
    finished = store.finish_runtime_run(
        run.run_id,
        status="cancelled",
        event=AgentEvent(run_id=run.run_id, type="run.cancelled", data={"reason": "stop"}),
        error={"message": "stop"},
    )
    assert finished is not None

    assert store.reset_runtime_run(run.run_id)
    record = store.get_runtime_run(run.run_id)
    assert record.status == "queued"
    assert record.cancel_requested_at is None
    assert record.cancel_reason is None
    reclaimed = store.claim_runtime_run(run.run_id, worker_id="worker", lease_seconds=5)
    assert reclaimed is not None
    # Leave no active run behind: this database is shared by the whole suite.
    assert store.update_runtime_run(
        run.run_id,
        status="completed",
        worker_id="worker",
        lease_version=reclaimed.lease_version,
    )


def test_runtime_store_factory_requires_postgres_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOYHOUSEBOT_DATABASE_URL", raising=False)
    config = Config()
    config.runtime.store.database_url = ""
    with pytest.raises(ValueError, match="database_url"):
        create_runtime_store(config)


@pytest.mark.postgres
def test_postgres_serializes_concurrent_migrations_and_maintenance() -> None:
    database_url = require_postgres()
    import psycopg

    from joyhousebot.storage.postgres_locks import SCHEMA_MIGRATION_LOCK_ID
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    def create_store(index: int) -> PostgresRuntimeStore:
        return PostgresRuntimeStore(
            database_url,
            min_pool_size=1,
            max_pool_size=1,
            application_name=f"concurrent-migration-{index}",
        )

    stores: list[PostgresRuntimeStore] = []
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            stores = list(executor.map(create_store, range(4)))

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda store: asyncio.run(store.purge_old_runtime_data(0)),
                    stores,
                )
            )
        assert len(results) == 4

        with psycopg.connect(database_url, autocommit=True) as migration_connection:
            migration_connection.execute(
                "SELECT pg_advisory_lock(%s)", (SCHEMA_MIGRATION_LOCK_ID,)
            )
            try:
                assert asyncio.run(stores[0].purge_old_runtime_data(0)) == {}
            finally:
                migration_connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (SCHEMA_MIGRATION_LOCK_ID,)
                )
    finally:
        for store in stores:
            store.close()


@pytest.mark.postgres
def test_postgres_runtime_store_conformance() -> None:
    database_url = require_postgres()
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    store = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=2)
    try:
        run_id = f"test-{uuid4().hex}"
        created = _create_run(store, run_id)
        assert created.status == "queued"
        claimed = store.claim_runtime_run(run_id, worker_id="pytest", lease_seconds=5)
        assert claimed is not None and claimed.lease_version == 1
        assert store.heartbeat_runtime_run(
            run_id,
            worker_id="pytest",
            lease_seconds=5,
            lease_version=claimed.lease_version,
        )
        store.append_runtime_log(run_id=run_id, stage="test", message="postgres")
        assert "postgres" in {log.message for log in store.list_runtime_logs(run_id)}
        assert store.healthcheck()["backend"] == "postgres"

        request = store.put_trace_blob(
            run_id=run_id,
            invocation_id=f"model-{run_id}",
            kind="model.request",
            content={"messages": [{"role": "user", "content": "postgres trace"}]},
        )
        span = store.start_execution_span(
            span_id=f"span-{run_id}",
            trace_id=f"trace-{run_id}",
            run_id=run_id,
            span_kind="model",
            name="anthropic:messages.stream",
        )
        invocation = store.create_model_invocation(
            invocation_id=f"model-{run_id}",
            run_id=run_id,
            span_id=span.span_id,
            provider="anthropic",
            model="claude-test",
            operation="messages.stream",
            request_blob_id=request.blob_id,
            request_hash=request.sha256,
        )
        reasoning = store.append_reasoning_segment(
            invocation_id=invocation.invocation_id,
            run_id=run_id,
            source="provider_native",
            content="postgres exact reasoning",
            fidelity="exact",
        )
        assert store.mark_model_invocation_first_token(invocation.invocation_id)
        assert store.finish_model_invocation(
            invocation.invocation_id,
            status="completed",
            finish_reason="stop",
            reasoning_availability="provider_native",
            usage={"total_tokens": 5},
        )
        assert store.list_reasoning_segments(run_id) == [reasoning]
        assert store.list_execution_spans(run_id)[0].status == "completed"
        assert store.get_trace_blob(request.blob_id).content["messages"][0]["content"] == (
            "postgres trace"
        )
        replay = store.create_replay_run(
            source_run_id=run_id,
            mode="offline",
            created_by="pytest",
            status="completed",
            comparison={"content_equal": True},
        )
        assert store.list_replay_runs(run_id) == [replay]
        store.put_model_response_cache(
            f"cache-{run_id}",
            provider="anthropic",
            model="claude-test",
            response={"content": "cached"},
        )
        assert store.get_model_response_cache(f"cache-{run_id}")["response"] == {
            "content": "cached"
        }
        assert store.update_runtime_run(
            run_id,
            status="completed",
            worker_id="pytest",
            lease_version=claimed.lease_version,
        )

        graph_id = f"test-graph-{uuid4().hex}"
        graph, created = store.create_runtime_graph(
            run_id=graph_id,
            user_id="test-user",
            session_id="main",
            agent_id="default",
            prompt="parallel",
            options={"max_concurrent": 2},
            tasks=[
                {
                    "task_id": f"{graph_id}:a",
                    "agent_id": "default",
                    "name": "a",
                    "payload": {"spec_id": "a"},
                    "dependencies": [],
                    "priority": 0,
                    "max_attempts": 1,
                },
                {
                    "task_id": f"{graph_id}:b",
                    "agent_id": "default",
                    "name": "b",
                    "payload": {"spec_id": "b"},
                    "dependencies": [],
                    "priority": 1,
                    "max_attempts": 1,
                },
            ],
        )
        assert created and graph.status == "queued"
        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed_tasks = list(
                executor.map(
                    lambda worker: store.claim_runtime_task(
                        worker_id=worker, lease_seconds=5, run_id=graph_id
                    ),
                    ("worker-a", "worker-b"),
                )
            )
        assert {task.task_id for task in claimed_tasks if task is not None} == {
            f"{graph_id}:a",
            f"{graph_id}:b",
        }
        for task in claimed_tasks:
            assert task is not None
            assert store.update_runtime_task(
                task.task_id,
                status="completed",
                result={"status": "completed"},
                worker_id=task.lease_owner,
                lease_version=task.lease_version,
            )

        planning_id = f"test-planning-{uuid4().hex}"
        store.create_runtime_run(
            run_id=planning_id,
            user_id="test-user",
            session_id="planning",
            agent_id="default",
            kind="agent",
            prompt="clarified fixed scenario",
            options={},
            initial_status="planning",
        )
        promoted = store.materialize_runtime_graph(
            run_id=planning_id,
            user_id="test-user",
            options={"max_concurrent": 1},
            tasks=[
                {
                    "task_id": f"{planning_id}:execute",
                    "agent_id": "default",
                    "name": "execute",
                    "payload": {"spec_id": "execute", "prompt": "run"},
                    "dependencies": [],
                    "priority": 0,
                    "max_attempts": 1,
                }
            ],
        )
        assert promoted.kind == "graph" and promoted.status == "queued"
        assert len(store.list_runtime_tasks(run_id=planning_id)) == 1

        session_id = f"session-{uuid4().hex}"
        root_ids = [f"session-run-{uuid4().hex}" for _ in range(2)]
        for root_id in root_ids:
            store.create_runtime_run(
                run_id=root_id,
                user_id="session-user",
                session_id=session_id,
                agent_id="default",
                kind="agent",
                prompt="serialize me",
                options={},
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            session_claims = list(
                executor.map(
                    lambda item: store.claim_runtime_run(
                        item[1], worker_id=item[0], lease_seconds=30
                    ),
                    (("session-worker-a", root_ids[0]), ("session-worker-b", root_ids[1])),
                )
            )
        assert len([claim for claim in session_claims if claim is not None]) == 1
    finally:
        store.close()


@pytest.mark.postgres
def test_postgres_submission_quota_is_cluster_atomic() -> None:
    database_url = require_postgres()
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    store = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=4)
    user_id = f"quota-{uuid4().hex}"

    def submit(index: int):
        try:
            return store.create_runtime_run(
                run_id=f"quota-run-{uuid4().hex}",
                user_id=user_id,
                session_id=f"session-{index}",
                agent_id="default",
                kind="agent",
                prompt="admit once",
                options={},
                max_active_per_user=1,
                max_submissions_per_minute=100,
            )[0]
        except ValueError as exc:
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(submit, range(2)))
        assert len([item for item in outcomes if not isinstance(item, Exception)]) == 1
        assert len([item for item in outcomes if isinstance(item, ValueError)]) == 1
    finally:
        store.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_postgres_two_runtimes_execute_one_graph() -> None:
    database_url = require_postgres()
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    class SlowAgent:
        async def process_direct(self, content: str, **_kwargs) -> str:
            await asyncio.sleep(0.1)
            return f"done:{content}"

    first_store = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=4)
    second_store = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=4)
    first = NativeAgentRuntime(agent=SlowAgent(), store=first_store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=SlowAgent(), store=second_store, max_concurrent_runs=1)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="postgres distributed graph",
                tasks=[GraphTaskSpec(id=f"t{i}", prompt=f"task-{i}") for i in range(8)],
                aggregate=False,
                max_concurrent=2,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=10)
        assert completed.status == "completed"
        claims = [
            log
            for log in first_store.list_runtime_logs(submitted.run_id)
            if log.stage == "task.claimed"
        ]
        assert len(claims) == 8
        assert len({log.worker_id for log in claims}) == 2
    finally:
        await asyncio.gather(first.close(), second.close())
        first_store.close()
        second_store.close()


@pytest.mark.asyncio
async def test_postgres_purge_old_runtime_data(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "purge.db")
    run = _create_run(store, "purge-run")
    store.append_runtime_event(AgentEvent(run_id=run.run_id, type="run.queued", data={}))
    store.append_runtime_log(run_id=run.run_id, stage="test", message="old log")
    store.append_request_trace_event(
        tracker_id="tracker-1", request_id="req-1", run_id=run.run_id, data={}
    )

    future_ms = int(time.time() * 1000) + 60_000
    counts = await store.purge_old_runtime_data(future_ms)
    assert counts == {
        "model_response_cache": 0,
        "model_reasoning_segments": 0,
        "model_invocations": 0,
        "execution_spans": 0,
        "trace_blobs": 0,
        "replay_runs": 0,
        "runtime_artifacts": 0,
        "runtime_events": 1,
        "runtime_logs": 2,
        "request_trace_events": 1,
        "runtime_runs_tombstoned": 1,
        "capability_invocations": 0,
        "schedule_occurrences": 0,
    }
    assert store.list_runtime_events(run.run_id) == []
    assert store.list_runtime_logs(run.run_id) == []
    assert store.list_request_trace_events("tracker-1") == []
    # Purging events/logs while the run survives leaves a tombstone marker.
    record = store.get_runtime_run(run.run_id)
    assert record.options["metadata"]["events_purged"] is True

    # Nothing is old enough for an ancient cutoff.
    store.append_runtime_log(run_id=run.run_id, stage="test", message="fresh log")
    counts = await store.purge_old_runtime_data(0)
    assert counts == {
        "model_response_cache": 0,
        "model_reasoning_segments": 0,
        "model_invocations": 0,
        "execution_spans": 0,
        "trace_blobs": 0,
        "replay_runs": 0,
        "runtime_artifacts": 0,
        "runtime_events": 0,
        "runtime_logs": 0,
        "request_trace_events": 0,
        "runtime_runs_tombstoned": 0,
        "capability_invocations": 0,
        "schedule_occurrences": 0,
    }
    assert len(store.list_runtime_logs(run.run_id)) == 1


def test_get_runtime_run_enforces_expected_user_id(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "tenancy.db")
    run = _create_run(store, "owned-run")
    assert store.get_runtime_run("owned-run") is not None
    assert store.get_runtime_run("owned-run", expected_user_id="test-user") is not None
    assert store.get_runtime_run("owned-run", expected_user_id="someone-else") is None
    assert run.user_id == "test-user"


def test_list_incomplete_runtime_runs_respects_limit(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "incomplete.db")
    for index in range(3):
        _create_run(store, f"queued-{index}")
    assert len(store.list_incomplete_runtime_runs()) == 3
    assert len(store.list_incomplete_runtime_runs(limit=2)) == 2


def test_delete_runtime_session_clears_leftovers(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "session-cleanup.db")
    run = _create_run(store, "session-run")
    assert store.update_runtime_run("session-run", status="completed")
    store.save_session_state(
        "7:default:main", session_key="main", namespace="default", state={"messages": []}
    )
    store.append_request_trace_event(
        tracker_id="tracker-cleanup",
        request_id="req-cleanup",
        user_id="test-user",
        run_id=run.run_id,
        data={},
    )

    deleted = store.delete_runtime_session(user_id="test-user", session_id="main")
    assert deleted == 1
    assert store.get_runtime_run("session-run") is None
    assert store.get_session_state("7:default:main") is None
    assert store.list_request_trace_events("tracker-cleanup") == []


def test_healthcheck_does_not_leak_database_path(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "health.db")
    status = store.healthcheck()
    assert status["ok"] is True
    assert status["backend"] == "postgres"
    assert str(tmp_path) not in status.values()


def test_safe_trace_data_redacts_credential_values() -> None:
    cleaned = safe_trace_data(
        {
            "note": "calling with sk-abcdefgh12345678 tomorrow",
            "headers": "Authorization: Bearer abc.def.ghi",
            "body": 'api_key="sk-abcdefgh12345678"',
            "password": "still-key-redacted",
        }
    )
    assert cleaned["note"] == "calling with ***REDACTED*** tomorrow"
    assert cleaned["headers"] == "Authorization: ***REDACTED***"
    assert "sk-abcdefgh12345678" not in cleaned["body"]
    assert cleaned["password"] == "<redacted>"


def test_redact_runtime_value_masks_credential_patterns() -> None:
    cleaned = redact_runtime_value(
        {"text": "token is sk-abcdefgh12345678 ok", "nested": ["Bearer xyz123"]}
    )
    assert cleaned["text"] == "token is ***REDACTED*** ok"
    assert cleaned["nested"] == ["***REDACTED***"]
    assert redact_runtime_value("api-key: abcdef123456") == "***REDACTED***"
