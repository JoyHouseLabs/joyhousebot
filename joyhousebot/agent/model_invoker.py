"""ModelInvoker responsibilities for the shared Agent engine."""

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.agent.auth_profiles import (
    classify_failover_reason,
    is_profile_available,
    mark_profile_failure,
    mark_profile_success,
    resolve_profile_order,
)
from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.providers.observability import bind_model_observation, model_cache_hit
from joyhousebot.utils.exceptions import (
    classify_exception,
    sanitize_error_message,
)

if TYPE_CHECKING:
    pass


class ModelInvokerMixin:
    def _model_cache_policy(self) -> dict[str, Any]:
        revision = getattr(self, "agent_revision", None)
        policy = dict(revision.model_policy) if revision is not None else {}
        return policy if bool(policy.get("cache_enabled", False)) else {}

    def _model_cache_key(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> str:
        payload = {
            "version": 1,
            "provider": provider,
            "model": model,
            "messages": messages,
            "tools": tools or [],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "agent_revision_id": getattr(getattr(self, "agent_revision", None), "revision_id", None),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _cached_response(value: dict[str, Any]) -> LLMResponse:
        return LLMResponse(
            content=value.get("content"),
            tool_calls=[
                ToolCallRequest(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or ""),
                    arguments=dict(item.get("arguments") or {}),
                )
                for item in value.get("tool_calls") or []
            ],
            finish_reason=str(value.get("finish_reason") or "stop"),
            usage=dict(value.get("usage") or {}),
            reasoning_content=value.get("reasoning_content"),
            reasoning_blocks=list(value.get("reasoning_blocks") or []),
        )

    @staticmethod
    def _cache_payload(response: LLMResponse) -> dict[str, Any]:
        return {
            "content": response.content,
            "tool_calls": [
                {"id": item.id, "name": item.name, "arguments": item.arguments}
                for item in response.tool_calls
            ],
            "finish_reason": response.finish_reason,
            "usage": response.usage,
            "reasoning_content": response.reasoning_content,
            "reasoning_blocks": response.reasoning_blocks,
            "reasoning_source": "provider_native",
        }

    def _normalize_model_fallbacks(self, raw_fallbacks: list[str] | None) -> list[str]:
        seen = {self.model}
        out: list[str] = []
        for raw in raw_fallbacks or []:
            model = str(raw or "").strip()
            if not model or model in seen:
                continue
            seen.add(model)
            out.append(model)
        return out

    def _resolve_provider_name_for_model(self, model: str) -> str:
        if "/" in model:
            return str(model.split("/", 1)[0]).strip()
        if self.config and hasattr(self.config, "get_provider_name"):
            resolved = self.config.get_provider_name(model)
            if resolved:
                return str(resolved).strip()
        return ""

    def _build_runtime_provider(self, *, model: str, profile_id: str | None) -> LLMProvider:
        if self.config is None or (profile_id is None and model == self.model):
            return self.provider
        from joyhousebot.providers.factory import create_model_provider

        provider_name = self._resolve_provider_name_for_model(
            model
        ) or self.config.get_provider_name(model)
        base_cfg = self.config.get_provider(model)
        api_key = base_cfg.api_key if base_cfg else None
        api_base = self.config.get_api_base(model)
        extra_headers = dict(base_cfg.extra_headers or {}) if base_cfg else {}

        if profile_id:
            profile = (getattr(self.config, "auth", None).profiles or {}).get(profile_id)
            if profile is not None:
                if getattr(profile, "api_key", ""):
                    api_key = profile.api_key
                elif getattr(profile, "token", ""):
                    api_key = profile.token
                if getattr(profile, "api_base", None):
                    api_base = profile.api_base
                if getattr(profile, "extra_headers", None):
                    extra_headers.update(dict(profile.extra_headers))
                if getattr(profile, "provider", ""):
                    provider_name = str(profile.provider).strip() or provider_name

        return create_model_provider(
            config=self.config,
            model=model,
            api_key=api_key,
            api_base=api_base,
            extra_headers=extra_headers or None,
            provider_name=provider_name or None,
            client=getattr(self.provider, "http_client", None),
            model_policy=(
                dict(self.agent_revision.model_policy)
                if getattr(self, "agent_revision", None) is not None
                else None
            ),
        )

    def _resolve_profile_candidates(self, provider_name: str) -> list[str | None]:
        if self.config is None or not provider_name:
            return [None]
        profile_ids = resolve_profile_order(self.config, provider_name)
        if not profile_ids:
            return [None]

        if self._profile_health_repository is not None:
            self._auth_profile_usage = self._profile_health_repository.load()
        now_ms = time.time() * 1000
        available: list[str] = []
        in_cooldown: list[str] = []
        for pid in profile_ids:
            if is_profile_available(self._auth_profile_usage, pid, now_ms=now_ms):
                available.append(pid)
            else:
                in_cooldown.append(pid)
        ordered = available if available else in_cooldown
        return [None] + ordered

    async def _call_provider_with_fallback(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        primary_model: str,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        allow_stream: bool = False,
        lifecycle_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        turn_id: str | None = None,
    ) -> tuple[LLMResponse, str]:
        candidates = [primary_model] + [m for m in self.model_fallbacks if m != primary_model]
        now = time.time()
        available: list[str] = []
        in_cooldown: list[str] = []
        for candidate in candidates:
            until = self._model_cooldown_until.get(candidate, 0.0)
            if until > now:
                in_cooldown.append(candidate)
            else:
                available.append(candidate)
        # Prefer non-cooled models. If all are cooled, still try to avoid deadlock.
        if available:
            candidates = available
        elif in_cooldown:
            candidates = in_cooldown
        last_response: LLMResponse | None = None
        stream_used = False
        cache_policy = self._model_cache_policy()
        tool_definitions = tools
        for idx, candidate in enumerate(candidates):
            provider_name = self._resolve_provider_name_for_model(candidate)
            profile_candidates = self._resolve_profile_candidates(provider_name)
            for pidx, profile_id in enumerate(profile_candidates):
                attempt_index = idx * max(1, len(profile_candidates)) + pidx + 1
                if lifecycle_callback and (idx > 0 or pidx > 0):
                    await lifecycle_callback(
                        "provider_fallback",
                        {
                            "from_model": primary_model,
                            "to_model": candidate,
                            "profile_id": profile_id,
                            "model_attempt": idx + 1,
                            "profile_attempt": pidx + 1,
                        },
                    )
                runtime_provider = self._build_runtime_provider(
                    model=candidate, profile_id=profile_id
                )
                cache_key = self._model_cache_key(
                    provider=provider_name,
                    model=candidate,
                    messages=messages,
                    tools=tool_definitions,
                )
                if cache_policy and hasattr(self.runtime_store, "get_model_response_cache"):
                    cached = await asyncio.to_thread(
                        self.runtime_store.get_model_response_cache, cache_key
                    )
                    if cached:
                        response_payload = dict(cached["response"])
                        response_payload["source_invocation_id"] = cached.get(
                            "source_invocation_id"
                        )
                        response = self._cached_response(response_payload)
                        if lifecycle_callback:
                            await lifecycle_callback(
                                "cache_hit",
                                {"model": candidate, "cache_key": cache_key},
                            )
                        with bind_model_observation(
                            turn_id=turn_id,
                            attempt=attempt_index,
                            provider=provider_name,
                        ):
                            response.usage = await model_cache_hit(
                                provider=provider_name,
                                model=candidate,
                                operation="model.cache.hit",
                                messages=messages,
                                tools=tool_definitions,
                                response_payload=response_payload,
                                reasoning_content=response.reasoning_content,
                            )
                        return response, candidate
                use_stream = (
                    allow_stream
                    and stream_callback is not None
                    and not stream_used
                    and idx == 0
                    and pidx == 0
                )
                if use_stream and hasattr(runtime_provider, "chat_stream"):
                    response: LLMResponse | None = None
                    stream_buffer: list[str] = []
                    try:
                        with bind_model_observation(
                            turn_id=turn_id,
                            attempt=attempt_index,
                            provider=provider_name,
                        ):
                            async for kind, data in runtime_provider.chat_stream(
                                messages=messages,
                                tools=tools,
                                model=candidate,
                                temperature=self.temperature,
                                max_tokens=self.max_tokens,
                            ):
                                if kind == "delta" and isinstance(data, str):
                                    stream_buffer.append(data)
                                    if stream_callback is not None:
                                        await stream_callback(data)
                                elif kind == "reasoning_delta" and isinstance(data, str):
                                    if lifecycle_callback is not None:
                                        await lifecycle_callback(
                                            "reasoning_delta",
                                            {"content": data, "model": candidate},
                                        )
                                elif kind == "done" and data is not None:
                                    response = data
                                    break
                    except asyncio.TimeoutError:
                        response = LLMResponse(content="Stream timeout", finish_reason="error")
                        logger.warning(f"Stream timeout for model {candidate}")
                    except ConnectionError as e:
                        response = LLMResponse(
                            content=f"Connection error: {sanitize_error_message(str(e))}",
                            finish_reason="error",
                        )
                        logger.error(f"Stream connection error for model {candidate}")
                    except Exception as e:
                        code, _, _ = classify_exception(e)
                        sanitized = sanitize_error_message(str(e))
                        response = LLMResponse(
                            content=f"Stream error [{code}]: {sanitized}", finish_reason="error"
                        )
                        logger.error(f"Stream error [{code}] for model {candidate}: {sanitized}")
                    if response is None:
                        response = LLMResponse(
                            content="Stream ended without response", finish_reason="error"
                        )
                    stream_used = True
                else:
                    with bind_model_observation(
                        turn_id=turn_id,
                        attempt=attempt_index,
                        provider=provider_name,
                    ):
                        response = await runtime_provider.chat(
                            messages=messages,
                            tools=tools,
                            model=candidate,
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                        )
                if response.finish_reason != "error":
                    if cache_policy and hasattr(self.runtime_store, "put_model_response_cache"):
                        ttl_seconds = max(1, int(cache_policy.get("cache_ttl_seconds") or 300))
                        await asyncio.to_thread(
                            self.runtime_store.put_model_response_cache,
                            cache_key,
                            provider=provider_name,
                            model=candidate,
                            response=self._cache_payload(response),
                            expires_at=(
                                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
                            ).isoformat(),
                        )
                    if profile_id:
                        if self._profile_health_repository is not None:
                            self._auth_profile_usage[profile_id] = (
                                self._profile_health_repository.mark_success(
                                    profile_id, provider_name
                                )
                            )
                        else:
                            mark_profile_success(self._auth_profile_usage, profile_id)
                    self._mark_model_success(candidate)
                    if candidate != primary_model:
                        logger.warning(f"Model fallback selected: {primary_model} -> {candidate}")
                    return response, candidate
                # DeepSeek occasionally reports invalid tool names in long sessions.
                # Retry once with a shorter context (keep system + most recent turns).
                err_text = str(response.content or "")
                if (
                    "invalid 'tools[" in err_text.lower()
                    and "function.name" in err_text.lower()
                    and len(messages) > 8
                ):
                    compact_messages: list[dict[str, Any]] = []
                    if (
                        messages
                        and isinstance(messages[0], dict)
                        and messages[0].get("role") == "system"
                    ):
                        compact_messages.append(messages[0])
                    recent = [m for m in messages[-6:] if isinstance(m, dict)]
                    compact_messages.extend(recent)
                    try:
                        if lifecycle_callback:
                            await lifecycle_callback(
                                "model_retry",
                                {
                                    "model": candidate,
                                    "reason": "provider_tool_name_validation",
                                },
                            )
                        with bind_model_observation(
                            turn_id=turn_id,
                            attempt=attempt_index + 1,
                            provider=provider_name,
                        ):
                            retry_response = await runtime_provider.chat(
                                messages=compact_messages,
                                tools=tools,
                                model=candidate,
                                temperature=self.temperature,
                                max_tokens=self.max_tokens,
                            )
                        if retry_response.finish_reason != "error":
                            logger.warning(
                                "Recovered from provider tool-name validation error by using compact history"
                            )
                            return retry_response, candidate
                    except Exception:
                        pass
                last_response = response
                reason = str(response.error_kind or "").strip() or classify_failover_reason(
                    response.content or ""
                )
                if profile_id and self.config is not None:
                    if self._profile_health_repository is not None:
                        self._auth_profile_usage[profile_id] = (
                            self._profile_health_repository.mark_failure(
                                profile_id,
                                provider_name,
                                reason,
                                self.config,
                            )
                        )
                    else:
                        mark_profile_failure(
                            self._auth_profile_usage,
                            profile_id=profile_id,
                            provider=provider_name,
                            reason=reason,
                            config=self.config,
                        )
                self._mark_model_failure(candidate)
                if pidx < len(profile_candidates) - 1:
                    logger.warning(
                        f"Model call failed on {candidate} profile={profile_id}, trying next profile"
                    )
            if idx < len(candidates) - 1:
                logger.warning(f"Model call failed on {candidate}, trying fallback")
        return last_response or LLMResponse(
            content="All models failed", finish_reason="error"
        ), primary_model

    def _mark_model_success(self, model: str) -> None:
        self._model_failure_count.pop(model, None)
        self._model_cooldown_until.pop(model, None)

    def _mark_model_failure(self, model: str) -> None:
        tracked = getattr(self, "_tracked_models", None)
        if tracked is not None and model not in tracked:
            # User-supplied model names are not written to the shared cooldown
            # table: an attacker could otherwise exhaust it or cool down
            # arbitrary models for every user of this process.
            return
        failures = int(self._model_failure_count.get(model, 0)) + 1
        self._model_failure_count[model] = failures
        # Exponential backoff cooldown: 15s, 30s, 60s, ... capped at 5min.
        cooldown_s = min(300.0, 15.0 * (2 ** max(0, failures - 1)))
        # Bound the table even if the tracked model set changes at runtime.
        while len(self._model_cooldown_until) >= 64 and model not in self._model_cooldown_until:
            self._model_cooldown_until.pop(next(iter(self._model_cooldown_until)))
        self._model_cooldown_until[model] = time.time() + cooldown_s
