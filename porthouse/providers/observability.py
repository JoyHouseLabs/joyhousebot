"""Full-fidelity model observability at the native provider boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from porthouse.providers.usage import cache_hit_usage, missing_usage
from porthouse.runtime.context import get_current_run_context
from porthouse.runtime.tracking import (
    append_trace_event_async,
    get_request_tracking,
    new_request_id,
    redact_sensitive_text,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelObservationContext:
    turn_id: str | None = None
    attempt: int = 1
    provider: str = "unknown"


_model_context: ContextVar[ModelObservationContext | None] = ContextVar(
    "porthouse_model_observation", default=None
)


@contextmanager
def bind_model_observation(*, turn_id: str | None, attempt: int, provider: str) -> Iterator[None]:
    token = _model_context.set(
        ModelObservationContext(turn_id=turn_id, attempt=max(1, attempt), provider=provider)
    )
    try:
        yield
    finally:
        _model_context.reset(token)


async def _store_call(method: str, *args: Any, **kwargs: Any) -> Any:
    tracking = get_request_tracking()
    store = tracking.store if tracking else None
    target = getattr(store, method, None)
    if target is None:
        return None
    try:
        return await asyncio.to_thread(target, *args, **kwargs)
    except Exception:
        _logger.exception("failed to persist model observability: %s", method)
        return None


async def model_request_started(
    *,
    model: str,
    operation: str,
    message_count: int,
    tool_count: int,
    provider: str = "unknown",
    request_payload: Any = None,
    request_url: str | None = None,
) -> str | None:
    tracking = get_request_tracking()
    request_id = new_request_id("model") if tracking else None
    observation = _model_context.get()
    run_context = get_current_run_context()
    resolved_provider = provider or (observation.provider if observation else "unknown")
    if tracking and request_id:
        span_id = f"span_{request_id}"
        request_blob = await _store_call(
            "put_trace_blob",
            run_id=tracking.run_id,
            invocation_id=request_id,
            kind="model.request",
            content=request_payload or {},
        )
        snapshot = await _store_call("get_run_execution_snapshot", tracking.run_id)
        await _store_call(
            "start_execution_span",
            span_id=span_id,
            trace_id=tracking.tracker_id,
            parent_span_id=getattr(run_context, "parent_span_id", None),
            run_id=tracking.run_id,
            task_id=getattr(run_context, "task_id", None),
            turn_id=observation.turn_id if observation else None,
            span_kind="model",
            name=f"{resolved_provider}:{operation}",
            worker_id=getattr(run_context, "worker_id", None),
            attributes={"model": model, "operation": operation, "request_url": request_url},
        )
        await _store_call(
            "create_model_invocation",
            invocation_id=request_id,
            run_id=tracking.run_id,
            task_id=getattr(run_context, "task_id", None),
            turn_id=observation.turn_id if observation else None,
            span_id=span_id,
            attempt=observation.attempt if observation else 1,
            provider=resolved_provider,
            model=model,
            operation=operation,
            agent_revision_id=getattr(snapshot, "agent_revision_id", None),
            request_blob_id=getattr(request_blob, "blob_id", None),
            request_hash=getattr(request_blob, "sha256", None),
        )
    await append_trace_event_async(
        request_id=request_id,
        parent_request_id=tracking.request_id if tracking else None,
        transport="model",
        direction="outbound",
        operation=operation,
        stage="request",
        status="sent",
        data={
            "provider": resolved_provider,
            "model": model,
            "message_count": message_count,
            "tool_count": tool_count,
            "invocation_id": request_id,
        },
    )
    return request_id


async def model_first_token(request_id: str | None) -> None:
    if request_id:
        await _store_call("mark_model_invocation_first_token", request_id)


async def model_request_finished(
    *,
    request_id: str | None,
    model: str,
    operation: str,
    status: str,
    usage: dict[str, Any] | None = None,
    has_tool_calls: bool = False,
    provider_request_id: str | None = None,
    response_payload: Any = None,
    reasoning_content: str | None = None,
    reasoning_blocks: list[dict[str, Any]] | None = None,
    reasoning_source: str = "provider_native",
    provider_block_type: str | None = None,
    cache_status: str = "miss",
) -> None:
    tracking = get_request_tracking()
    response_blob = None
    if tracking and request_id:
        response_blob = await _store_call(
            "put_trace_blob",
            run_id=tracking.run_id,
            invocation_id=request_id,
            kind="model.response",
            content=response_payload or {},
        )
        has_reasoning = bool(reasoning_content or reasoning_blocks)
        if reasoning_content:
            await _store_call(
                "append_reasoning_segment",
                invocation_id=request_id,
                run_id=tracking.run_id,
                source=reasoning_source,
                kind="analysis",
                content=reasoning_content,
                fidelity="exact" if reasoning_source == "provider_native" else "normalized",
                provider_block_type=provider_block_type,
            )
        for block in reasoning_blocks or []:
            await _store_call(
                "append_reasoning_segment",
                invocation_id=request_id,
                run_id=tracking.run_id,
                source=reasoning_source,
                kind="provider_block",
                content=json.dumps(
                    block,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
                content_format="application/json",
                fidelity="exact",
                provider_block_type=str(block.get("type") or provider_block_type or "reasoning"),
            )
        numeric_usage = usage or {}
        await _store_call(
            "finish_model_invocation",
            request_id,
            provider_request_id=provider_request_id,
            response_blob_id=getattr(response_blob, "blob_id", None),
            response_hash=getattr(response_blob, "sha256", None),
            status="failed" if status == "error" else "completed",
            finish_reason=status,
            reasoning_availability=(reasoning_source if has_reasoning else "unavailable"),
            usage=numeric_usage,
            cost_usd=float(
                numeric_usage.get(
                    "cost_usd", numeric_usage.get("total_cost", numeric_usage.get("cost", 0.0))
                )
                or 0.0
            ),
            cache_status=cache_status,
        )
    await append_trace_event_async(
        request_id=request_id,
        parent_request_id=tracking.request_id if tracking else None,
        transport="model",
        direction="inbound",
        operation=operation,
        stage="response",
        status=status,
        data={
            "model": model,
            "usage": usage or {},
            "has_tool_calls": has_tool_calls,
            "provider_request_id": provider_request_id,
            "invocation_id": request_id,
            "reasoning_availability": (
                reasoning_source if reasoning_content or reasoning_blocks else "unavailable"
            ),
            "cache_status": cache_status,
        },
    )


async def model_cache_hit(
    *,
    provider: str,
    model: str,
    operation: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_payload: dict[str, Any],
    reasoning_content: str | None,
) -> dict[str, Any]:
    request_id = await model_request_started(
        provider=provider,
        model=model,
        operation=operation,
        message_count=len(messages),
        tool_count=len(tools or []),
        request_payload={"messages": messages, "tools": tools or [], "cache_lookup": True},
        request_url="cache://exact-model-response",
    )
    await model_first_token(request_id)
    usage = cache_hit_usage(dict(response_payload.get("usage") or {}))
    await model_request_finished(
        request_id=request_id,
        model=model,
        operation=operation,
        status=str(response_payload.get("finish_reason") or "stop"),
        usage=usage,
        has_tool_calls=bool(response_payload.get("tool_calls")),
        provider_request_id=str(response_payload.get("source_invocation_id") or "") or None,
        response_payload=response_payload,
        reasoning_content=reasoning_content,
        reasoning_blocks=list(response_payload.get("reasoning_blocks") or []),
        reasoning_source=str(response_payload.get("reasoning_source") or "provider_native"),
        provider_block_type="cache",
        cache_status="hit",
    )
    return usage


async def model_request_failed(
    *,
    request_id: str | None,
    model: str,
    operation: str,
    exc: Exception,
    provider_request_id: str | None = None,
    response_payload: Any = None,
    usage: dict[str, Any] | None = None,
) -> None:
    tracking = get_request_tracking()
    error = {
        "error_type": type(exc).__name__,
        "message": redact_sensitive_text(str(exc)),
    }
    response_blob = None
    recorded_usage = dict(usage or missing_usage())
    if tracking and request_id:
        response_blob = await _store_call(
            "put_trace_blob",
            run_id=tracking.run_id,
            invocation_id=request_id,
            kind="model.error",
            content=response_payload or error,
        )
        await _store_call(
            "finish_model_invocation",
            request_id,
            provider_request_id=provider_request_id,
            response_blob_id=getattr(response_blob, "blob_id", None),
            response_hash=getattr(response_blob, "sha256", None),
            status="failed",
            finish_reason="error",
            reasoning_availability="unavailable",
            usage=recorded_usage,
            cost_usd=float(recorded_usage.get("cost_usd") or 0.0),
            error=error,
        )
    await append_trace_event_async(
        request_id=request_id,
        parent_request_id=tracking.request_id if tracking else None,
        transport="model",
        direction="inbound",
        operation=operation,
        stage="error",
        status="failed",
        data={
            "model": model,
            **error,
            "invocation_id": request_id,
            "usage": recorded_usage,
        },
    )
