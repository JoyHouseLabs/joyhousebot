"""Optional Anthropic Messages API model provider extension."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import json_repair

from joyhousebot.extension_sdk import ExtensionManifest
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.models import (
    LLMProvider,
    LLMResponse,
    ModelProviderBuildRequest,
    ModelProviderExtension,
    ModelProviderSpec,
    ProviderHTTPError,
    ToolCallRequest,
    error_metadata,
    missing_usage,
    model_first_token,
    model_request_failed,
    model_request_finished,
    model_request_started,
    normalized_usage,
    partial_usage,
    restore_tool_name,
    sanitize_tools,
    user_friendly_error,
)

ANTHROPIC_EXTENSION_MANIFEST = ExtensionManifest(
    extension_id="provider-anthropic",
    version="0.1.1",
    name="joyhousebot Anthropic Provider",
    extension_types=("model_provider",),
    description="Anthropic Messages API, streaming, tools and native reasoning adapter.",
    distribution_name="joyhousebot-provider-anthropic",
    build_digest=source_tree_digest(__file__),
    required_permissions=("model.invoke",),
    dependencies=(
        {"id": "anthropic-api", "kind": "service", "required": True},
        {"id": "anthropic-api-key", "kind": "credential", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "required": ["api_key"],
        "properties": {
            "api_key": {"type": "string", "writeOnly": True},
            "api_base": {"type": "string"},
            "extra_headers": {"type": "object"},
        },
    },
)

ANTHROPIC_PROVIDER_SPEC = ModelProviderSpec(
    name="anthropic",
    keywords=("anthropic", "claude"),
    default_api_base="https://api.anthropic.com/v1",
    env_key="ANTHROPIC_API_KEY",
)

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


@dataclass(slots=True)
class _AnthropicStreamState:
    text_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    reasoning_blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    tool_blocks: dict[int, dict[str, Any]] = field(default_factory=dict)
    raw_usage: dict[str, int] = field(default_factory=dict)
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    first_token_seen: bool = False


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        default_model: str,
        extra_headers: dict[str, str] | None = None,
        reasoning_options: dict[str, Any] | None = None,
        usage_pricing: dict[str, Any] | None = None,
        request_timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = dict(extra_headers or {})
        self.reasoning_options = dict(reasoning_options or {})
        self.usage_pricing = dict(usage_pricing or {})
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(request_timeout_seconds), connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def get_default_model(self) -> str:
        return self.default_model

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._client

    def _model(self, model: str | None) -> str:
        return str(model or self.default_model).removeprefix("anthropic/")

    def _url(self) -> str:
        return f"{str(self.api_base).rstrip('/')}/messages"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "joyhousebot-runtime",
            "anthropic-version": "2023-06-01",
            "x-api-key": str(self.api_key or ""),
        }
        headers.update(self.extra_headers)
        return headers

    def _payload(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        stream: bool,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        provider_tools, aliases = sanitize_tools(tools)
        converted, system = self._convert_messages(messages, aliases)
        payload: dict[str, Any] = {
            "model": self._model(model),
            "messages": converted,
            "max_tokens": max(1, int(max_tokens)),
            "temperature": temperature,
            "stream": stream,
        }
        thinking_budget = int(self.reasoning_options.get("thinking_budget_tokens") or 0)
        if thinking_budget > 0:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(thinking_budget, max(1, int(max_tokens) - 1)),
            }
            payload["temperature"] = 1.0
        if system:
            payload["system"] = system
        if provider_tools:
            payload["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description") or "",
                    "input_schema": item["function"].get("parameters") or {"type": "object"},
                }
                for item in provider_tools
            ]
        return payload, aliases

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        payload, aliases = self._payload(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )
        resolved_model = str(payload["model"])
        request_id = await model_request_started(
            model=resolved_model,
            operation="messages.create",
            message_count=len(messages),
            tool_count=len(tools or []),
            provider="anthropic",
            request_payload=payload,
            request_url=self._url(),
        )
        try:
            response = await self._client.post(self._url(), headers=self._headers(), json=payload)
            await self._raise_for_status(response)
            raw_response = response.json()
            parsed = self._parse_response(raw_response, aliases)
            await model_request_finished(
                request_id=request_id,
                model=resolved_model,
                operation="messages.create",
                status=parsed.finish_reason,
                usage=parsed.usage,
                has_tool_calls=bool(parsed.tool_calls),
                provider_request_id=response.headers.get("request-id")
                or response.headers.get("x-request-id"),
                response_payload=raw_response,
                reasoning_content=parsed.reasoning_content,
                reasoning_blocks=parsed.reasoning_blocks,
                provider_block_type="thinking",
            )
            return parsed
        except Exception as exc:
            failed_usage = self._error_usage(exc)
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="messages.create",
                exc=exc,
                provider_request_id=getattr(exc, "provider_request_id", None),
                response_payload=getattr(exc, "raw_response", None),
                usage=failed_usage,
            )
            return LLMResponse(
                content=user_friendly_error(exc, model=self._model(model)),
                finish_reason="error",
                usage=failed_usage,
                **error_metadata(exc),
            )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncGenerator[tuple[str, str | LLMResponse], None]:
        payload, aliases = self._payload(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        resolved_model = str(payload["model"])
        request_id = await model_request_started(
            model=resolved_model,
            operation="messages.stream",
            message_count=len(messages),
            tool_count=len(tools or []),
            provider="anthropic",
            request_payload=payload,
            request_url=self._url(),
        )
        state = _AnthropicStreamState()
        try:
            async with self._client.stream(
                "POST", self._url(), headers=self._headers(), json=payload
            ) as response:
                await self._raise_for_status(response)
                async for line in response.aiter_lines():
                    event = self._stream_event(line, state)
                    if event is None:
                        continue
                    emitted = await self._apply_stream_event(event, state, request_id)
                    if emitted is not None:
                        yield emitted
            usage = self._usage(state.raw_usage)
            final = LLMResponse(
                content="".join(state.text_parts) or None,
                tool_calls=self._stream_tool_calls(state.tool_blocks, aliases),
                finish_reason=_STOP_REASONS.get(state.stop_reason, state.stop_reason),
                usage=usage,
                reasoning_content="".join(state.reasoning_parts) or None,
                reasoning_blocks=[
                    state.reasoning_blocks[index]
                    for index in sorted(state.reasoning_blocks)
                ],
            )
            await model_request_finished(
                request_id=request_id,
                model=resolved_model,
                operation="messages.stream",
                status=final.finish_reason,
                usage=final.usage,
                has_tool_calls=bool(final.tool_calls),
                provider_request_id=response.headers.get("request-id")
                or response.headers.get("x-request-id"),
                response_payload={"stream_events": state.raw_events},
                reasoning_content=final.reasoning_content,
                reasoning_blocks=final.reasoning_blocks,
                provider_block_type="thinking_delta",
            )
            yield "done", final
        except Exception as exc:
            failed_usage = partial_usage(self._usage(state.raw_usage))
            if failed_usage.get("usage_status") == "missing":
                failed_usage = self._error_usage(exc)
            raw_error = getattr(exc, "raw_response", None)
            failure_payload: Any = {"stream_events": state.raw_events}
            if raw_error is not None:
                failure_payload["provider_error"] = raw_error
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="messages.stream",
                exc=exc,
                provider_request_id=getattr(exc, "provider_request_id", None),
                response_payload=failure_payload,
                usage=failed_usage,
            )
            yield (
                "done",
                LLMResponse(
                    content=user_friendly_error(exc, model=self._model(model)),
                    finish_reason="error",
                    usage=failed_usage,
                    **error_metadata(exc),
                ),
            )

    @staticmethod
    def _stream_event(line: str, state: _AnthropicStreamState) -> dict[str, Any] | None:
        if not line.startswith("data:"):
            return None
        raw = line[5:].strip()
        if not raw:
            return None
        event = json.loads(raw)
        state.raw_events.append(event)
        return event

    async def _apply_stream_event(
        self,
        event: dict[str, Any],
        state: _AnthropicStreamState,
        request_id: str,
    ) -> tuple[str, str] | None:
        event_type = event.get("type")
        if event_type == "message_start":
            self._merge_raw_usage(
                state.raw_usage, (event.get("message") or {}).get("usage")
            )
            return None
        if event_type == "content_block_start":
            self._start_stream_block(event, state)
            return None
        if event_type == "content_block_delta":
            return await self._apply_stream_delta(event, state, request_id)
        if event_type == "message_delta":
            state.stop_reason = str(
                (event.get("delta") or {}).get("stop_reason") or state.stop_reason
            )
            self._merge_raw_usage(state.raw_usage, event.get("usage"))
        return None

    @staticmethod
    def _start_stream_block(event: dict[str, Any], state: _AnthropicStreamState) -> None:
        block = event.get("content_block") or {}
        index = int(event.get("index") or 0)
        if block.get("type") == "tool_use":
            state.tool_blocks[index] = {
                "id": str(block.get("id") or ""),
                "name": str(block.get("name") or ""),
                "arguments": json.dumps(block.get("input") or {}),
            }
        elif block.get("type") in {"thinking", "redacted_thinking"}:
            state.reasoning_blocks[index] = dict(block)

    async def _apply_stream_delta(
        self,
        event: dict[str, Any],
        state: _AnthropicStreamState,
        request_id: str,
    ) -> tuple[str, str] | None:
        delta = event.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = str(delta.get("text") or "")
            if not text:
                return None
            await self._mark_first_stream_token(state, request_id)
            state.text_parts.append(text)
            return "delta", text
        if delta_type == "thinking_delta":
            thinking = str(delta.get("thinking") or "")
            if thinking:
                await self._mark_first_stream_token(state, request_id)
            state.reasoning_parts.append(thinking)
            block = self._reasoning_stream_block(event, state)
            block["thinking"] = str(block.get("thinking") or "") + thinking
            return ("reasoning_delta", thinking) if thinking else None
        if delta_type == "signature_delta":
            block = self._reasoning_stream_block(event, state)
            block["signature"] = str(block.get("signature") or "") + str(
                delta.get("signature") or ""
            )
        elif delta_type == "input_json_delta":
            self._append_tool_arguments(event, state)
        return None

    @staticmethod
    def _reasoning_stream_block(
        event: dict[str, Any], state: _AnthropicStreamState
    ) -> dict[str, Any]:
        return state.reasoning_blocks.setdefault(
            int(event.get("index") or 0),
            {"type": "thinking", "thinking": "", "signature": ""},
        )

    @staticmethod
    def _append_tool_arguments(
        event: dict[str, Any], state: _AnthropicStreamState
    ) -> None:
        delta = event.get("delta") or {}
        block = state.tool_blocks.setdefault(
            int(event.get("index") or 0), {"id": "", "name": "", "arguments": ""}
        )
        existing = str(block["arguments"])
        block["arguments"] = ("" if existing == "{}" else existing) + str(
            delta.get("partial_json") or ""
        )

    @staticmethod
    async def _mark_first_stream_token(
        state: _AnthropicStreamState, request_id: str
    ) -> None:
        if state.first_token_seen:
            return
        state.first_token_seen = True
        await model_first_token(request_id)

    @staticmethod
    def _convert_messages(
        messages: list[dict[str, Any]], aliases: dict[str, str]
    ) -> tuple[list[dict[str, Any]], str]:
        original_to_alias = {original: alias for alias, original in aliases.items()}
        system_parts: list[str] = []
        result: list[dict[str, Any]] = []
        for raw in messages:
            role = str(raw.get("role") or "user")
            content = raw.get("content")
            if role == "system":
                system_parts.append(str(content or ""))
                continue
            if role == "tool":
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(raw.get("tool_call_id") or ""),
                                "content": str(content or ""),
                            }
                        ],
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            if role == "assistant":
                blocks.extend(
                    dict(item)
                    for item in raw.get("reasoning_blocks") or []
                    if isinstance(item, dict)
                    and item.get("type") in {"thinking", "redacted_thinking"}
                )
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                blocks.extend(item for item in content if isinstance(item, dict))
            for call in raw.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = json_repair.loads(arguments)
                name = str(function.get("name") or "")
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": original_to_alias.get(name, name),
                        "input": arguments if isinstance(arguments, dict) else {},
                    }
                )
            result.append(
                {
                    "role": "assistant" if role == "assistant" else "user",
                    "content": blocks or [{"type": "text", "text": ""}],
                }
            )
        return result, "\n\n".join(part for part in system_parts if part)

    def _parse_response(self, value: dict[str, Any], aliases: dict[str, str]) -> LLMResponse:
        text_parts = []
        reasoning_parts = []
        reasoning_blocks = []
        calls = []
        for block in value.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "thinking":
                reasoning_parts.append(str(block.get("thinking") or ""))
                reasoning_blocks.append(dict(block))
            elif block.get("type") == "redacted_thinking":
                reasoning_blocks.append(dict(block))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCallRequest(
                        id=str(block.get("id") or ""),
                        name=restore_tool_name(str(block.get("name") or ""), aliases),
                        arguments=dict(block.get("input") or {}),
                    )
                )
        usage = self._usage(value.get("usage"))
        stop_reason = str(value.get("stop_reason") or "end_turn")
        return LLMResponse(
            content="".join(text_parts) or None,
            tool_calls=calls,
            finish_reason=_STOP_REASONS.get(stop_reason, stop_reason),
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
            reasoning_blocks=reasoning_blocks,
        )

    @staticmethod
    def _stream_tool_calls(
        values: dict[int, dict[str, Any]], aliases: dict[str, str]
    ) -> list[ToolCallRequest]:
        result = []
        for index in sorted(values):
            value = values[index]
            arguments = json_repair.loads(str(value.get("arguments") or "{}"))
            result.append(
                ToolCallRequest(
                    id=str(value.get("id") or ""),
                    name=restore_tool_name(str(value.get("name") or ""), aliases),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return result

    @staticmethod
    def _merge_raw_usage(target: dict[str, int], value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            if key in value:
                target[key] = max(int(target.get(key) or 0), int(value.get(key) or 0))

    def _usage(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return missing_usage()
        uncached = int(value.get("input_tokens") or 0)
        cache_creation = int(value.get("cache_creation_input_tokens") or 0)
        cache_read = int(value.get("cache_read_input_tokens") or 0)
        output = int(value.get("output_tokens") or 0)
        has_counts = any(
            key in value
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        return normalized_usage(
            input_tokens=uncached + cache_creation + cache_read,
            output_tokens=output,
            billed_input_tokens=uncached + cache_creation + cache_read,
            billed_output_tokens=output,
            cached_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            usage_source="provider" if has_counts else "missing",
            usage_status="exact" if has_counts else "missing",
            provider_cost_usd=value.get(
                "cost", value.get("cost_usd", value.get("total_cost"))
            ),
            pricing=self.usage_pricing,
        )

    def _error_usage(self, exc: Exception) -> dict[str, Any]:
        raw = getattr(exc, "raw_response", None)
        value = raw.get("usage") if isinstance(raw, dict) else None
        return partial_usage(self._usage(value)) if isinstance(value, dict) else missing_usage()

    @staticmethod
    async def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        body = await response.aread()
        text = body.decode(errors="replace")
        raw_response: Any = text
        try:
            value = json.loads(text)
            raw_response = value
            error = value.get("error") or value
            message = str(error.get("message") or text)[:1200]
            code = str(error.get("type") or "") or None
        except (json.JSONDecodeError, AttributeError):
            message, code = text[:1200], None
        raise ProviderHTTPError(
            response.status_code,
            message,
            code=code,
            raw_response=raw_response,
            provider_request_id=response.headers.get("request-id")
            or response.headers.get("x-request-id"),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _create_provider(request: ModelProviderBuildRequest) -> AnthropicProvider:
    return AnthropicProvider(
        api_key=request.api_key,
        api_base=request.api_base,
        default_model=request.default_model,
        extra_headers=request.extra_headers,
        reasoning_options=request.reasoning_options,
        usage_pricing=request.usage_pricing,
        request_timeout_seconds=request.request_timeout_seconds,
        client=request.client,
    )


ANTHROPIC_PROVIDER_EXTENSION = ModelProviderExtension(
    manifest=ANTHROPIC_EXTENSION_MANIFEST,
    providers=(ANTHROPIC_PROVIDER_SPEC,),
    factory=_create_provider,
)


def create_extension() -> ModelProviderExtension:
    return ANTHROPIC_PROVIDER_EXTENSION
