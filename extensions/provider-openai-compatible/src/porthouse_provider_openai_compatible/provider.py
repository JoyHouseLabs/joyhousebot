"""Optional OpenAI-compatible chat-completions provider extension."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx
import json_repair

from porthouse.extension_sdk import ExtensionManifest
from porthouse.extension_sdk.manifest import source_tree_digest
from porthouse.extension_sdk.models import (
    EmbeddingResponse,
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
    sanitize_messages,
    sanitize_tools,
    user_friendly_error,
)

OPENAI_COMPATIBLE_EXTENSION_MANIFEST = ExtensionManifest(
    extension_id="provider-openai-compatible",
    version="0.1.5",
    name="Porthouse OpenAI-compatible Provider",
    extension_types=("model_provider",),
    description="OpenAI-compatible chat completions, streaming, tools and reasoning adapter.",
    distribution_name="porthouse-provider-openai-compatible",
    build_digest=source_tree_digest(__file__),
    required_permissions=("model.invoke",),
    dependencies=({"id": "model-api", "kind": "service", "required": True},),
    configuration_schema={
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "writeOnly": True},
            "api_base": {"type": "string"},
            "extra_headers": {"type": "object"},
        },
    },
)

OPENAI_COMPATIBLE_PROVIDER_SPECS = (
    ModelProviderSpec("custom", (), "", "OPENAI_API_KEY", is_gateway=True),
    ModelProviderSpec(
        "openrouter",
        ("openrouter",),
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        is_gateway=True,
        default_model="openrouter/deepseek/deepseek-v4-flash",
    ),
    ModelProviderSpec(
        "aihubmix",
        ("aihubmix",),
        "https://aihubmix.com/v1",
        "OPENAI_API_KEY",
        is_gateway=True,
    ),
    ModelProviderSpec(
        "openai",
        ("openai", "gpt", "o1", "o3", "o4"),
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
    ),
    ModelProviderSpec("deepseek", ("deepseek",), "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ModelProviderSpec(
        "gemini",
        ("gemini",),
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
    ),
    ModelProviderSpec(
        "zhipu",
        ("zhipu", "glm", "zai"),
        "https://open.bigmodel.cn/api/paas/v4",
        "ZHIPUAI_API_KEY",
    ),
    ModelProviderSpec(
        "dashscope",
        ("qwen", "dashscope"),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
    ),
    ModelProviderSpec(
        "moonshot", ("moonshot", "kimi"), "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"
    ),
    ModelProviderSpec("minimax", ("minimax",), "https://api.minimax.io/v1", "MINIMAX_API_KEY"),
    ModelProviderSpec("vllm", ("vllm",), "", "", is_local=True),
    ModelProviderSpec(
        "ollama",
        ("ollama",),
        "http://127.0.0.1:11434/v1",
        "",
        is_local=True,
    ),
    ModelProviderSpec("groq", ("groq",), "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
)


@dataclass(slots=True)
class _OpenAIStreamState:
    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    calls: dict[int, dict[str, str]] = field(default_factory=dict)
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=missing_usage)
    first_token_seen: bool = False


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
        usage_pricing: dict[str, Any] | None = None,
        request_timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.provider_name = provider_name
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
        value = str(model or self.default_model)
        prefix = f"{self.provider_name}/"
        if value.startswith(prefix):
            return value.removeprefix(prefix)
        if self.provider_name == "openrouter" and value.startswith("openrouter/"):
            return value.removeprefix("openrouter/")
        return value

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "porthouse-runtime"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def _url(self) -> str:
        return f"{str(self.api_base).rstrip('/')}/chat/completions"

    def _embeddings_url(self) -> str:
        return f"{str(self.api_base).rstrip('/')}/embeddings"

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
        if self.provider_name == "deepseek":
            if reasoning_effort == "none":
                payload["thinking"] = {"type": "disabled"}
            elif reasoning_effort:
                # DeepSeek V4 supports only high/max. Keep the Runtime's
                # provider-neutral low/medium/xhigh names deterministic.
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = {
                    "low": "high",
                    "medium": "high",
                    "xhigh": "max",
                }.get(reasoning_effort, reasoning_effort)
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
            failed_usage = self._error_usage(exc)
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="chat.completion",
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

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResponse:
        if not texts or len(texts) > 256 or any(not str(text).strip() for text in texts):
            raise ValueError("embedding input requires 1-256 non-empty texts")
        resolved_model = self._model(model)
        payload: dict[str, Any] = {"model": resolved_model, "input": list(texts)}
        if dimensions is not None:
            payload["dimensions"] = int(dimensions)
        request_id = await model_request_started(
            model=resolved_model,
            operation="embeddings",
            message_count=len(texts),
            tool_count=0,
            provider=self.provider_name,
            request_payload=payload,
            request_url=self._embeddings_url(),
        )
        try:
            response = await self._client.post(
                self._embeddings_url(), headers=self._headers(), json=payload
            )
            await self._raise_for_status(response)
            raw_response = response.json()
            rows = sorted(raw_response.get("data") or [], key=lambda item: int(item["index"]))
            indices = [int(item["index"]) for item in rows]
            if indices != list(range(len(texts))):
                raise ProviderHTTPError(502, "provider returned invalid embedding indices")
            embeddings = [[float(value) for value in item["embedding"]] for item in rows]
            if len(embeddings) != len(texts):
                raise ProviderHTTPError(502, "provider returned an incomplete embedding batch")
            width = len(embeddings[0]) if embeddings else 0
            if not width or any(len(item) != width for item in embeddings):
                raise ProviderHTTPError(502, "provider returned inconsistent embedding dimensions")
            if any(not math.isfinite(value) for item in embeddings for value in item):
                raise ProviderHTTPError(502, "provider returned non-finite embedding values")
            if dimensions is not None and width != int(dimensions):
                raise ProviderHTTPError(502, "provider returned unexpected embedding dimensions")
            usage = self._usage(raw_response.get("usage"))
            await model_request_finished(
                request_id=request_id,
                model=resolved_model,
                operation="embeddings",
                status="succeeded",
                usage=usage,
                provider_request_id=response.headers.get("x-request-id")
                or response.headers.get("request-id"),
                response_payload=raw_response,
            )
            return EmbeddingResponse(
                embeddings=embeddings,
                model=str(raw_response.get("model") or resolved_model),
                usage=usage,
            )
        except Exception as exc:
            failed_usage = self._error_usage(exc)
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="embeddings",
                exc=exc,
                provider_request_id=getattr(exc, "provider_request_id", None),
                response_payload=getattr(exc, "raw_response", None),
                usage=failed_usage,
            )
            raise

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
        state = _OpenAIStreamState()
        try:
            async with self._client.stream(
                "POST", self._url(), headers=self._headers(), json=payload
            ) as response:
                await self._raise_for_status(response)
                async for line in response.aiter_lines():
                    event = self._openai_stream_event(line, state)
                    if event is None:
                        continue
                    emitted = await self._apply_openai_stream_event(
                        event, state, request_id
                    )
                    for item in emitted:
                        yield item
            final = LLMResponse(
                content="".join(state.content_parts) or None,
                tool_calls=self._parse_stream_calls(state.calls, aliases),
                finish_reason=state.finish_reason,
                usage=state.usage,
                reasoning_content="".join(state.reasoning_parts) or None,
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
                response_payload={"stream_events": state.raw_events},
                reasoning_content=final.reasoning_content,
                reasoning_blocks=final.reasoning_blocks,
                provider_block_type="reasoning_content_delta",
            )
            yield "done", final
        except Exception as exc:
            failed_usage = partial_usage(state.usage)
            if failed_usage.get("usage_status") == "missing":
                failed_usage = self._error_usage(exc)
            raw_error = getattr(exc, "raw_response", None)
            failure_payload: Any = {"stream_events": state.raw_events}
            if raw_error is not None:
                failure_payload["provider_error"] = raw_error
            await model_request_failed(
                request_id=request_id,
                model=resolved_model,
                operation="chat.completion.stream",
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
    def _openai_stream_event(
        line: str, state: _OpenAIStreamState
    ) -> dict[str, Any] | None:
        if not line.startswith("data:"):
            return None
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            return None
        event = json.loads(raw)
        state.raw_events.append(event)
        return event

    async def _apply_openai_stream_event(
        self,
        event: dict[str, Any],
        state: _OpenAIStreamState,
        request_id: str,
    ) -> list[tuple[str, str]]:
        if isinstance(event.get("usage"), dict):
            state.usage = self._usage(event["usage"])
        choices = event.get("choices") or []
        if not choices:
            return []
        choice = choices[0]
        delta = choice.get("delta") or {}
        emitted: list[tuple[str, str]] = []
        text = delta.get("content")
        if isinstance(text, str) and text:
            await self._mark_openai_first_token(state, request_id)
            state.content_parts.append(text)
            emitted.append(("delta", text))
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str):
            if reasoning:
                await self._mark_openai_first_token(state, request_id)
                emitted.append(("reasoning_delta", reasoning))
            state.reasoning_parts.append(reasoning)
        self._append_openai_tool_calls(delta.get("tool_calls"), state)
        if choice.get("finish_reason"):
            state.finish_reason = str(choice["finish_reason"])
        return emitted

    @staticmethod
    def _append_openai_tool_calls(value: Any, state: _OpenAIStreamState) -> None:
        for call in value or []:
            index = int(call.get("index") or 0)
            current = state.calls.setdefault(
                index, {"id": "", "name": "", "arguments": ""}
            )
            current["id"] += str(call.get("id") or "")
            function = call.get("function") or {}
            current["name"] += str(function.get("name") or "")
            current["arguments"] += str(function.get("arguments") or "")

    @staticmethod
    async def _mark_openai_first_token(
        state: _OpenAIStreamState, request_id: str
    ) -> None:
        if state.first_token_seen:
            return
        state.first_token_seen = True
        await model_first_token(request_id)

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

    def _parse_response(self, value: dict[str, Any], aliases: dict[str, str]) -> LLMResponse:
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
            usage=self._usage(value.get("usage")),
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

    def _usage(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return missing_usage()
        input_tokens = int(value.get("prompt_tokens") or value.get("input_tokens") or 0)
        output_tokens = int(value.get("completion_tokens") or value.get("output_tokens") or 0)
        raw_prompt_details = value.get("prompt_tokens_details")
        raw_completion_details = value.get("completion_tokens_details")
        prompt_details = dict(raw_prompt_details) if isinstance(raw_prompt_details, dict) else {}
        completion_details = (
            dict(raw_completion_details) if isinstance(raw_completion_details, dict) else {}
        )
        has_counts = any(
            key in value
            for key in (
                "prompt_tokens",
                "input_tokens",
                "completion_tokens",
                "output_tokens",
                "total_tokens",
            )
        )
        return normalized_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            billed_input_tokens=input_tokens,
            billed_output_tokens=output_tokens,
            cached_input_tokens=prompt_details.get("cached_tokens", value.get("cached_tokens", 0)),
            reasoning_output_tokens=completion_details.get(
                "reasoning_tokens", value.get("reasoning_tokens", 0)
            ),
            audio_input_tokens=prompt_details.get("audio_tokens", 0),
            audio_output_tokens=completion_details.get("audio_tokens", 0),
            usage_source="provider" if has_counts else "missing",
            usage_status="exact" if has_counts else "missing",
            provider_cost_usd=value.get("cost", value.get("cost_usd", value.get("total_cost"))),
            pricing=self.usage_pricing,
        )

    def _error_usage(self, exc: Exception) -> dict[str, Any]:
        raw = getattr(exc, "raw_response", None)
        value = raw.get("usage") if isinstance(raw, dict) else None
        return partial_usage(self._usage(value)) if isinstance(value, dict) else missing_usage()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _create_provider(request: ModelProviderBuildRequest) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=request.api_key,
        api_base=request.api_base,
        default_model=request.default_model,
        provider_name=request.provider_name,
        extra_headers=request.extra_headers,
        reasoning_options=request.reasoning_options,
        usage_pricing=request.usage_pricing,
        request_timeout_seconds=request.request_timeout_seconds,
        client=request.client,
    )


OPENAI_COMPATIBLE_PROVIDER_EXTENSION = ModelProviderExtension(
    manifest=OPENAI_COMPATIBLE_EXTENSION_MANIFEST,
    providers=OPENAI_COMPATIBLE_PROVIDER_SPECS,
    factory=_create_provider,
)


def create_extension() -> ModelProviderExtension:
    return OPENAI_COMPATIBLE_PROVIDER_EXTENSION
