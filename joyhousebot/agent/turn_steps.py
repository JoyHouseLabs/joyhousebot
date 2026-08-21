"""Model and capability execution steps used by the durable turn loop."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from joyhousebot.agent.context_budget import allocate_context
from joyhousebot.agent.durable_loop import guard_repeated_actions, response_from_durable_payload
from joyhousebot.agent.tool_execution import build_tool_execution_batches
from joyhousebot.agent.turn_state import TurnLoopState
from joyhousebot.capabilities.dispatcher import capability_result_prompt
from joyhousebot.providers.base import LLMResponse
from joyhousebot.runtime.artifact_materialization import materialize_capability_artifacts
from joyhousebot.runtime.context import ActionOutcomeUnknownError, RunContext

StreamCallback = Callable[[str], Awaitable[None]] | None
ExecutionCallback = Callable[[str, dict], Awaitable[None]] | None

DEFAULT_AFTER_TOOL_RESULTS_PROMPT = (
    "Summarize the tool results briefly for the user (1-4 sentences). "
    "If the task is done, give the outcome; if more steps are needed, state the next action only."
)


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    tools: list[dict[str, Any]]
    durable_turn: Any
    created: bool


async def prepare_turn(
    owner: Any,
    state: TurnLoopState,
    *,
    context: RunContext,
    event_callback: ExecutionCallback,
) -> PreparedTurn:
    prepared = allocate_context(
        base_candidates=list(context.context_candidates),
        base_sources=list(context.context_sources),
        dynamic_messages=state.messages[state.base_message_count :],
        tools=owner.capabilities.get_tool_definitions(context.for_tools()),
        budget_tokens=context.context_budget_tokens,
    )
    state.messages = prepared.messages
    state.base_message_count = prepared.base_message_count
    turn_id = _turn_id(state)
    durable_turn, created = await state.journal.begin(
        turn_id=turn_id,
        turn_index=state.iteration,
        model=state.active_model,
        messages=state.messages,
        tools=prepared.tools,
        manifest_entries=prepared.entries,
    )
    manifest_event = state.journal.context_event()
    if manifest_event and event_callback:
        await event_callback("context_built", manifest_event)
    if state.journal.enabled and event_callback:
        await event_callback(
            "turn_started",
            {
                "turn_id": turn_id,
                "iteration": state.iteration,
                "recovered": not created,
            },
        )
    return PreparedTurn(tools=prepared.tools, durable_turn=durable_turn, created=created)


async def resolve_turn_response(
    owner: Any,
    state: TurnLoopState,
    prepared: PreparedTurn,
    *,
    stream_callback: StreamCallback,
    event_callback: ExecutionCallback,
) -> tuple[LLMResponse, str]:
    turn_id = _turn_id(state)
    durable_turn = prepared.durable_turn
    if durable_turn and durable_turn.response is not None:
        response = response_from_durable_payload(durable_turn.response or {})
        used_model = durable_turn.model or state.active_model
        if event_callback:
            await event_callback(
                "turn_recovered",
                {
                    "turn_id": turn_id,
                    "iteration": state.iteration,
                    "model": used_model,
                    "status": durable_turn.status,
                },
            )
        return response, used_model
    if event_callback:
        await event_callback(
            "model_request_start",
            {
                "turn_id": turn_id,
                "iteration": state.iteration,
                "model": state.active_model,
                "message_count": len(state.messages),
            },
        )
        await event_callback(
            "thinking_start", {"turn_id": turn_id, "iteration": state.iteration}
        )

    async def stream(content: str) -> None:
        if stream_callback:
            await stream_callback(content)
        if event_callback:
            await event_callback("llm_delta", {"content": content, "turn_id": turn_id})

    async def lifecycle(kind: str, payload: dict) -> None:
        if event_callback:
            await event_callback(
                kind,
                {**payload, "turn_id": turn_id, "iteration": state.iteration},
            )

    use_stream = bool(stream_callback or event_callback) and hasattr(
        owner.provider, "chat_stream"
    )
    response, used_model = await owner._call_provider_with_fallback(
        messages=state.messages,
        tools=prepared.tools,
        primary_model=state.active_model,
        stream_callback=stream if use_stream else None,
        allow_stream=use_stream,
        lifecycle_callback=lifecycle if event_callback else None,
        turn_id=turn_id,
    )
    await state.journal.record_response(turn_id, model=used_model, response=response)
    if event_callback:
        await event_callback(
            "thinking_end", {"turn_id": turn_id, "iteration": state.iteration}
        )
        await event_callback(
            "model_response_end",
            {
                "turn_id": turn_id,
                "iteration": state.iteration,
                "model": used_model,
                "finish_reason": response.finish_reason,
                "has_tool_calls": response.has_tool_calls,
                "tool_call_count": len(response.tool_calls),
                "duration_ms": int((time.monotonic() - state.turn_started_at) * 1000),
            },
        )
    return response, used_model


class ToolTurnExecutor:
    def __init__(
        self,
        owner: Any,
        state: TurnLoopState,
        *,
        context: RunContext,
        event_callback: ExecutionCallback,
    ) -> None:
        self.owner = owner
        self.state = state
        self.context = context
        self.event_callback = event_callback
        messages_config = getattr(owner.config, "messages", None) if owner.config else None
        self.suppress_errors = bool(
            messages_config and getattr(messages_config, "suppress_tool_errors", False)
        )
        configured_prompt = getattr(messages_config, "after_tool_results_prompt", None)
        self.follow_up_prompt = (
            str(configured_prompt or "").strip() or DEFAULT_AFTER_TOOL_RESULTS_PROMPT
        )

    async def execute(self, response: LLMResponse) -> None:
        state = self.state
        turn_id = _turn_id(state)
        state.previous_action_signature = await guard_repeated_actions(
            state.journal,
            response,
            state.previous_action_signature,
            turn_id=turn_id,
            turn_index=state.iteration,
            event_callback=self.event_callback,
        )
        tool_call_dicts = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in response.tool_calls
        ]
        state.messages = self.owner.context.add_assistant_message(
            state.messages,
            response.content,
            tool_call_dicts,
            reasoning_content=response.reasoning_content,
            reasoning_blocks=response.reasoning_blocks,
        )
        agent_policy = (
            dict(self.owner.agent_revision.model_policy)
            if getattr(self.owner, "agent_revision", None) is not None
            else {}
        )
        scenario_policy = dict(
            (self.context.metadata or {}).get("scenario_execution_policy") or {}
        )
        capability_policy_for = getattr(
            self.owner.capabilities,
            "get_tool_invocation_policy",
            lambda _name: {
                "mode": "sequential",
                "max_concurrent": 1,
                "idempotent": False,
                "side_effect": "unknown",
            },
        )
        batches = build_tool_execution_batches(
            response.tool_calls,
            agent_policy=agent_policy,
            scenario_execution_policy=scenario_policy,
            capability_policy_for=capability_policy_for,
        )
        for batch in batches:
            calls = [response.tool_calls[index] for index in batch.indices]
            self._record_tools(calls)
            completed = await self._execute_batch(
                calls, indices=batch.indices, parallel=batch.parallel
            )
            for tool_call, tool_name, result in completed:
                state.messages = self.owner.context.add_tool_result(
                    state.messages, tool_call.id, tool_name, result
                )
        state.messages.append({"role": "user", "content": self.follow_up_prompt})
        await state.journal.finish(
            turn_id,
            status="completed",
            stop_reason="actions_observed",
        )
        if self.event_callback:
            await self.event_callback(
                "turn_completed",
                {
                    "turn_id": turn_id,
                    "iteration": state.iteration,
                    "stop_reason": "actions_observed",
                },
            )

    def _record_tools(self, calls: list[Any]) -> None:
        for tool_call in calls:
            tool_name = str(getattr(tool_call, "name", "") or "").strip()
            if tool_name:
                self.state.tools_used.append(tool_name)

    async def _execute_batch(
        self,
        calls: list[Any],
        *,
        indices: tuple[int, ...],
        parallel: bool,
    ) -> list[tuple[Any, str, str]]:
        if parallel:
            return list(
                await asyncio.gather(
                    *(
                        self._execute_call(
                            tool_call,
                            action_index=action_index,
                            batch_size=len(calls),
                            parallel=True,
                        )
                        for action_index, tool_call in zip(indices, calls)
                    )
                )
            )
        return [
            await self._execute_call(
                tool_call,
                action_index=action_index,
                batch_size=1,
                parallel=False,
            )
            for action_index, tool_call in zip(indices, calls)
        ]

    async def _execute_call(
        self,
        tool_call: Any,
        *,
        action_index: int,
        batch_size: int,
        parallel: bool,
    ) -> tuple[Any, str, str]:
        tool_name = (
            (tool_call.name or "").strip() if isinstance(tool_call.name, str) else ""
        )
        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        event = {
            "tool": tool_name or "unknown",
            "tool_call_id": tool_call.id,
            "turn_id": _turn_id(self.state),
            "span_id": uuid.uuid4().hex,
            "batch_size": batch_size,
            "execution_mode": "parallel" if parallel else "sequential",
        }
        started_at = time.monotonic()
        if not tool_name:
            result = "Error: invalid tool call (missing name or arguments)."
            logger.warning(
                "Tool call with empty name; returning error result to keep message sync"
            )
            await self._emit("tool_requested", {**event, "args": tool_args})
            await self._emit("tool_start", {**event, "args": tool_args})
            await self._emit(
                "tool_end",
                {
                    **event,
                    "ok": False,
                    "error_code": "invalid_tool_call",
                    "error": result,
                    "result": result,
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
            return tool_call, tool_call.name or "", result
        safe_args = json.dumps(tool_args, ensure_ascii=False)[:200]
        safe_args = safe_args.replace("\n", "\\n").replace("\r", "\\r")
        logger.debug(f"Tool call: {tool_name}({safe_args})")
        await self._emit("tool_requested", {**event, "args": tool_args})
        await self._emit("tool_start", {**event, "args": tool_args})

        async def progress(kind: str, payload: dict) -> None:
            await self._emit(
                kind,
                {**payload, **event, "tool": payload.get("tool") or tool_name},
            )

        try:
            capability_result = await self.owner.capabilities.invoke_tool(
                tool_name,
                tool_args,
                context=self.context.for_tools(
                    turn_id=_turn_id(self.state),
                    turn_index=self.state.iteration,
                    action_index=action_index,
                ),
                tool_call_id=tool_call.id,
                execution_stream_callback=progress,
            )
        except ActionOutcomeUnknownError as exc:
            await self._emit_unknown_outcome(event, exc, started_at)
            raise
        except asyncio.CancelledError:
            await self._emit_interrupted(event, started_at)
            raise
        result = capability_result_prompt(capability_result)
        if capability_result.ok and capability_result.artifacts:
            await asyncio.to_thread(
                materialize_capability_artifacts,
                self.owner.runtime_store,
                run_id=self.context.run_id,
                task_id=self.context.task_id,
                agent_id=self.context.agent_id,
                capability_result=capability_result,
                capability_id=tool_name,
            )
        await self._emit_result(event, capability_result, result, started_at)
        if self.suppress_errors and not capability_result.ok:
            logger.debug(f"Tool {tool_name} error (suppressed for user): {result[:300]}")
            result = "Error: Tool execution failed."
        preview = (result[:500] + "...") if len(result) > 500 else result
        logger.debug(f"Tool {tool_name} result (preview): {preview}")
        return tool_call, tool_name, result

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.event_callback:
            await self.event_callback(kind, payload)

    async def _emit_unknown_outcome(
        self, event: dict[str, Any], exc: ActionOutcomeUnknownError, started_at: float
    ) -> None:
        await self._emit(
            "tool_end",
            {
                **event,
                "invocation_id": exc.invocation_id,
                "action_id": exc.action_id,
                "ok": False,
                "error_code": "ACTION_OUTCOME_UNKNOWN",
                "error": str(exc),
                "result": "Action outcome requires reconciliation",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            },
        )

    async def _emit_interrupted(self, event: dict[str, Any], started_at: float) -> None:
        await self._emit(
            "tool_end",
            {
                **event,
                "ok": False,
                "error_code": "interrupted",
                "error": "Tool execution interrupted",
                "result": "Error: Tool execution interrupted",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            },
        )

    async def _emit_result(
        self,
        event: dict[str, Any],
        capability_result: Any,
        result: str,
        started_at: float,
    ) -> None:
        await self._emit(
            "tool_end",
            {
                **event,
                "invocation_id": capability_result.invocation_id,
                "ok": capability_result.ok,
                "result": result,
                "error_code": (
                    capability_result.error.code if capability_result.error else None
                ),
                "error": (
                    capability_result.error.message if capability_result.error else None
                ),
                "capability_result": capability_result.to_dict(),
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            },
        )


def _turn_id(state: TurnLoopState) -> str:
    if state.current_turn_id is None:
        raise RuntimeError("turn has not started")
    return state.current_turn_id
