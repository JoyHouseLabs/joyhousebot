from pathlib import Path

import pytest

from joyhousebot.agent.loop import AgentLoop
from joyhousebot.bus.queue import MessageBus
from joyhousebot.providers.base import LLMProvider, LLMResponse


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


@pytest.mark.asyncio
async def test_agent_loop_uses_model_fallback_when_primary_errors(tmp_path: Path) -> None:
    provider = _FakeFallbackProvider()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="openai/gpt-primary",
        model_fallbacks=["anthropic/claude-fallback"],
        max_iterations=2,
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
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="openai/gpt-primary",
        model_fallbacks=["anthropic/claude-fallback"],
        max_iterations=2,
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

