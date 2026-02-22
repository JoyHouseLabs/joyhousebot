"""LiteLLM provider implementation for multi-provider support."""

import json
import json_repair
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

# 使用本地 model cost map，避免启动时拉取远程 JSON 导致超时/变慢（仅影响成本统计，不影响推理）
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm import acompletion

from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.providers.registry import find_by_model, find_gateway


# 详细错误信息最大长度，避免刷屏
_MAX_DETAIL_LEN = 1200


def _mask_api_key(api_key: str | None) -> str:
    """Mask API key for display: 'not set' or first6...last4."""
    if not api_key or not api_key.strip():
        return "not set"
    key = api_key.strip()
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure no message has content: null (DeepSeek etc. require string)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        m = dict(m)
        if "content" in m and m["content"] is None:
            m["content"] = ""
        out.append(m)
    return out


def _format_request_debug(model: str | None, api_base: str | None, api_key: str | None) -> str:
    """Format request params for debugging: model, api_base, api_key (masked)."""
    parts = [
        f"model={model or '(none)'}",
        f"api_base={api_base or '(default)'}",
        f"api_key={_mask_api_key(api_key)}",
    ]
    return ", ".join(parts)


def _user_friendly_llm_error(
    exc: Exception,
    model: str | None = None,
    *,
    api_base: str | None = None,
    api_key: str | None = None,
) -> str:
    """Turn LiteLLM/API errors into a short, actionable message, with full detail appended."""
    err_str = str(exc)
    # 1) Try to get API error message from received_args (e.g. "404 NOT_FOUND")
    match = re.search(r"'msg':\s*'([^']+)'", err_str)
    if match:
        api_msg = match.group(1).strip()
        hint = "Check agents.defaults.model, api_base and API key; ensure the model is available at the endpoint."
        short = f"Error calling LLM: {api_msg}. {hint}"
    elif "404" in err_str or "NOT_FOUND" in err_str:
        hint = "Model or endpoint not found. Check agents.defaults.model and api_base."
        short = f"Error calling LLM: 404 NOT_FOUND. {hint}"
    else:
        # 2) Fallback: use first line of exception to avoid huge tracebacks in short msg
        first_line = err_str.split("\n")[0].strip()
        if len(err_str) > 200:
            short = f"Error calling LLM: {first_line}"
        else:
            short = f"Error calling LLM: {err_str}"
    # Append full exception detail (truncated if too long)
    detail = err_str if len(err_str) <= _MAX_DETAIL_LEN else err_str[: _MAX_DETAIL_LEN] + "\n... (truncated)"
    out = f"{short}\n\nDetail: {detail}"
    if model is not None or api_base is not None or api_key is not None:
        out += f"\n\nRequest: {_format_request_debug(model, api_base, api_key)}"
    return out


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    msg = str(exc)
    for code in (401, 403, 404, 408, 409, 425, 429, 500, 502, 503, 504):
        if f"{code}" in msg:
            return code
    return None


def _classify_error_kind(exc: Exception) -> tuple[str, bool]:
    msg = str(exc).lower()
    if any(x in msg for x in ("rate limit", "too many requests", "429")):
        return "rate_limit", True
    if any(x in msg for x in ("insufficient", "credit", "billing", "payment", "quota exceeded")):
        return "billing", False
    if any(x in msg for x in ("unauthorized", "invalid api key", "forbidden", "401", "403")):
        return "auth", False
    if any(x in msg for x in ("timeout", "timed out", "deadline exceeded")):
        return "timeout", True
    status = _extract_status_code(exc)
    if status is not None:
        if status >= 500 or status in {408, 409, 425, 429}:
            return "unknown", True
        return "unknown", False
    return "unknown", True


def _build_error_meta(exc: Exception) -> dict[str, Any]:
    kind, retryable = _classify_error_kind(exc)
    status = _extract_status_code(exc)
    code = getattr(exc, "code", None)
    if code is None:
        code = exc.__class__.__name__
    return {
        "error_kind": kind,
        "retryable": retryable,
        "error_status": status,
        "error_code": str(code),
    }


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.
    """
    
    # User-Agent sent to external LLM APIs; aligned with OpenClaw (provider-usage.fetch.claude.ts, etc.)
    DEFAULT_LLM_USER_AGENT = "openclaw"

    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = dict(extra_headers or {})
        # If User-Agent not set, use OpenClaw-aligned UA so external APIs see "openclaw"
        if not any(k.lower() == "user-agent" for k in self.extra_headers):
            self.extra_headers["User-Agent"] = self.DEFAULT_LLM_USER_AGENT
        
        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)
        
        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)
        
        if api_base:
            litellm.api_base = api_base
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True
    
    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)
    
    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model
        
        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        # Anthropic API 只接受裸模型 id（如 claude-opus-4-5），带 "anthropic/" 会 404
        if spec and spec.name == "anthropic" and model.startswith("anthropic/"):
            model = model.removeprefix("anthropic/")
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"
        
        return model
    
    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return
    
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
        
        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = self._resolve_model(model or self.default_model)
        
        # Clamp max_tokens to at least 1 — negative or zero values cause
        # LiteLLM to reject the request with "max_tokens must be at least 1".
        max_tokens = max(1, max_tokens)
        
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(model, kwargs)
        
        # Pass api_key directly — more reliable than env vars alone
        if self.api_key:
            kwargs["api_key"] = self.api_key
        
        # Pass api_base for custom endpoints
        if self.api_base:
            kwargs["api_base"] = self.api_base
        
        # Pass extra headers (e.g. APP-Code for AiHubMix)
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        kwargs["messages"] = _sanitize_messages(kwargs["messages"])
        
        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            msg = _user_friendly_llm_error(
                e, model=model, api_base=self.api_base, api_key=self.api_key
            )
            meta = _build_error_meta(e)
            return LLMResponse(
                content=msg,
                finish_reason="error",
                **meta,
            )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncGenerator[tuple[str, LLMResponse | None], None]:
        """
        Stream chat completion: yields ("delta", content_str) for each content delta,
        then ("done", LLMResponse) with the full response (for tool_calls etc.).
        """
        model = self._resolve_model(model or self.default_model)
        max_tokens = max(1, max_tokens)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        self._apply_model_overrides(model, kwargs)
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        kwargs["messages"] = _sanitize_messages(kwargs["messages"])

        accumulated_content: list[str] = []
        accumulated_tool_calls: list[dict[str, Any]] = []
        finish_reason = "stop"
        usage: dict[str, int] = {}

        try:
            stream = await acompletion(**kwargs)
            async for chunk in stream:
                choices = chunk.get("choices", []) if isinstance(chunk, dict) else getattr(chunk, "choices", [])
                if not choices:
                    if isinstance(chunk, dict) and chunk.get("usage"):
                        usage = chunk["usage"]
                        if isinstance(usage, dict):
                            usage = {k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
                    continue
                choice = choices[0] if isinstance(choices, list) else choices
                delta = choice.get("delta", {}) if isinstance(choice, dict) else getattr(choice, "delta", None) or {}
                # Content delta
                content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    accumulated_content.append(content)
                    yield ("delta", content)
                # Tool calls (OpenAI streaming: delta.tool_calls)
                tc_list = delta.get("tool_calls") if isinstance(delta, dict) else getattr(delta, "tool_calls", None)
                if tc_list:
                    for tc in tc_list:
                        if isinstance(tc, dict):
                            accumulated_tool_calls.append(tc)
                        else:
                            accumulated_tool_calls.append({"id": getattr(tc, "id", ""), "function": getattr(tc, "function", {})})
                fr = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
                if fr:
                    finish_reason = fr or "stop"
            # Build final LLMResponse from accumulated data
            full_content = "".join(accumulated_content)
            tool_calls_parsed: list[ToolCallRequest] = []
            for tc in accumulated_tool_calls:
                fn = tc.get("function") if isinstance(tc, dict) else {}
                args = fn.get("arguments", "{}") if isinstance(fn, dict) else getattr(fn, "arguments", "{}")
                if isinstance(args, str):
                    args = json_repair.loads(args)
                if not isinstance(args, dict):
                    args = {}
                name = (fn.get("name") or "") if isinstance(fn, dict) else (getattr(fn, "name", None) or "")
                if not isinstance(name, str):
                    name = ""
                tool_calls_parsed.append(ToolCallRequest(
                    id=tc.get("id", ""),
                    name=name,
                    arguments=args,
                ))
            final = LLMResponse(
                content=full_content or None,
                tool_calls=tool_calls_parsed,
                finish_reason=finish_reason,
                usage=usage,
            )
            yield ("done", final)
        except Exception as e:
            msg = _user_friendly_llm_error(
                e, model=model, api_base=self.api_base, api_key=self.api_key
            )
            meta = _build_error_meta(e)
            yield ("done", LLMResponse(content=msg, finish_reason="error", **meta))

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = getattr(tc.function, "arguments", "{}")
                if isinstance(args, str):
                    args = json_repair.loads(args)
                if not isinstance(args, dict):
                    args = {}
                name = getattr(tc.function, "name", None) or ""
                if not isinstance(name, str):
                    name = ""
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        reasoning_content = getattr(message, "reasoning_content", None)
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )
    
    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
