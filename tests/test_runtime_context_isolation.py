import asyncio
from pathlib import Path

import pytest

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.bus.events import OutboundMessage
from joyhousebot.config.schema import Config
from joyhousebot.contracts.tools import Tool
from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.runtime.context import (
    CancellationToken,
    RunContext,
    bind_run_context,
    get_current_run_context,
)
from joyhousebot.session.models import Session
from tests.support.capabilities import register_tool_fixture
from tests.support.postgres_store import PostgresTestStore


class _MemorySessions:
    def __init__(self, store) -> None:
        self.store = store
        self.items: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        return self.items.setdefault(key, Session(key=key))

    def save(self, session: Session) -> None:
        self.items[session.key] = session

    def invalidate(self, key: str) -> None:
        self.items.pop(key, None)


class _ConcurrentMessageProvider(LLMProvider):
    """Force two runs to reach their tool calls at the same time."""

    def __init__(self) -> None:
        super().__init__(api_key="test")
        self._arrived = 0
        self._both_arrived = asyncio.Event()

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        if any(message.get("role") == "tool" for message in messages):
            return LLMResponse(content="done")

        prompt = str(messages[-1].get("content") or "")
        self._arrived += 1
        if self._arrived == 2:
            self._both_arrived.set()
        await asyncio.wait_for(self._both_arrived.wait(), timeout=2)
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{prompt}",
                    name="routing_probe",
                    arguments={"content": f"out:{prompt}"},
                )
            ],
        )


class _RoutingProbeTool(Tool):
    """Read-only test probe for ContextVar routing, with no business write."""

    name = "routing_probe"
    description = "Record the current execution route"
    parameters = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    }

    def __init__(self, sink) -> None:
        self._sink = sink

    async def execute(self, content: str, **kwargs) -> str:
        context = kwargs["tool_context"]
        await self._sink(
            OutboundMessage(
                channel=context.channel,
                chat_id=context.chat_id,
                content=content,
            )
        )
        return "recorded"


class _ConcurrencyProbeProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.active = 0
        self.max_active = 0

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.03)
        self.active -= 1
        return LLMResponse(content="done")


class _InstructionCaptureProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.messages = []

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        self.messages = messages
        return LLMResponse(content='{"answer": 42}')


def test_run_context_binding_is_task_local() -> None:
    context = RunContext(
        run_id="run-1",
        session_key="web:user-1",
        channel="web",
        chat_id="user-1",
    )
    assert get_current_run_context() is None
    with bind_run_context(context):
        assert get_current_run_context() is context
    assert get_current_run_context() is None


@pytest.mark.asyncio
async def test_cancellation_token_propagates_reason() -> None:
    token = CancellationToken()
    waiter = asyncio.create_task(token.wait())
    token.cancel("user requested")
    assert await waiter == "user requested"
    assert token.is_cancelled
    assert token.reason == "user requested"


@pytest.mark.asyncio
async def test_concurrent_sessions_keep_tool_routing_isolated(tmp_path: Path) -> None:
    outbound = []

    async def sink(message):
        outbound.append(message)

    loop = NativeAgentExecutor(
        provider=_ConcurrentMessageProvider(),
        scratch_root=tmp_path,
        session_manager=_MemorySessions(PostgresTestStore(tmp_path / "runtime.db")),
        max_iterations=2,
        outbound_sink=sink,
    )
    tool = _RoutingProbeTool(sink)
    register_tool_fixture(loop.capabilities, tool)

    await asyncio.gather(
        loop.process_direct("alpha", session_key="web:a", channel="web", chat_id="a"),
        loop.process_direct("beta", session_key="web:b", channel="web", chat_id="b"),
    )

    routed = {(message.chat_id, message.content) for message in outbound}
    assert routed == {("a", "out:alpha"), ("b", "out:beta")}


@pytest.mark.asyncio
async def test_same_session_runs_are_serialized(tmp_path: Path) -> None:
    provider = _ConcurrencyProbeProvider()
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        session_manager=_MemorySessions(PostgresTestStore(tmp_path / "runtime.db")),
        max_iterations=1,
    )

    await asyncio.gather(
        loop.process_direct("one", session_key="web:same", channel="web", chat_id="same"),
        loop.process_direct("two", session_key="web:same", channel="web", chat_id="same"),
    )

    assert provider.max_active == 1


@pytest.mark.asyncio
async def test_global_run_concurrency_limit_is_enforced(tmp_path: Path) -> None:
    provider = _ConcurrencyProbeProvider()
    config = Config()
    config.gateway.max_concurrent_sessions = 1
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        session_manager=_MemorySessions(PostgresTestStore(tmp_path / "runtime.db")),
        config=config,
        max_iterations=1,
    )

    await asyncio.gather(
        loop.process_direct("one", session_key="web:one", channel="web", chat_id="one"),
        loop.process_direct("two", session_key="web:two", channel="web", chat_id="two"),
    )

    assert provider.max_active == 1


@pytest.mark.asyncio
async def test_run_instructions_are_scoped_to_one_execution(tmp_path: Path) -> None:
    provider = _InstructionCaptureProvider()
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        session_manager=_MemorySessions(PostgresTestStore(tmp_path / "runtime.db")),
        max_iterations=1,
    )
    context = RunContext(
        run_id="structured",
        session_key="api:structured",
        channel="api",
        chat_id="structured",
        system_prompt="Use the risk policy.",
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
        },
    )

    await loop.process_direct(
        "analyze",
        session_key="api:structured",
        channel="api",
        chat_id="structured",
        run_context=context,
    )

    system_content = "\n".join(
        str(message.get("content") or "")
        for message in provider.messages
        if message.get("role") == "system"
    )
    assert "Use the risk policy." in system_content
    assert "Return only JSON matching this JSON Schema" in system_content
