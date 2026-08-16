"""Durable journal helpers kept outside the provider/tool turn engine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from porthouse.agent.context_manifest import build_turn_manifest
from porthouse.providers.base import LLMResponse, ToolCallRequest
from porthouse.runtime.action_identity import payload_hash
from porthouse.runtime.context import AgentLoopStalledError, RunContext


def durable_response_payload(response: LLMResponse) -> dict[str, Any]:
    """Persist protocol state required for resume, excluding hidden reasoning."""

    return {
        "content": response.content,
        "tool_calls": [
            {"id": item.id, "name": item.name, "arguments": item.arguments}
            for item in response.tool_calls
        ],
        "finish_reason": response.finish_reason,
        "usage": dict(response.usage or {}),
        "error_kind": response.error_kind,
        "error_code": response.error_code,
        "error_status": response.error_status,
        "retryable": response.retryable,
    }


def response_from_durable_payload(value: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content=value.get("content"),
        tool_calls=[
            ToolCallRequest(
                id=str(item["id"]),
                name=str(item["name"]),
                arguments=dict(item.get("arguments") or {}),
            )
            for item in value.get("tool_calls", [])
        ],
        finish_reason=str(value.get("finish_reason") or "stop"),
        usage=dict(value.get("usage") or {}),
        error_kind=value.get("error_kind"),
        error_code=value.get("error_code"),
        error_status=value.get("error_status"),
        retryable=value.get("retryable"),
    )


@dataclass(slots=True)
class DurableTurnJournal:
    store: Any | None
    context: RunContext
    last_manifest: Any | None = None

    @classmethod
    async def open(cls, context: RunContext) -> "DurableTurnJournal":
        store = context.trace_store
        required = (
            "create_runtime_turn",
            "get_context_manifest_for_turn",
            "record_context_manifest",
            "record_runtime_turn_response",
            "finish_runtime_turn",
        )
        if store is None or not all(hasattr(store, method) for method in required):
            return cls(None, context)
        persisted_run = await asyncio.to_thread(store.get_runtime_run, context.run_id)
        return cls(store if persisted_run is not None else None, context)

    @property
    def enabled(self) -> bool:
        return self.store is not None

    async def begin(
        self,
        *,
        turn_id: str,
        turn_index: int,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        manifest_entries: list[dict[str, Any]] | None = None,
    ) -> tuple[Any | None, bool]:
        if self.store is None:
            return None, False
        turn, created = await asyncio.to_thread(
            self.store.create_runtime_turn,
            turn_id=turn_id,
            run_id=self.context.run_id,
            task_id=self.context.task_id,
            scope=self.context.turn_scope,
            turn_index=turn_index,
            model=model,
            request_hash=payload_hash(messages),
            worker_id=self.context.worker_id,
        )
        if not created and turn.response is not None:
            self.last_manifest = await asyncio.to_thread(
                self.store.get_context_manifest_for_turn,
                turn_id,
                worker_id=self.context.worker_id,
                run_lease_version=self.context.run_lease_version,
                task_lease_version=self.context.task_lease_version,
            )
            if self.last_manifest is not None:
                return turn, created
        manifest = build_turn_manifest(
            self.context,
            turn_id=turn_id,
            turn_index=turn_index,
            messages=messages,
            tools=tools,
            entries=manifest_entries,
        )
        self.last_manifest = await asyncio.to_thread(self.store.record_context_manifest, **manifest)
        if self.last_manifest is None:
            raise RuntimeError(f"context manifest lease lost: {turn_id}")
        return turn, created

    def context_event(self) -> dict[str, Any] | None:
        """Return the redacted event payload for the most recently frozen manifest."""

        manifest = self.last_manifest
        if manifest is None:
            return None
        return {
            "event_id": f"{manifest.manifest_id}:context.built",
            "manifest_id": manifest.manifest_id,
            "turn_id": manifest.turn_id,
            "turn_index": manifest.turn_index,
            "scope": manifest.scope,
            "manifest_hash": manifest.manifest_hash,
            "request_hash": manifest.request_hash,
            "estimated_tokens": manifest.estimated_tokens,
            "excluded_tokens": manifest.excluded_tokens,
            "entry_count": len(manifest.entries),
            "budget_strategy": manifest.budget_strategy,
        }

    async def record_response(self, turn_id: str, *, model: str, response: LLMResponse) -> None:
        if self.store is None:
            return
        if response.finish_reason == "error":
            await self.finish(
                turn_id,
                status="model_error",
                stop_reason="provider_error",
                error={
                    "kind": response.error_kind,
                    "code": response.error_code,
                    "message": response.content,
                },
            )
            return
        saved = await asyncio.to_thread(
            self.store.record_runtime_turn_response,
            turn_id,
            model=model,
            response=durable_response_payload(response),
            usage=dict(response.usage or {}),
            status="action_proposed" if response.has_tool_calls else "model_responded",
        )
        if not saved:
            raise RuntimeError(f"durable turn response conflict: {turn_id}")

    async def finish(self, turn_id: str, *, status: str, **kwargs: Any) -> None:
        if self.store is not None:
            await asyncio.to_thread(
                self.store.finish_runtime_turn, turn_id, status=status, **kwargs
            )


async def guard_repeated_actions(
    journal: DurableTurnJournal,
    response: LLMResponse,
    previous_signature: str | None,
    *,
    turn_id: str,
    turn_index: int,
    event_callback: Callable[[str, dict], Awaitable[None]] | None,
) -> str:
    signature = payload_hash(
        [
            {"name": str(item.name or ""), "arguments": dict(item.arguments or {})}
            for item in response.tool_calls
        ]
    )
    if signature != previous_signature:
        return signature
    await journal.finish(
        turn_id,
        status="failed",
        stop_reason="loop_stalled",
        error={"message": "consecutive turns proposed the same actions"},
    )
    if event_callback:
        await event_callback(
            "loop_stalled",
            {
                "turn_id": turn_id,
                "iteration": turn_index,
                "action_signature": signature,
            },
        )
    raise AgentLoopStalledError(turn_index)
