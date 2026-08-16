import asyncio
from pathlib import Path
from typing import Any

import pytest

from porthouse.agent.executor import NativeAgentExecutor
from porthouse.contracts.tools import Tool
from porthouse.domain.capabilities import CapabilityKind, CapabilityRef
from porthouse.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from porthouse.runtime.action_identity import durable_action_id
from porthouse.runtime.context import ActionOutcomeUnknownError, RunContext
from porthouse.runtime.models import AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.session.runtime_manager import RuntimeSessionManager
from tests.support.capabilities import register_tool_fixture
from tests.support.postgres_store import PostgresTestStore


class _CountingTool(Tool):
    name = "durable_count"
    description = "Count executions"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self, *, cancel: bool = False) -> None:
        self.calls = 0
        self.cancel = cancel

    async def execute(self, value: str, **_kwargs: Any) -> str:
        self.calls += 1
        if self.cancel:
            raise asyncio.CancelledError("simulated worker loss")
        return f"observed:{value}"


class _CrashAfterObservationProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/durable"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="provider-call-1",
                        name="durable_count",
                        arguments={"value": "once"},
                    )
                ],
            )
        if self.calls == 2:
            raise RuntimeError("simulated worker loss before the next model response")
        return LLMResponse(content="finished after recovery", finish_reason="stop")


class _OneToolTurnProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/durable"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(
                    id="provider-call-1",
                    name="durable_count",
                    arguments={"value": "once"},
                )
            ],
        )


def _create_run(store: PostgresTestStore, run_id: str) -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id="user-durable",
        session_id="session-durable",
        agent_id="default",
        kind="agent",
        prompt="perform durable work",
        options={},
        initial_status="running",
    )


def _context(store: PostgresTestStore, run_id: str) -> RunContext:
    return RunContext(
        run_id=run_id,
        user_id="user-durable",
        agent_id="default",
        session_id="session-durable",
        session_key="api:user-durable:default:session-durable",
        channel="api",
        chat_id="durable",
        model="test/durable",
        trace_store=store,
        worker_id="worker-test",
    )


def test_action_identity_is_stable_and_sensitive_to_position() -> None:
    ref = CapabilityRef(
        capability_id="example.write",
        version="2.1.0",
        kind=CapabilityKind.TOOL,
        plugin_id="example.plugin",
        plugin_version="1.0.0",
        plugin_build_digest="sha256:abc",
    )
    first = durable_action_id(
        run_id="run-1",
        task_id=None,
        turn_index=2,
        action_index=0,
        capability_ref=ref,
        inputs={"b": 2, "a": 1},
    )
    reordered = durable_action_id(
        run_id="run-1",
        task_id=None,
        turn_index=2,
        action_index=0,
        capability_ref=ref,
        inputs={"a": 1, "b": 2},
    )
    moved = durable_action_id(
        run_id="run-1",
        task_id=None,
        turn_index=2,
        action_index=1,
        capability_ref=ref,
        inputs={"a": 1, "b": 2},
    )

    assert first == reordered
    assert first != moved


@pytest.mark.asyncio
async def test_recovery_reuses_model_response_and_observation_without_reexecution(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "durable-recovery.db")
    run_id = "run-durable-recovery"
    _create_run(store, run_id)
    provider = _CrashAfterObservationProvider()
    tool = _CountingTool()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/durable",
        max_iterations=3,
        session_manager=RuntimeSessionManager(store),
    )
    register_tool_fixture(executor.capabilities, tool)
    context = _context(store, run_id)

    with pytest.raises(RuntimeError, match="simulated worker loss"):
        await executor.process_direct(
            "perform durable work",
            session_key=context.session_key,
            channel="api",
            chat_id="durable",
            run_context=context,
        )

    result = await executor.process_direct(
        "perform durable work",
        session_key=context.session_key,
        channel="api",
        chat_id="durable",
        run_context=context,
    )

    assert result == "finished after recovery"
    assert provider.calls == 3
    assert tool.calls == 1
    assert [turn.status for turn in store.list_runtime_turns(run_id)] == [
        "completed",
        "completed",
    ]
    actions = store.list_action_intents(run_id)
    assert len(actions) == 1
    assert actions[0].status == "observed"
    assert store.get_action_observation(actions[0].action_id) is not None
    await executor.close_tool_connectors()


@pytest.mark.asyncio
async def test_unknown_action_outcome_is_not_blindly_replayed(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "durable-unknown.db")
    run_id = "run-durable-unknown"
    _create_run(store, run_id)
    provider = _OneToolTurnProvider()
    tool = _CountingTool(cancel=True)
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/durable",
        max_iterations=2,
        session_manager=RuntimeSessionManager(store),
    )
    register_tool_fixture(executor.capabilities, tool)
    context = _context(store, run_id)

    with pytest.raises(asyncio.CancelledError):
        await executor.process_direct(
            "perform durable work",
            session_key=context.session_key,
            run_context=context,
        )
    with pytest.raises(ActionOutcomeUnknownError):
        await executor.process_direct(
            "perform durable work",
            session_key=context.session_key,
            run_context=context,
        )

    assert provider.calls == 1
    assert tool.calls == 1
    assert store.list_action_intents(run_id)[0].status == "waiting_external"
    reconciliation = store.get_action_reconciliation(
        store.list_action_intents(run_id)[0].action_id
    )
    assert reconciliation is not None
    assert reconciliation.status == "manual_required"
    await executor.close_tool_connectors()


@pytest.mark.asyncio
async def test_runtime_fails_instead_of_completing_when_loop_is_exhausted(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "durable-exhausted.db")
    provider = _OneToolTurnProvider()
    tool = _CountingTool()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/durable",
        max_iterations=1,
        session_manager=RuntimeSessionManager(store),
    )
    register_tool_fixture(executor.capabilities, tool)
    runtime = NativeAgentRuntime(agent=executor, store=store)

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="perform durable work",
            user_id="user-durable",
            session_id="session-exhausted",
            max_turns=1,
        )
    )
    finished = await runtime.wait(submitted.run_id, timeout=3)

    assert finished.status == "failed"
    assert finished.result["stop_reason"] == "loop_exhausted"
    event_types = [event.type for event in store.list_runtime_events(submitted.run_id)]
    assert "loop.exhausted" in event_types
    assert event_types.count("loop.exhausted") == 1
    assert event_types[-1] == "run.failed"
    await runtime.close()
    await executor.close_tool_connectors()


@pytest.mark.asyncio
async def test_runtime_stops_repeated_action_before_second_execution(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "durable-stalled.db")
    provider = _OneToolTurnProvider()
    tool = _CountingTool()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/durable",
        max_iterations=3,
        session_manager=RuntimeSessionManager(store),
    )
    register_tool_fixture(executor.capabilities, tool)
    runtime = NativeAgentRuntime(agent=executor, store=store)

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="repeat the same action forever",
            user_id="user-durable",
            session_id="session-stalled",
            max_turns=3,
        )
    )
    finished = await runtime.wait(submitted.run_id, timeout=3)

    assert finished.status == "failed"
    assert finished.result["stop_reason"] == "loop_stalled"
    assert provider.calls == 2
    assert tool.calls == 1
    assert [turn.status for turn in store.list_runtime_turns(submitted.run_id)] == [
        "completed",
        "failed",
    ]
    event_types = [event.type for event in store.list_runtime_events(submitted.run_id)]
    assert event_types.count("loop.stalled") == 1
    assert event_types[-1] == "run.failed"
    await runtime.close()
    await executor.close_tool_connectors()


@pytest.mark.asyncio
async def test_runtime_suspends_unknown_action_for_external_reconciliation(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "durable-waiting-external.db")

    class _UnknownOutcomeAgent:
        async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
            raise ActionOutcomeUnknownError("act_unknown", "inv_unknown")

    runtime = NativeAgentRuntime(agent=_UnknownOutcomeAgent(), store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="perform uncertain external work",
            user_id="user-durable",
            session_id="session-waiting-external",
        )
    )
    waiting = await runtime.wait(submitted.run_id, timeout=3)

    assert waiting.status == "waiting_external"
    assert waiting.lease_owner is None
    assert waiting.result == {
        "stop_reason": "waiting_external",
        "action_id": "act_unknown",
        "invocation_id": "inv_unknown",
    }
    event_types = [event.type for event in store.list_runtime_events(submitted.run_id)]
    assert event_types[-1] == "run.waiting_external"
    await runtime.close()
