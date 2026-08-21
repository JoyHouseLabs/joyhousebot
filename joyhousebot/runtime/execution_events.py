"""Translate executor callbacks into durable Runtime events and spans."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from joyhousebot.runtime.models import AgentEvent, AgentUsage, EventType, EventVisibility
from joyhousebot.storage.contracts import RuntimeStores


class ExecutionEventRuntime(Protocol):
    worker_id: str
    stores: RuntimeStores
    events: Any

    async def _log(self, run_id: str, event_type: str, message: str, **kwargs: Any) -> None: ...


_EVENT_TYPES = {
    "llm_delta": EventType.MESSAGE_DELTA.value,
    "model_request_start": EventType.MODEL_REQUEST_STARTED.value,
    "thinking_start": EventType.MODEL_THINKING_STARTED.value,
    "thinking_end": EventType.MODEL_THINKING_COMPLETED.value,
    "reasoning_delta": EventType.MODEL_REASONING_DELTA.value,
    "model_response_end": EventType.MODEL_RESPONSE_COMPLETED.value,
    "provider_fallback": EventType.MODEL_PROVIDER_FALLBACK.value,
    "model_retry": EventType.MODEL_RETRY_SCHEDULED.value,
    "cache_hit": EventType.MODEL_CACHE_HIT.value,
    "context_built": EventType.CONTEXT_BUILT.value,
    "turn_started": EventType.TURN_STARTED.value,
    "turn_recovered": EventType.TURN_RECOVERED.value,
    "turn_completed": EventType.TURN_COMPLETED.value,
    "verification_started": EventType.VERIFICATION_STARTED.value,
    "verification_passed": EventType.VERIFICATION_PASSED.value,
    "verification_failed": EventType.VERIFICATION_FAILED.value,
    "loop_stalled": EventType.LOOP_STALLED.value,
    "loop_exhausted": EventType.LOOP_EXHAUSTED.value,
    "tool_requested": EventType.CAPABILITY_REQUESTED.value,
    "permission_requested": EventType.CAPABILITY_PERMISSION_REQUESTED.value,
    "permission_resolved": EventType.CAPABILITY_PERMISSION_RESOLVED.value,
    "tool_start": EventType.CAPABILITY_STARTED.value,
    "tool_output": EventType.CAPABILITY_PROGRESS.value,
    "tool_end": EventType.CAPABILITY_COMPLETED.value,
    "final": EventType.MESSAGE_COMPLETED.value,
    "usage": EventType.USAGE_UPDATED.value,
}
_FAILED_EVENTS = {
    EventType.CAPABILITY_FAILED.value,
    EventType.LOOP_STALLED.value,
    EventType.LOOP_EXHAUSTED.value,
    EventType.VERIFICATION_FAILED.value,
}
_COMPLETED_EVENTS = {
    EventType.CAPABILITY_COMPLETED.value,
    EventType.MODEL_RESPONSE_COMPLETED.value,
    EventType.MESSAGE_COMPLETED.value,
    EventType.TURN_COMPLETED.value,
    EventType.VERIFICATION_PASSED.value,
}


@dataclass(slots=True)
class ExecutionEventBridge:
    runtime: ExecutionEventRuntime
    run_id: str
    task_id: str | None
    tracker_id: str
    execution_span_id: str
    model: str | None
    usage: AgentUsage
    tools_used: list[str] = field(default_factory=list)

    async def observe(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Persist best-effort observability without affecting execution."""
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except Exception:
            return None

    async def handle(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "tool_start" and payload.get("tool"):
            self.tools_used.append(str(payload["tool"]))
        payload = dict(payload)
        if payload.get("tool") and not payload.get("capability_id"):
            payload["capability_id"] = payload["tool"]
        mapped = (
            EventType.CAPABILITY_FAILED.value
            if event_type == "tool_end" and payload.get("ok") is False
            else _EVENT_TYPES.get(event_type, event_type)
        )
        await self._record_tool_span(event_type, payload)
        if event_type == "usage":
            received = AgentUsage.from_dict(payload)
            received.model = str(payload.get("model") or self.model or "") or None
            self.usage = received
        await self.runtime.events.publish(
            AgentEvent(
                run_id=self.run_id,
                task_id=self.task_id,
                type=mapped,
                data=payload,
                event_id=self._event_id(mapped, payload),
                turn_id=str(payload.get("turn_id") or "") or None,
                span_id=str(payload.get("span_id") or "") or None,
                parent_span_id=str(payload.get("parent_span_id") or "") or None,
                tool_call_id=str(payload.get("tool_call_id") or "") or None,
                attempt=(
                    int(payload["attempt"])
                    if payload.get("attempt") is not None
                    else None
                ),
                status=self._status(mapped, payload),
                visibility=(
                    EventVisibility.PRIVATE.value
                    if mapped == EventType.MODEL_REASONING_DELTA.value
                    else EventVisibility.PUBLIC.value
                ),
                worker_id=self.runtime.worker_id,
            )
        )
        if event_type not in {"llm_delta", "reasoning_delta"}:
            await self.runtime._log(
                self.run_id,
                mapped,
                f"Execution event: {mapped}",
                level=(
                    "error"
                    if mapped
                    in {
                        EventType.CAPABILITY_FAILED.value,
                        EventType.VERIFICATION_FAILED.value,
                    }
                    else "info"
                ),
                task_id=self.task_id,
                data=payload,
            )

    async def _record_tool_span(
        self, event_type: str, payload: dict[str, Any]
    ) -> None:
        if event_type == "tool_start" and payload.get("span_id"):
            await self.observe(
                self.runtime.stores.observability.start_execution_span,
                span_id=str(payload["span_id"]),
                trace_id=self.tracker_id,
                parent_span_id=self.execution_span_id,
                run_id=self.run_id,
                task_id=self.task_id,
                turn_id=str(payload.get("turn_id") or "") or None,
                span_kind="capability",
                name=str(payload.get("tool") or "capability"),
                worker_id=self.runtime.worker_id,
                attributes={
                    "tool_call_id": payload.get("tool_call_id"),
                    "arguments": payload.get("args") or {},
                },
            )
        elif event_type == "tool_end" and payload.get("span_id"):
            await self.observe(
                self.runtime.stores.observability.finish_execution_span,
                str(payload["span_id"]),
                status="completed" if payload.get("ok") is not False else "failed",
                duration_ms=payload.get("duration_ms"),
                error=(
                    {"code": payload.get("error_code"), "message": payload.get("error")}
                    if payload.get("ok") is False
                    else None
                ),
                attributes={
                    "invocation_id": payload.get("invocation_id"),
                    "result": payload.get("result"),
                },
            )

    @staticmethod
    def _event_id(mapped: str, payload: dict[str, Any]) -> str:
        if payload.get("event_id"):
            return str(payload["event_id"])
        if payload.get("verification_id") and mapped in {
            EventType.VERIFICATION_STARTED.value,
            EventType.VERIFICATION_PASSED.value,
            EventType.VERIFICATION_FAILED.value,
        }:
            return f"{payload['verification_id']}:{mapped}"
        return uuid4().hex

    @staticmethod
    def _status(mapped: str, payload: dict[str, Any]) -> str:
        if mapped in _FAILED_EVENTS or (
            mapped == EventType.MODEL_RESPONSE_COMPLETED.value
            and payload.get("finish_reason") == "error"
        ):
            return "failed"
        if mapped in _COMPLETED_EVENTS:
            return "completed"
        return "running"


__all__ = ["ExecutionEventBridge"]
