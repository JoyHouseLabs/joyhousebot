import asyncio

import httpx
import pytest
from joyhousebot_provider_anthropic import AnthropicProvider
from joyhousebot_provider_openai_compatible import OpenAICompatibleProvider

from joyhousebot.config.schema import Config, ExtensionsConfig, ProviderConfig
from joyhousebot.providers.factory import create_model_provider
from joyhousebot.providers.observability import bind_model_observation
from joyhousebot.providers.provider_support import (
    error_metadata,
    sanitize_tools,
    user_friendly_error,
)
from joyhousebot.runtime.context import CancellationToken, RunContext, bind_run_context
from tests.support.postgres_store import PostgresTestStore


class _TestError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def test_provider_factory_rejects_non_ascii_api_key() -> None:
    config = Config(extensions=ExtensionsConfig(enabled=["provider-anthropic"]))
    config.providers.settings["anthropic"] = ProviderConfig(api_key="你的密钥")
    with pytest.raises(RuntimeError, match="ASCII"):
        create_model_provider(config=config, model="anthropic/claude-test")


def test_openrouter_keeps_vendor_qualified_model_name() -> None:
    config = Config(
        extensions=ExtensionsConfig(enabled=["provider-openai-compatible"])
    )
    config.providers.default_provider = "openrouter"
    config.providers.settings["openrouter"] = ProviderConfig(api_key="gateway-key")
    provider = create_model_provider(
        config=config,
        model="anthropic/claude-opus-4.5",
    )
    try:
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.provider_name == "openrouter"
        assert provider._model(None) == "anthropic/claude-opus-4.5"
    finally:
        asyncio.run(provider.close())


def test_openrouter_strips_gateway_prefix_and_sends_reasoning_policy() -> None:
    provider = OpenAICompatibleProvider(
        api_key="gateway-key",
        api_base="https://openrouter.ai/api/v1",
        default_model="openrouter/deepseek/deepseek-v4-flash",
        provider_name="openrouter",
        reasoning_options={"reasoning_effort": "none"},
    )
    try:
        payload, _ = provider._payload(
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            model=None,
            max_tokens=256,
            temperature=0.3,
            stream=True,
        )
        assert payload["model"] == "deepseek/deepseek-v4-flash"
        assert payload["reasoning"] == {"effort": "none"}
    finally:
        asyncio.run(provider.close())


def test_error_metadata_classifies_rate_limit_and_billing() -> None:
    rate_limit = error_metadata(_TestError("429 too many requests"))
    assert rate_limit["error_kind"] == "rate_limit"
    assert rate_limit["retryable"] is True
    assert rate_limit["error_status"] == 429

    billing = error_metadata(_TestError("insufficient credits"))
    assert billing["error_kind"] == "billing"
    assert billing["retryable"] is False


@pytest.mark.asyncio
async def test_openai_compatible_provider_returns_structured_error() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            401,
            json={"error": {"message": "unauthorized", "code": "invalid_api_key"}},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            api_key="x",
            api_base="https://models.example/v1",
            default_model="openai/gpt-test",
            provider_name="openai",
            client=client,
        )
        response = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="openai/gpt-test",
        )
    assert response.finish_reason == "error"
    assert response.error_kind == "auth"
    assert response.error_status == 401
    assert response.retryable is False
    assert response.error_code == "invalid_api_key"


@pytest.mark.asyncio
async def test_openai_compatible_parses_tool_calls_and_usage() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "gpt-test"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "plugin_invoke",
                                        "arguments": '{"value": 1}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider(
            api_key="x",
            api_base="https://models.example/v1",
            default_model="openai/gpt-test",
            provider_name="openai",
            client=client,
        )
        response = await provider.chat(
            messages=[{"role": "user", "content": "run"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "plugin.invoke",
                        "description": "invoke",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    assert response.tool_calls[0].name == "plugin.invoke"
    assert response.tool_calls[0].arguments == {"value": 1}
    assert response.usage == {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}


@pytest.mark.asyncio
async def test_provider_persists_full_request_response_and_native_reasoning(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "provider-observability.db")
    store.create_runtime_run(
        run_id="run-provider-trace",
        user_id="user-a",
        session_id="session-a",
        agent_id="default",
        kind="agent",
        prompt="explain",
        options={},
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-request-1"},
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": "answer",
                            "reasoning_content": "provider-native-analysis",
                            "reasoning_details": [
                                {"type": "reasoning.encrypted", "data": "opaque-signed-block"}
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        )

    context = RunContext(
        run_id="run-provider-trace",
        user_id="user-a",
        agent_id="default",
        session_id="session-a",
        session_key="user-a:default:session-a",
        channel="api",
        chat_id="runtime",
        request_id="req-provider",
        tracker_id="trace-provider",
        trace_store=store,
        cancellation=CancellationToken(),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider(
            api_key="secret-not-persisted-as-header",
            api_base="https://models.example/v1",
            default_model="openai/gpt-test",
            provider_name="openai",
            client=client,
        )
        with (
            bind_run_context(context),
            bind_model_observation(turn_id="turn-provider", attempt=1, provider="openai"),
        ):
            response = await provider.chat(messages=[{"role": "user", "content": "explain"}])

    assert response.reasoning_content == "provider-native-analysis"
    assert response.reasoning_blocks == [
        {"type": "reasoning.encrypted", "data": "opaque-signed-block"}
    ]
    invocation = store.list_model_invocations(context.run_id)[0]
    assert invocation.provider_request_id == "provider-request-1"
    assert invocation.turn_id == "turn-provider"
    assert invocation.reasoning_availability == "provider_native"
    assert (
        store.get_trace_blob(invocation.request_blob_id).content["messages"][0]["content"]
        == "explain"
    )
    segments = store.list_reasoning_segments(context.run_id)
    assert segments[0].content == "provider-native-analysis"
    assert segments[1].kind == "provider_block"
    assert segments[1].content_format == "application/json"
    assert "opaque-signed-block" in segments[1].content


@pytest.mark.asyncio
async def test_provider_persists_full_error_response_and_request_id(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "provider-error-observability.db")
    store.create_runtime_run(
        run_id="run-provider-error",
        user_id="user-a",
        session_id="session-a",
        agent_id="default",
        kind="agent",
        prompt="fail with diagnostics",
        options={},
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-request-id": "provider-error-request-1"},
            json={
                "error": {
                    "code": "rate_limit",
                    "message": "try later",
                    "provider_debug": {"bucket": "requests-per-minute"},
                }
            },
        )

    context = RunContext(
        run_id="run-provider-error",
        user_id="user-a",
        agent_id="default",
        session_id="session-a",
        session_key="user-a:default:session-a",
        channel="api",
        chat_id="runtime",
        request_id="req-provider-error",
        tracker_id="trace-provider-error",
        trace_store=store,
        cancellation=CancellationToken(),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OpenAICompatibleProvider(
            api_key="secret-not-persisted-as-header",
            api_base="https://models.example/v1",
            default_model="openai/gpt-test",
            provider_name="openai",
            client=client,
        )
        with (
            bind_run_context(context),
            bind_model_observation(turn_id="turn-provider-error", attempt=1, provider="openai"),
        ):
            response = await provider.chat(messages=[{"role": "user", "content": "fail"}])

    assert response.finish_reason == "error"
    invocation = store.list_model_invocations(context.run_id)[0]
    assert invocation.status == "failed"
    assert invocation.provider_request_id == "provider-error-request-1"
    error_blob = store.get_trace_blob(invocation.response_blob_id)
    assert error_blob.kind == "model.error"
    assert error_blob.content["error"]["provider_debug"]["bucket"] == ("requests-per-minute")


@pytest.mark.asyncio
async def test_openai_compatible_stream_merges_tool_argument_fragments() -> None:
    events = [
        {
            "choices": [
                {
                    "delta": {
                        "content": "working ",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {
                                    "name": "plugin_invoke",
                                    "arguments": '{"query":',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"pg"}'}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {
            "choices": [],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        },
    ]
    body = "".join(f"data: {__import__('json').dumps(event)}\n\n" for event in events)
    body += "data: [DONE]\n\n"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=body))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            api_key="x",
            api_base="https://models.example/v1",
            default_model="openai/gpt-test",
            provider_name="openai",
            client=client,
        )
        chunks = [
            item
            async for item in provider.chat_stream(
                messages=[{"role": "user", "content": "run"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "plugin.invoke",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )
        ]
    assert chunks[0] == ("delta", "working ")
    final = chunks[-1][1]
    assert final.finish_reason == "tool_calls"
    assert final.tool_calls[0].name == "plugin.invoke"
    assert final.tool_calls[0].arguments == {"query": "pg"}
    assert final.usage["total_tokens"] == 6


def test_sanitize_tools_replaces_dotted_names() -> None:
    tools = [
        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
        {"type": "function", "function": {"name": "plugin.invoke", "parameters": {}}},
    ]
    sanitized, aliases = sanitize_tools(tools)
    assert sanitized is not None
    assert [item["function"]["name"] for item in sanitized] == [
        "read_file",
        "plugin_invoke",
    ]
    assert aliases == {"plugin_invoke": "plugin.invoke"}


def test_user_friendly_error_never_leaks_credentials() -> None:
    message = user_friendly_error(
        _TestError("401 unauthorized api_key=sk-secret at https://internal.example/v1"),
        model="openai/gpt-test",
    )
    assert "sk-secret" not in message
    assert "internal.example" not in message
    assert "authentication failed" in message


@pytest.mark.asyncio
async def test_anthropic_provider_converts_system_tools_and_usage() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "claude-test"
        assert payload["system"] == "Platform policy"
        assert payload["tools"][0]["name"] == "plugin_invoke"
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "plugin_invoke",
                        "input": {"query": "postgres"},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 8, "output_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = AnthropicProvider(
            api_key="x",
            api_base="https://api.anthropic.example/v1",
            default_model="anthropic/claude-test",
            client=client,
        )
        response = await provider.chat(
            messages=[
                {"role": "system", "content": "Platform policy"},
                {"role": "user", "content": "search"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "plugin.invoke",
                        "description": "invoke",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "plugin.invoke"
    assert response.tool_calls[0].arguments == {"query": "postgres"}
    assert response.usage == {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}
