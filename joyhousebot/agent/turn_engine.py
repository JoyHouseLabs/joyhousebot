"""TurnEngine responsibilities for the shared Agent engine."""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable

from loguru import logger

from joyhousebot.agent.context_budget import allocate_context
from joyhousebot.agent.context_scope import ContextScopeMixin
from joyhousebot.agent.durable_loop import (
    DurableTurnJournal,
    guard_repeated_actions,
    response_from_durable_payload,
)
from joyhousebot.agent.tool_execution import build_tool_execution_batches
from joyhousebot.agent.verification_loop import accept_or_repair_final_response
from joyhousebot.capabilities.dispatcher import capability_result_prompt
from joyhousebot.providers.base import LLMResponse
from joyhousebot.runtime.action_identity import durable_turn_id
from joyhousebot.runtime.artifact_materialization import materialize_capability_artifacts
from joyhousebot.runtime.context import (
    ActionOutcomeUnknownError,
    AgentLoopExhaustedError,
    RunBudgetExceededError,
    RunContext,
    bind_run_context,
    get_current_run_context,
)
from joyhousebot.utils.exceptions import (
    LLMError,
    sanitize_error_message,
)

_default_after_tool_results_prompt = (
    "Summarize the tool results briefly for the user (1-4 sentences). "
    "If the task is done, give the outcome; if more steps are needed, state the next action only."
)


class TurnEngineMixin(ContextScopeMixin):
    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
        run_context: RunContext | None = None,
    ) -> tuple[str | None, list[str], bool, LLMResponse | None]:
        """Bind run-scoped state and execute the provider/tool loop."""
        context = run_context or get_current_run_context()
        if context is None:
            context = RunContext(
                run_id=uuid.uuid4().hex,
                session_key="",
                channel="",
                chat_id="",
                user_id="system",
                agent_id="default",
                session_id="",
            )
        with bind_run_context(context):
            return await self._run_agent_loop_inner(
                initial_messages,
                stream_callback=stream_callback,
                execution_stream_callback=execution_stream_callback,
                check_abort_requested=check_abort_requested,
                run_context=context,
            )

    async def _run_agent_loop_inner(
        self,
        initial_messages: list[dict],
        stream_callback: Callable[[str], Awaitable[None]] | None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None,
        check_abort_requested: Callable[[str], bool] | None,
        run_context: RunContext,
    ) -> tuple[str | None, list[str], bool, LLMResponse | None]:
        """Run provider/tool turns and return content, tools, abort state, and usage."""
        messages = initial_messages
        base_message_count = len(messages)
        iteration = 0
        final_content = None
        last_response: LLMResponse | None = None
        tools_used: list[str] = []
        active_model = run_context.model or self.model
        effective_max_iterations = self.max_iterations
        if run_context.max_turns is not None:
            effective_max_iterations = max(1, min(effective_max_iterations, run_context.max_turns))
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost_usd = 0.0
        previous_action_signature: str | None = None
        repairs_used = 0
        journal = await DurableTurnJournal.open(run_context)

        current_turn_id: str | None = None

        async def _stream_cb(content: str) -> None:
            if stream_callback:
                await stream_callback(content)
            if execution_stream_callback:
                await execution_stream_callback(
                    "llm_delta", {"content": content, "turn_id": current_turn_id}
                )

        while iteration < effective_max_iterations:
            iteration += 1
            current_turn_id = durable_turn_id(
                run_context.run_id,
                run_context.task_id,
                iteration,
                scope=run_context.turn_scope,
            )
            turn_started_at = time.monotonic()

            if run_context.cancellation.is_cancelled:
                return (None, tools_used, True, last_response)

            if check_abort_requested:
                run_id = run_context.run_id
                if run_id and check_abort_requested(run_id):
                    run_context.cancellation.cancel("abort requested")
                    return (None, tools_used, True, None)

            logger.debug(
                f"Calling LLM (iteration {iteration}), model={active_model}, messages={len(messages)}"
            )
            prepared_context = allocate_context(
                base_candidates=list(run_context.context_candidates),
                base_sources=list(run_context.context_sources),
                dynamic_messages=messages[base_message_count:],
                tools=self.capabilities.get_tool_definitions(run_context.for_tools()),
                budget_tokens=run_context.context_budget_tokens,
            )
            messages = prepared_context.messages
            turn_tools = prepared_context.tools
            base_message_count = prepared_context.base_message_count
            durable_turn, created = await journal.begin(
                turn_id=current_turn_id,
                turn_index=iteration,
                model=active_model,
                messages=messages,
                tools=turn_tools,
                manifest_entries=prepared_context.entries,
            )
            manifest_event = journal.context_event()
            if manifest_event and execution_stream_callback:
                await execution_stream_callback("context_built", manifest_event)
            if journal.enabled and execution_stream_callback:
                await execution_stream_callback(
                    "turn_started",
                    {
                        "turn_id": current_turn_id,
                        "iteration": iteration,
                        "recovered": not created,
                    },
                )

            recovered_response = bool(durable_turn and durable_turn.response is not None)
            if recovered_response:
                response = response_from_durable_payload(durable_turn.response or {})
                used_model = durable_turn.model or active_model
                if execution_stream_callback:
                    await execution_stream_callback(
                        "turn_recovered",
                        {
                            "turn_id": current_turn_id,
                            "iteration": iteration,
                            "model": used_model,
                            "status": durable_turn.status,
                        },
                    )
            else:
                if execution_stream_callback:
                    await execution_stream_callback(
                        "model_request_start",
                        {
                            "turn_id": current_turn_id,
                            "iteration": iteration,
                            "model": active_model,
                            "message_count": len(messages),
                        },
                    )
                    await execution_stream_callback(
                        "thinking_start",
                        {"turn_id": current_turn_id, "iteration": iteration},
                    )
                use_stream = (
                    stream_callback is not None or execution_stream_callback is not None
                ) and hasattr(self.provider, "chat_stream")
                response, used_model = await self._call_provider_with_fallback(
                    messages=messages,
                    tools=turn_tools,
                    primary_model=active_model,
                    stream_callback=_stream_cb if use_stream else None,
                    allow_stream=use_stream,
                    lifecycle_callback=(
                        (
                            lambda kind, payload: execution_stream_callback(
                                kind,
                                {
                                    **payload,
                                    "turn_id": current_turn_id,
                                    "iteration": iteration,
                                },
                            )
                        )
                        if execution_stream_callback
                        else None
                    ),
                    turn_id=current_turn_id,
                )
                await journal.record_response(current_turn_id, model=used_model, response=response)
                if execution_stream_callback:
                    await execution_stream_callback(
                        "thinking_end",
                        {"turn_id": current_turn_id, "iteration": iteration},
                    )
                    await execution_stream_callback(
                        "model_response_end",
                        {
                            "turn_id": current_turn_id,
                            "iteration": iteration,
                            "model": used_model,
                            "finish_reason": response.finish_reason,
                            "has_tool_calls": response.has_tool_calls,
                            "tool_call_count": len(response.tool_calls),
                            "duration_ms": int((time.monotonic() - turn_started_at) * 1000),
                        },
                    )
            last_response = response
            usage = response.usage or {}
            total_input_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
            total_output_tokens += int(
                usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            )
            total_cost_usd += float(
                usage.get("cost_usd", usage.get("total_cost", usage.get("cost", 0.0))) or 0.0
            )
            if execution_stream_callback:
                await execution_stream_callback(
                    "usage",
                    {
                        "input_tokens": total_input_tokens,
                        "output_tokens": total_output_tokens,
                        "total_tokens": total_input_tokens + total_output_tokens,
                        "cost_usd": total_cost_usd,
                        "model": used_model,
                        "iteration": iteration,
                        "turn_id": current_turn_id,
                    },
                )
            if (
                run_context.max_input_tokens is not None
                and total_input_tokens > run_context.max_input_tokens
            ):
                raise RunBudgetExceededError("maximum input token budget exceeded")
            if (
                run_context.max_output_tokens is not None
                and total_output_tokens > run_context.max_output_tokens
            ):
                raise RunBudgetExceededError("maximum output token budget exceeded")
            if run_context.max_cost_usd is not None and total_cost_usd > run_context.max_cost_usd:
                raise RunBudgetExceededError("maximum cost budget exceeded")
            active_model = used_model
            logger.debug(
                f"LLM response: has_tool_calls={response.has_tool_calls}, content_len={len(response.content or '')}"
            )

            if response.finish_reason == "error":
                raise LLMError(
                    sanitize_error_message(response.content or "Model provider request failed"),
                    provider=self._resolve_provider_name_for_model(used_model),
                    model=used_model,
                    is_retryable=bool(response.retryable),
                )

            if response.has_tool_calls:
                previous_action_signature = await guard_repeated_actions(
                    journal,
                    response,
                    previous_action_signature,
                    turn_id=current_turn_id,
                    turn_index=iteration,
                    event_callback=execution_stream_callback,
                )
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    reasoning_blocks=response.reasoning_blocks,
                )

                messages_config_loop = (
                    getattr(self.config, "messages", None) if self.config else None
                )
                suppress_tool_errors = bool(
                    messages_config_loop
                    and getattr(messages_config_loop, "suppress_tool_errors", False)
                )
                agent_model_policy = (
                    dict(self.agent_revision.model_policy)
                    if getattr(self, "agent_revision", None) is not None
                    else {}
                )
                scenario_execution_policy = dict(
                    (run_context.metadata or {}).get("scenario_execution_policy") or {}
                )
                capability_policy_for = getattr(
                    self.capabilities,
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
                    agent_policy=agent_model_policy,
                    scenario_execution_policy=scenario_execution_policy,
                    capability_policy_for=capability_policy_for,
                )

                async def _execute_tool_call(
                    tool_call,
                    *,
                    action_index: int,
                    batch_size: int,
                    parallel: bool,
                ):
                    tool_name = (
                        (tool_call.name or "").strip() if isinstance(tool_call.name, str) else ""
                    )
                    tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                    tool_span_id = uuid.uuid4().hex
                    tool_started_at = time.monotonic()
                    if not tool_name:
                        logger.warning(
                            "Tool call with empty name; returning error result to keep message sync"
                        )
                        result = "Error: invalid tool call (missing name or arguments)."
                        if execution_stream_callback:
                            invalid_payload = {
                                "tool": "unknown",
                                "tool_call_id": tool_call.id,
                                "turn_id": current_turn_id,
                                "span_id": tool_span_id,
                                "batch_size": batch_size,
                                "execution_mode": "parallel" if parallel else "sequential",
                            }
                            await execution_stream_callback(
                                "tool_requested", {**invalid_payload, "args": tool_args}
                            )
                            await execution_stream_callback(
                                "tool_start", {**invalid_payload, "args": tool_args}
                            )
                            await execution_stream_callback(
                                "tool_end",
                                {
                                    "tool": tool_name or "unknown",
                                    "tool_call_id": tool_call.id,
                                    "turn_id": current_turn_id,
                                    "span_id": tool_span_id,
                                    "batch_size": batch_size,
                                    "execution_mode": "parallel" if parallel else "sequential",
                                    "ok": False,
                                    "error_code": "invalid_tool_call",
                                    "error": result,
                                    "result": result,
                                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                                },
                            )
                        return tool_call, tool_call.name or "", result

                    args_str = json.dumps(tool_args, ensure_ascii=False)
                    # Tool arguments are model/user-influenced and noisy: keep
                    # them at debug level and escape newlines to prevent log
                    # line forgery.
                    safe_args = args_str[:200].replace("\n", "\\n").replace("\r", "\\r")
                    logger.debug(f"Tool call: {tool_name}({safe_args})")
                    tool_event = {
                        "tool": tool_name,
                        "tool_call_id": tool_call.id,
                        "turn_id": current_turn_id,
                        "span_id": tool_span_id,
                        "batch_size": batch_size,
                        "execution_mode": "parallel" if parallel else "sequential",
                    }
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_requested", {**tool_event, "args": tool_args}
                        )
                        await execution_stream_callback(
                            "tool_start", {**tool_event, "args": tool_args}
                        )

                    async def _tool_progress(kind: str, payload: dict) -> None:
                        if execution_stream_callback:
                            await execution_stream_callback(
                                kind,
                                {
                                    **payload,
                                    **tool_event,
                                    "tool": payload.get("tool") or tool_name,
                                },
                            )

                    try:
                        capability_result = await self.capabilities.invoke_tool(
                            tool_name,
                            tool_args,
                            context=run_context.for_tools(
                                turn_id=current_turn_id,
                                turn_index=iteration,
                                action_index=action_index,
                            ),
                            tool_call_id=tool_call.id,
                            execution_stream_callback=_tool_progress,
                        )
                    except ActionOutcomeUnknownError as exc:
                        if execution_stream_callback:
                            await execution_stream_callback(
                                "tool_end",
                                {
                                    **tool_event,
                                    "invocation_id": exc.invocation_id,
                                    "action_id": exc.action_id,
                                    "ok": False,
                                    "error_code": "ACTION_OUTCOME_UNKNOWN",
                                    "error": str(exc),
                                    "result": "Action outcome requires reconciliation",
                                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                                },
                            )
                        raise
                    except asyncio.CancelledError:
                        if execution_stream_callback:
                            await execution_stream_callback(
                                "tool_end",
                                {
                                    **tool_event,
                                    "ok": False,
                                    "error_code": "interrupted",
                                    "error": "Tool execution interrupted",
                                    "result": "Error: Tool execution interrupted",
                                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                                },
                            )
                        raise
                    result = capability_result_prompt(capability_result)
                    if capability_result.ok and capability_result.artifacts:
                        await asyncio.to_thread(
                            materialize_capability_artifacts,
                            self.runtime_store,
                            run_id=run_context.run_id,
                            task_id=run_context.task_id,
                            agent_id=run_context.agent_id,
                            capability_result=capability_result,
                            capability_id=tool_name,
                        )
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_end",
                            {
                                **tool_event,
                                "invocation_id": capability_result.invocation_id,
                                "ok": capability_result.ok,
                                "result": result,
                                "error_code": (
                                    capability_result.error.code
                                    if capability_result.error
                                    else None
                                ),
                                "error": (
                                    capability_result.error.message
                                    if capability_result.error
                                    else None
                                ),
                                "capability_result": capability_result.to_dict(),
                                "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                            },
                        )
                    if suppress_tool_errors and not capability_result.ok:
                        logger.debug(
                            f"Tool {tool_name} error (suppressed for user): {result[:300]}"
                        )
                        result = "Error: Tool execution failed."

                    preview = (result[:500] + "...") if len(result) > 500 else result
                    logger.debug(f"Tool {tool_name} result (preview): {preview}")
                    return tool_call, tool_name, result

                for batch in batches:
                    calls = [response.tool_calls[index] for index in batch.indices]
                    for tool_call in calls:
                        tool_name = str(getattr(tool_call, "name", "") or "").strip()
                        if tool_name:
                            tools_used.append(tool_name)
                    if batch.parallel:
                        completed = await asyncio.gather(
                            *(
                                _execute_tool_call(
                                    tool_call,
                                    action_index=action_index,
                                    batch_size=len(calls),
                                    parallel=True,
                                )
                                for action_index, tool_call in zip(batch.indices, calls)
                            )
                        )
                    else:
                        completed = [
                            await _execute_tool_call(
                                tool_call,
                                action_index=action_index,
                                batch_size=1,
                                parallel=False,
                            )
                            for action_index, tool_call in zip(batch.indices, calls)
                        ]
                    # Provider protocols require Tool result messages in the
                    # exact order of the original calls, even when their work
                    # completed out of order.
                    for tool_call, tool_name, result in completed:
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_name, result
                        )
                follow_up = _default_after_tool_results_prompt
                if messages_config_loop and getattr(
                    messages_config_loop, "after_tool_results_prompt", None
                ):
                    follow_up = (
                        messages_config_loop.after_tool_results_prompt or ""
                    ).strip() or follow_up
                messages.append({"role": "user", "content": follow_up})
                await journal.finish(
                    current_turn_id,
                    status="completed",
                    stop_reason="actions_observed",
                )
                if execution_stream_callback:
                    await execution_stream_callback(
                        "turn_completed",
                        {
                            "turn_id": current_turn_id,
                            "iteration": iteration,
                            "stop_reason": "actions_observed",
                        },
                    )
            else:
                accepted, repairs_used = await accept_or_repair_final_response(
                    content=response.content,
                    messages=messages,
                    journal=journal,
                    context=run_context,
                    turn_id=current_turn_id,
                    turn_index=iteration,
                    max_turns=effective_max_iterations,
                    repairs_used=repairs_used,
                    event_callback=execution_stream_callback,
                )
                if accepted:
                    final_content = response.content
                    break

        if final_content is None:
            if current_turn_id is not None:
                await journal.finish(
                    current_turn_id,
                    status="exhausted",
                    stop_reason="max_turns",
                )
            if execution_stream_callback:
                await execution_stream_callback(
                    "loop_exhausted",
                    {
                        "turn_id": current_turn_id,
                        "iteration": iteration,
                        "max_turns": effective_max_iterations,
                    },
                )
            raise AgentLoopExhaustedError(effective_max_iterations)

        if execution_stream_callback and final_content is not None:
            await execution_stream_callback("final", {"content": final_content})

        return final_content, tools_used, False, last_response

    async def close_tool_connectors(self) -> None:
        """Close external Tool connector lifecycles and the model client."""
        async with self._tool_connector_lock:
            if self._tool_connector_stack:
                try:
                    await self._tool_connector_stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    pass  # Some connector SDK cancellation scopes are noisy on shutdown.
                self._tool_connector_stack = None
            self._tool_connectors_connected = False
        await self.provider.close()

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
