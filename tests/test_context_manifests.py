"""Context provenance, budget evidence, fencing, and public API coverage."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.context_manifest import build_turn_manifest, source_entry
from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.providers.base import LLMProvider, LLMResponse
from joyhousebot.runtime.action_identity import payload_hash
from joyhousebot.runtime.context import RunContext
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore


class _FinalProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/context"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content="context captured", finish_reason="stop")


def _create_run(
    store: PostgresTestStore,
    run_id: str,
    *,
    user_id: str = "context-owner",
    initial_status: str = "running",
) -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="private request",
        options={},
        initial_status=initial_status,
    )


def test_context_builder_records_included_and_excluded_sources(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-sources.db")
    history = [
        {"role": "user", "content": "old secret " * 100},
        {"role": "assistant", "content": "recent answer"},
    ]

    messages, sources = ContextBuilder(tmp_path, store).build_messages_with_sources(
        history=history,
        current_message="current secret request",
        channel="api",
        chat_id="private-chat",
        max_context_tokens=500,
    )

    history_sources = [item for item in sources if item["source_kind"] == "conversation_history"]
    assert [item["included"] for item in history_sources] == [False, True]
    assert history_sources[0]["excluded_reason"] == "lower_priority_context_budget"
    assert messages[-2:] == [history[-1], {"role": "user", "content": "current secret request"}]
    encoded = json.dumps(sources, ensure_ascii=False)
    assert "old secret" not in encoded
    assert "current secret request" not in encoded
    assert all(len(item["content_hash"]) == 64 for item in sources)


def test_durable_context_timestamp_freezes_dynamic_identity(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-time.db")
    builder = ContextBuilder(tmp_path, store)

    first, _ = builder.build_messages_with_sources(
        history=[],
        current_message="same request",
        context_timestamp="2026-08-08T01:02:03+00:00",
    )
    second, _ = builder.build_messages_with_sources(
        history=[],
        current_message="same request",
        context_timestamp="2026-08-08T01:02:03+00:00",
    )

    assert first == second


def test_context_manifest_is_fenced_and_frozen_across_worker_takeover(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "context-fence.db")
    _create_run(store, "context-fence", initial_status="queued")
    first = store.claim_runtime_run("context-fence", worker_id="worker-a", lease_seconds=5)
    assert first is not None
    messages = [{"role": "user", "content": "private context"}]
    context_a = RunContext(
        run_id=first.run_id,
        user_id=first.user_id,
        agent_id=first.agent_id,
        session_id=first.session_id,
        session_key="context-fence",
        channel="api",
        chat_id="runtime",
        trace_store=store,
        worker_id="worker-a",
        run_lease_version=first.lease_version,
        context_sources=(
            source_entry(
                source_kind="current_request",
                source_id="request:current",
                content="private context",
                classification="confidential",
                authority="user",
                freshness="request",
                priority=100,
                included_reason="current_user_request",
            ),
        ),
        context_initial_message_count=1,
    )
    store.create_runtime_turn(
        turn_id="turn-context-1",
        run_id=first.run_id,
        task_id=None,
        scope="execution",
        turn_index=1,
        model="test/context",
        request_hash=payload_hash(messages),
        worker_id="worker-a",
    )
    first_manifest = build_turn_manifest(
        context_a,
        turn_id="turn-context-1",
        turn_index=1,
        messages=messages,
        tools=[],
    )
    saved = store.record_context_manifest(**first_manifest)
    assert saved is not None
    assert store.record_context_manifest(**first_manifest) == saved
    conflict = {**first_manifest, "request_hash": "changed-request-hash"}
    with pytest.raises(RuntimeError, match="context manifest identity conflict"):
        store.record_context_manifest(**conflict)

    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE run_id=%s""",
            (first.run_id,),
        )
    second = store.claim_runtime_run(first.run_id, worker_id="worker-b", lease_seconds=30)
    assert second is not None and second.lease_version > first.lease_version
    assert (
        store.get_context_manifest_for_turn(
            "turn-context-1",
            worker_id="worker-a",
            run_lease_version=first.lease_version,
            task_lease_version=None,
        )
        is None
    )
    assert (
        store.get_context_manifest_for_turn(
            "turn-context-1",
            worker_id="worker-b",
            run_lease_version=second.lease_version,
            task_lease_version=None,
        )
        == saved
    )
    store.create_runtime_turn(
        turn_id="turn-context-2",
        run_id=first.run_id,
        task_id=None,
        scope="execution",
        turn_index=2,
        model="test/context",
        request_hash=payload_hash(messages),
        worker_id="worker-a",
    )
    stale_manifest = build_turn_manifest(
        context_a,
        turn_id="turn-context-2",
        turn_index=2,
        messages=messages,
        tools=[],
    )
    assert store.record_context_manifest(**stale_manifest) is None

    context_b = replace(
        context_a,
        worker_id="worker-b",
        run_lease_version=second.lease_version,
    )
    current_manifest = build_turn_manifest(
        context_b,
        turn_id="turn-context-2",
        turn_index=2,
        messages=messages,
        tools=[],
    )
    assert store.record_context_manifest(**current_manifest) is not None
    assert store.list_context_manifests(first.run_id, expected_user_id="other") == []
    assert len(store.list_context_manifests(first.run_id, expected_user_id=first.user_id)) == 2


def test_task_context_manifest_uses_task_lease_fencing(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "context-task-fence.db")
    _create_run(store, "context-task-fence", initial_status="queued")
    store.create_runtime_task(
        task_id="context-task-fence:task",
        run_id="context-task-fence",
        name="context task",
        payload={"spec_id": "task"},
        max_attempts=2,
    )
    first = store.claim_runtime_task(worker_id="task-worker-a", lease_seconds=5)
    assert first is not None
    messages = [{"role": "user", "content": "task context"}]
    context = RunContext(
        run_id=first.run_id,
        task_id=first.task_id,
        user_id="context-owner",
        agent_id="default",
        session_id="context-task-fence",
        session_key="context-task-fence",
        channel="runtime",
        chat_id="task",
        trace_store=store,
        worker_id="task-worker-a",
        task_lease_version=first.lease_version,
        context_initial_message_count=1,
    )
    store.create_runtime_turn(
        turn_id="turn-context-task-1",
        run_id=first.run_id,
        task_id=first.task_id,
        scope="execution",
        turn_index=1,
        model="test/context",
        request_hash=payload_hash(messages),
        worker_id="task-worker-a",
    )
    manifest = build_turn_manifest(
        context,
        turn_id="turn-context-task-1",
        turn_index=1,
        messages=messages,
        tools=[],
    )
    assert store.record_context_manifest(**manifest) is not None

    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE runtime_tasks
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE task_id=%s""",
            (first.task_id,),
        )
    store._lease_sweep_at = 0.0
    second = store.claim_runtime_task(worker_id="task-worker-b", lease_seconds=30)
    assert second is not None and second.lease_version > first.lease_version
    assert (
        store.get_context_manifest_for_turn(
            "turn-context-task-1",
            worker_id="task-worker-a",
            run_lease_version=None,
            task_lease_version=first.lease_version,
        )
        is None
    )
    store.create_runtime_turn(
        turn_id="turn-context-task-2",
        run_id=first.run_id,
        task_id=first.task_id,
        scope="execution",
        turn_index=2,
        model="test/context",
        request_hash=payload_hash(messages),
        worker_id="task-worker-a",
    )
    stale = build_turn_manifest(
        context,
        turn_id="turn-context-task-2",
        turn_index=2,
        messages=messages,
        tools=[],
    )
    assert store.record_context_manifest(**stale) is None
    current = build_turn_manifest(
        replace(
            context,
            worker_id="task-worker-b",
            task_lease_version=second.lease_version,
        ),
        turn_id="turn-context-task-2",
        turn_index=2,
        messages=messages,
        tools=[],
    )
    assert store.record_context_manifest(**current) is not None


@pytest.mark.asyncio
async def test_runtime_persists_manifest_before_model_and_public_api_is_redacted(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "context-runtime.db")
    run_id = "context-runtime"
    _create_run(store, run_id)
    store.create_api_access_token(
        user_id="context-owner", actor_id="test", token="context-owner-token"
    )
    store.create_api_access_token(
        user_id="other-user", actor_id="test", token="context-other-token"
    )
    provider = _FinalProvider()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/context",
        session_manager=RuntimeSessionManager(store),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    context = RunContext(
        run_id=run_id,
        user_id="context-owner",
        agent_id="default",
        session_id=f"session-{run_id}",
        session_key=f"api:context-owner:default:session-{run_id}",
        channel="api",
        chat_id="runtime",
        trace_store=store,
    )

    async def capture(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    result = await executor.process_direct(
        "top secret context",
        session_key=context.session_key,
        execution_stream_callback=capture,
        run_context=context,
    )

    assert result == "context captured"
    assert provider.calls == 1
    kinds = [kind for kind, _payload in events]
    assert kinds.index("context_built") < kinds.index("model_request_start")
    manifest_event = next(payload for kind, payload in events if kind == "context_built")
    assert manifest_event["event_id"].endswith(":context.built")
    assert len(store.list_context_manifests(run_id)) == 1

    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    with client:
        own = client.get(
            f"/v1/runs/{run_id}/context-manifest",
            headers={"Authorization": "Bearer context-owner-token"},
        )
        other = client.get(
            f"/v1/runs/{run_id}/context-manifest",
            headers={"Authorization": "Bearer context-other-token"},
        )
    assert own.status_code == 200
    body = own.json()
    assert body["items"][0]["entries"]
    assert "owner_scope" not in json.dumps(body)
    assert "top secret context" not in json.dumps(body)
    assert "worker_id" not in json.dumps(body)
    assert other.status_code == 404
    await executor.close_mcp()


@pytest.mark.asyncio
async def test_native_runtime_persists_deterministic_context_built_event(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "context-event.db")
    executor = NativeAgentExecutor(
        provider=_FinalProvider(),
        scratch_root=tmp_path,
        model="test/context",
        session_manager=RuntimeSessionManager(store),
    )
    runtime = NativeAgentRuntime(agent=executor, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="event secret",
            user_id="context-event-owner",
            session_id="context-event",
            max_turns=1,
        )
    )

    completed = await runtime.wait(submitted.run_id, timeout=5)

    assert completed.status == "completed", completed.error
    events = store.list_runtime_events(submitted.run_id)
    event_types = [item.type for item in events]
    assert event_types.index("context.built") < event_types.index("model.request.started")
    built = next(item for item in events if item.type == "context.built")
    assert built.event_id == f"{built.data['manifest_id']}:context.built"
    assert "event secret" not in json.dumps(built.data)
    assert len(store.list_context_manifests(submitted.run_id)) == 1
    await runtime.close()
