import pytest

import joyhousebot.providers.litellm_provider as lp
from joyhousebot.providers.litellm_provider import (
    LiteLLMProvider,
    _build_error_meta,
    _sanitize_tools_for_provider,
)


class _Err(Exception):
    def __init__(self, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.status_code = status_code


def test_build_error_meta_classifies_rate_limit_and_billing() -> None:
    meta = _build_error_meta(_Err("429 too many requests"))
    assert meta["error_kind"] == "rate_limit"
    assert meta["retryable"] is True
    assert meta["error_status"] == 429

    meta = _build_error_meta(_Err("insufficient credits"))
    assert meta["error_kind"] == "billing"
    assert meta["retryable"] is False


@pytest.mark.asyncio
async def test_chat_returns_structured_error_metadata(monkeypatch) -> None:
    async def _raise(**kwargs):
        raise _Err("401 unauthorized", status_code=401)

    monkeypatch.setattr(lp, "acompletion", _raise)
    provider = LiteLLMProvider(api_key="x", default_model="openai/gpt-4o-mini")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}], model="openai/gpt-4o-mini")
    assert resp.finish_reason == "error"
    assert resp.error_kind == "auth"
    assert resp.error_status == 401
    assert resp.retryable is False
    assert resp.error_code


def test_sanitize_tools_replaces_dotted_names_for_deepseek() -> None:
    """Tools with dots (e.g. plugin.invoke, mcp_x_tool.name) must be sanitized for DeepSeek."""
    import re

    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "x", "parameters": {}}},
        {"type": "function", "function": {"name": "plugin.invoke", "description": "y", "parameters": {}}},
        {"type": "function", "function": {"name": "mcp_server_files.read", "description": "z", "parameters": {}}},
    ]
    sanitized, alias_to_original = _sanitize_tools_for_provider(tools)
    names = [t["function"]["name"] for t in sanitized]
    assert "read_file" in names  # already valid
    assert "plugin.invoke" not in names
    assert "mcp_server_files.read" not in names
    assert alias_to_original  # we created at least one alias
    safe_pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    for n in names:
        assert safe_pattern.match(n), f"Tool name must match ^[a-zA-Z0-9_-]+$: {n!r}"

