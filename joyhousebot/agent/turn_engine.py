"""TurnEngine responsibilities for the shared Agent engine."""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from loguru import logger

from joyhousebot.capabilities.dispatcher import capability_result_prompt
from joyhousebot.providers.base import LLMResponse
from joyhousebot.runtime.context import (
    RunBudgetExceededError,
    RunContext,
    bind_run_context,
    get_current_run_context,
)
from joyhousebot.utils.exceptions import (
    LLMError,
    sanitize_error_message,
)

if TYPE_CHECKING:
    pass

# Default user message sent after tool results when messages.after_tool_results_prompt is not set
_default_after_tool_results_prompt = (
    "Summarize the tool results briefly for the user (1-4 sentences). "
    "If the task is done, give the outcome; if more steps are needed, state the next action only."
)


class TurnEngineMixin:
    def _resolve_memory_scope_key(
        self,
        session_key: str,
        sender_id: str = "",
        metadata: dict | None = None,
        run_context: RunContext | None = None,
    ) -> str | None:
        """Resolve memory scope key from config. Returns None for shared, else scope_key (session or user)."""
        if not self.config:
            return None
        retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
        if not retrieval:
            return None
        scope = getattr(retrieval, "memory_scope", "user") or "user"
        if scope == "shared":
            return None
        if scope == "session":
            return session_key
        if scope == "user":
            if run_context is not None and run_context.user_id:
                return f"user:{run_context.user_id}:agent:{run_context.agent_id}"
            from_id = getattr(retrieval, "memory_user_id_from", "sender_id") or "sender_id"
            meta_key = getattr(retrieval, "memory_user_id_metadata_key", "user_id") or "user_id"
            meta = metadata or {}
            if from_id == "metadata":
                candidate = (
                    (meta.get(meta_key) or "").strip()
                    if isinstance(meta.get(meta_key), str)
                    else ""
                )
                # Channel senders control message metadata, so a metadata
                # user id is only trusted when it matches the authenticated
                # run identity; otherwise a sender could read/write another
                # user's memory scope. Fall back to the sender id.
                if candidate and run_context is not None and candidate == run_context.user_id:
                    user_id = candidate
                else:
                    user_id = (sender_id or "").strip()
            else:
                user_id = (sender_id or "").strip()
            if not user_id:
                user_id = session_key.split(":", 1)[-1] if ":" in session_key else session_key
            channel = session_key.split(":", 1)[0] if ":" in session_key else "unknown"
            return f"{channel}:{user_id}"
        return None

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
        """
        Run the agent iteration loop.

        Args:
            initial_messages: Starting messages for the LLM conversation.
            stream_callback: If set and provider supports chat_stream, called with each content delta.
            execution_stream_callback: If set, called with (event_type, payload) for llm_delta, tool_start, tool_output, tool_end, final.
            check_abort_requested: If set, called at start of each iteration with current run_id; when True, loop breaks and returns (None, tools_used, True, None).

        Returns:
            Tuple of (final_content, list_of_tools_used, aborted, last_response for usage persistence).
        """
        messages = initial_messages
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
            current_turn_id = uuid.uuid4().hex
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
                tools=self.capabilities.get_tool_definitions(run_context.for_tools()),
                primary_model=active_model,
                stream_callback=_stream_cb if use_stream else None,
                allow_stream=use_stream,
                lifecycle_callback=(
                    (
                        lambda kind, payload: execution_stream_callback(
                            kind, {**payload, "turn_id": current_turn_id, "iteration": iteration}
                        )
                    )
                    if execution_stream_callback
                    else None
                ),
                turn_id=current_turn_id,
            )
            last_response = response
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
                for tool_call in response.tool_calls:
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
                                    "ok": False,
                                    "error_code": "invalid_tool_call",
                                    "error": result,
                                    "result": result,
                                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                                },
                            )
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name or "", result
                        )
                        continue

                    tools_used.append(tool_name)
                    args_str = json.dumps(tool_args, ensure_ascii=False)
                    # Tool arguments are model/user-influenced and noisy: keep
                    # them at debug level and escape newlines to prevent log
                    # line forgery.
                    safe_args = args_str[:200].replace("\n", "\\n").replace("\r", "\\r")
                    logger.debug(f"Tool call: {tool_name}({safe_args})")
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_requested",
                            {
                                "tool": tool_name,
                                "args": tool_args,
                                "tool_call_id": tool_call.id,
                                "turn_id": current_turn_id,
                                "span_id": tool_span_id,
                            },
                        )
                        await execution_stream_callback(
                            "tool_start",
                            {
                                "tool": tool_name,
                                "args": tool_args,
                                "tool_call_id": tool_call.id,
                                "turn_id": current_turn_id,
                                "span_id": tool_span_id,
                            },
                        )

                    async def _tool_progress(kind: str, payload: dict) -> None:
                        if execution_stream_callback:
                            await execution_stream_callback(
                                kind,
                                {
                                    **payload,
                                    "tool": payload.get("tool") or tool_name,
                                    "tool_call_id": tool_call.id,
                                    "turn_id": current_turn_id,
                                    "span_id": tool_span_id,
                                },
                            )

                    try:
                        capability_result = await self.capabilities.invoke_tool(
                            tool_name,
                            tool_args,
                            context=run_context.for_tools(),
                            tool_call_id=tool_call.id,
                            execution_stream_callback=_tool_progress,
                        )
                    except asyncio.CancelledError:
                        if execution_stream_callback:
                            await execution_stream_callback(
                                "tool_end",
                                {
                                    "tool": tool_name,
                                    "tool_call_id": tool_call.id,
                                    "turn_id": current_turn_id,
                                    "span_id": tool_span_id,
                                    "ok": False,
                                    "error_code": "interrupted",
                                    "error": "Tool execution interrupted",
                                    "result": "Error: Tool execution interrupted",
                                    "duration_ms": int((time.monotonic() - tool_started_at) * 1000),
                                },
                            )
                        raise
                    result = capability_result_prompt(capability_result)
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_end",
                            {
                                "tool": tool_name,
                                "tool_call_id": tool_call.id,
                                "turn_id": current_turn_id,
                                "span_id": tool_span_id,
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
            else:
                final_content = response.content
                break

        if execution_stream_callback and final_content is not None:
            await execution_stream_callback("final", {"content": final_content})

        return final_content, tools_used, False, last_response

    async def close_mcp(self) -> None:
        """Close MCP connections and the model HTTP connection pool."""
        async with self._mcp_connect_lock:
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    pass  # MCP SDK cancel scope cleanup is noisy but harmless
                self._mcp_stack = None
            self._mcp_connected = False
        await self.provider.close()

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
