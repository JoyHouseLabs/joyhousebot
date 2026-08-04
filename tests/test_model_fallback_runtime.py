from pathlib import Path

import pytest

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.agent.tools.base import Tool
from joyhousebot.domain.agents import AgentRevision
from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore


class _FakeFallbackProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="x")
        self.calls: list[str] = []

    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens=4096,
        temperature=0.7,
    ) -> LLMResponse:
        current = str(model or "unknown")
        self.calls.append(current)
        if current == "openai/gpt-primary":
            return LLMResponse(content="quota limited", finish_reason="error")
        return LLMResponse(content=f"ok:{current}", finish_reason="stop")

    def get_default_model(self) -> str:
        return "openai/gpt-primary"


class _EchoTool(Tool):
    name = "echo_test"
    description = "echo"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    async def execute(self, value: str, **_kwargs) -> str:
        return value


class _TwoTurnStreamingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="x")
        self.stream_calls = 0

    async def chat(self, **_kwargs) -> LLMResponse:
        raise AssertionError("every agent turn should use chat_stream")

    async def chat_stream(self, **_kwargs):
        self.stream_calls += 1
        if self.stream_calls == 1:
            yield (
                "done",
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest(id="call-1", name="echo_test", arguments={"value": "ok"})
                    ],
                    finish_reason="tool_calls",
                ),
            )
        else:
            yield "delta", "final"
            yield "done", LLMResponse(content="final", finish_reason="stop")

    def get_default_model(self) -> str:
        return "test/model"


@pytest.mark.asyncio
async def test_agent_loop_uses_model_fallback_when_primary_errors(tmp_path: Path) -> None:
    provider = _FakeFallbackProvider()
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="openai/gpt-primary",
        model_fallbacks=["anthropic/claude-fallback"],
        max_iterations=2,
        session_manager=RuntimeSessionManager(PostgresTestStore(tmp_path / "runtime.db")),
    )
    try:
        text = await loop.process_direct("hello", session_key="t:fallback")
        assert text == "ok:anthropic/claude-fallback"
        assert provider.calls[:2] == ["openai/gpt-primary", "anthropic/claude-fallback"]
    finally:
        await loop.close_mcp()


@pytest.mark.asyncio
async def test_agent_loop_skips_primary_while_in_cooldown(tmp_path: Path) -> None:
    provider = _FakeFallbackProvider()
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="openai/gpt-primary",
        model_fallbacks=["anthropic/claude-fallback"],
        max_iterations=2,
        session_manager=RuntimeSessionManager(PostgresTestStore(tmp_path / "runtime.db")),
    )
    try:
        first = await loop.process_direct("hello-1", session_key="t:cooldown")
        assert first == "ok:anthropic/claude-fallback"
        # Primary failed once and is now in cooldown. Next call should go directly to fallback.
        second = await loop.process_direct("hello-2", session_key="t:cooldown")
        assert second == "ok:anthropic/claude-fallback"
        assert provider.calls == [
            "openai/gpt-primary",
            "anthropic/claude-fallback",
            "anthropic/claude-fallback",
        ]
    finally:
        await loop.close_mcp()


@pytest.mark.asyncio
async def test_exact_model_cache_reuses_response_and_keeps_provider_out_of_hot_path(
    tmp_path: Path,
) -> None:
    provider = _FakeFallbackProvider()
    revision = AgentRevision(
        revision_id="cache-agent:v1",
        agent_id="cache-agent",
        version=1,
        model_policy={
            "primary": "openai/gpt-primary",
            "cache_enabled": True,
            "cache_ttl_seconds": 60,
        },
        status="published",
    )
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        agent_revision=revision,
        model="openai/gpt-primary",
        model_fallbacks=["anthropic/claude-fallback"],
        max_iterations=2,
        session_manager=RuntimeSessionManager(PostgresTestStore(tmp_path / "cache-runtime.db")),
    )
    try:
        first = await loop.process_direct("same", session_key="cache:first")
        second = await loop.process_direct("same", session_key="cache:second")
        assert first == second == "ok:anthropic/claude-fallback"
        assert provider.calls == ["openai/gpt-primary", "anthropic/claude-fallback"]
    finally:
        await loop.close_mcp()


@pytest.mark.asyncio
async def test_agent_loop_streams_every_turn_and_correlates_tool_events(tmp_path: Path) -> None:
    provider = _TwoTurnStreamingProvider()
    loop = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/model",
        max_iterations=3,
        session_manager=RuntimeSessionManager(PostgresTestStore(tmp_path / "runtime.db")),
    )
    loop.capabilities.register_tool(_EchoTool())
    events: list[tuple[str, dict]] = []

    async def capture(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    try:
        text = await loop.process_direct(
            "hello",
            session_key="t:stream-all-turns",
            execution_stream_callback=capture,
        )
        assert text == "final"
        assert provider.stream_calls == 2
        assert len([item for item in events if item[0] == "model_request_start"]) == 2
        tool_start = next(payload for kind, payload in events if kind == "tool_start")
        tool_end = next(payload for kind, payload in events if kind == "tool_end")
        assert tool_start["tool_call_id"] == tool_end["tool_call_id"] == "call-1"
        assert tool_start["turn_id"] == tool_end["turn_id"]
        assert tool_end["ok"] is True
    finally:
        await loop.close_mcp()
