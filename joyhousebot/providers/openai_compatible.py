"""Native OpenAI-compatible chat-completions provider."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import json_repair

from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.providers.observability import (
    model_first_token,
    model_request_failed,
    model_request_finished,
    model_request_started,
)
from joyhousebot.providers.provider_support import (
    ProviderHTTPError,
    error_metadata,
    restore_tool_name,
    sanitize_messages,
    sanitize_tools,
    user_friendly_error,
)


class OpenAICompatibleProvider(LLMProvider):
    """Direct HTTP adapter for OpenAI-compatible `/chat/completions` APIs."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base: str,
        default_model: str,
        provider_name: str,
        extra_headers: dict[str, str] | None = None,
        reasoning_options: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.provider_name = provider_name
        self.extra_headers = dict(extra_headers or {})
        self.reasoning_options = dict(reasoning_options or {})
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def get_default_model(self) -> str:
        return self.default_model

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._client

    def _model(self, model: str | None) -> str:
        value = str(model or self.default_model)
        prefix = f"{self.provider_name}/"
        if value.startswith(prefix):
            return value.removeprefix(prefix)
        if self.provider_name == "openrouter" and value.startswith("openrouter/"):
            return value.removeprefix("openrouter/")
        return value

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "joyhousebot-cloud"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def _url(self) -> str:
        return f"{str(self.api_base).rstrip('/')}/chat/completions"

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
        original_to_alias = {original: alias for alias, original in aliases.items()}
        resolved_model = self._model(model)
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": sanitize_messages(messages, original_to_alias=original_to_alias),
            "stream": stream,
        }
        reasoning_model = self.provider_name == "openai" and resolved_model.lower().startswith(
            ("gpt-5", "o1", "o3", "o4")
        )
        payload["max_completion_tokens" if reasoning_model else "max_tokens"] = max(
            1, int(max_tokens)
        )
        if not reasoning_model:
            payload["temperature"] = 1.0 if "kimi-k2.5" in resolved_model.lower() else temperature
        if provider_tools:
            payload["tools"] = provider_tools
            payload["tool_choice"] = "auto"
        reasoning_effort = str(self.reasoning_options.get("reasoning_effort") or "").strip()
        if reasoning_effort and self.provider_name == "openai":
            payload["reasoning_effort"] = reasoning_effort
        if self.provider_name == "openrouter" and reasoning_effort:
            # OpenRouter uses one vendor-neutral object for reasoning.  The
            # explicit `none` default keeps Flash turns on the low-latency
            # path; a revision can opt into a supported effort deliberately.
            payload["reasoning"] = {"effort": reasoning_effort}
        if stream:
            payload["stream_options"] = {"include_usage": True}
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
            operation="chat.completion",
            message_count=len(messages),
            tool_count=len(tools or []),
            provider=self.provider_name,
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
                operation="chat.completion",
                status=parsed.finish_reason,
                usage=parsed.usage,
                has_tool_calls=bool(parsed.tool_calls),
                provider_request_id=response.headers.get("x-request-id")
                or response.headers.get("request-id"),
                response_payload=raw_response,
                reasoning_content=parsed.reasoning_content,
                reasoning_blocks=parsed.reasoning_blocks,
                provider_block_type="reasoning_content",
            )
            return parsed
        except Exception as exc:
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="chat.completion",
                exc=exc,
                provider_request_id=getattr(exc, "provider_request_id", None),
                response_payload=getattr(exc, "raw_response", None),
            )
            return LLMResponse(
                content=user_friendly_error(exc, model=self._model(model)),
                finish_reason="error",
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
            operation="chat.completion.stream",
            message_count=len(messages),
            tool_count=len(tools or []),
            provider=self.provider_name,
            request_payload=payload,
            request_url=self._url(),
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}
        raw_events: list[dict[str, Any]] = []
        first_token_seen = False
        try:
            async with self._client.stream(
                "POST", self._url(), headers=self._headers(), json=payload
            ) as response:
                await self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    raw_events.append(event)
                    usage = self._usage(event.get("usage")) or usage
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        if not first_token_seen:
                            first_token_seen = True
                            await model_first_token(request_id)
                        content_parts.append(text)
                        yield "delta", text
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str):
                        if reasoning and not first_token_seen:
                            first_token_seen = True
                            await model_first_token(request_id)
                        reasoning_parts.append(reasoning)
                        if reasoning:
                            yield "reasoning_delta", reasoning
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index") or 0)
                        current = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        current["id"] += str(call.get("id") or "")
                        function = call.get("function") or {}
                        current["name"] += str(function.get("name") or "")
                        current["arguments"] += str(function.get("arguments") or "")
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
            final = LLMResponse(
                content="".join(content_parts) or None,
                tool_calls=self._parse_stream_calls(calls, aliases),
                finish_reason=finish_reason,
                usage=usage,
                reasoning_content="".join(reasoning_parts) or None,
            )
            await model_request_finished(
                request_id=request_id,
                model=resolved_model,
                operation="chat.completion.stream",
                status=final.finish_reason,
                usage=final.usage,
                has_tool_calls=bool(final.tool_calls),
                provider_request_id=response.headers.get("x-request-id")
                or response.headers.get("request-id"),
                response_payload={"stream_events": raw_events},
                reasoning_content=final.reasoning_content,
                reasoning_blocks=final.reasoning_blocks,
                provider_block_type="reasoning_content_delta",
            )
            yield "done", final
        except Exception as exc:
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="chat.completion.stream",
                exc=exc,
                provider_request_id=getattr(exc, "provider_request_id", None),
                response_payload=getattr(exc, "raw_response", None),
            )
            yield (
                "done",
                LLMResponse(
                    content=user_friendly_error(exc, model=self._model(model)),
                    finish_reason="error",
                    **error_metadata(exc),
                ),
            )

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
            code = str(error.get("code") or "") or None
        except (json.JSONDecodeError, AttributeError):
            message, code = text[:1200], None
        raise ProviderHTTPError(
            response.status_code,
            message,
            code=code,
            raw_response=raw_response,
            provider_request_id=response.headers.get("x-request-id")
            or response.headers.get("request-id"),
        )

    @classmethod
    def _parse_response(cls, value: dict[str, Any], aliases: dict[str, str]) -> LLMResponse:
        choices = value.get("choices") or []
        if not choices:
            raise ProviderHTTPError(502, "provider returned no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        calls: list[ToolCallRequest] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                arguments = json_repair.loads(arguments)
            calls.append(
                ToolCallRequest(
                    id=str(raw.get("id") or ""),
                    name=restore_tool_name(str(function.get("name") or ""), aliases),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        reasoning_blocks = [
            dict(item) for item in message.get("reasoning_details") or [] if isinstance(item, dict)
        ]
        return LLMResponse(
            content=message.get("content"),
            tool_calls=calls,
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=cls._usage(value.get("usage")),
            reasoning_content=message.get("reasoning_content") or message.get("reasoning"),
            reasoning_blocks=reasoning_blocks,
        )

    @staticmethod
    def _parse_stream_calls(
        values: dict[int, dict[str, str]], aliases: dict[str, str]
    ) -> list[ToolCallRequest]:
        result = []
        for index in sorted(values):
            value = values[index]
            arguments = json_repair.loads(value["arguments"] or "{}")
            result.append(
                ToolCallRequest(
                    id=value["id"],
                    name=restore_tool_name(value["name"], aliases),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return result

    @staticmethod
    def _usage(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            return {}
        input_tokens = int(value.get("prompt_tokens") or value.get("input_tokens") or 0)
        output_tokens = int(value.get("completion_tokens") or value.get("output_tokens") or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(value.get("total_tokens") or input_tokens + output_tokens),
        }

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
