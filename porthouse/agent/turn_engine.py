"""TurnEngine responsibilities for the shared Agent engine."""

import uuid
from collections.abc import Awaitable, Callable

from loguru import logger

from porthouse.agent.context_scope import ContextScopeMixin
from porthouse.agent.turn_state import TurnLoopState
from porthouse.agent.turn_steps import ToolTurnExecutor, prepare_turn, resolve_turn_response
from porthouse.agent.verification_loop import accept_or_repair_final_response
from porthouse.providers.base import LLMResponse
from porthouse.runtime.context import (
    AgentLoopExhaustedError,
    RunContext,
    bind_run_context,
    get_current_run_context,
)
from porthouse.utils.exceptions import LLMError, sanitize_error_message


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
        """Run the explicit durable model/tool turn pipeline."""
        state = await TurnLoopState.create(
            initial_messages,
            context=run_context,
            default_model=self.model,
            configured_max_iterations=self.max_iterations,
        )
        while state.iteration < state.max_iterations:
            turn_id = state.begin_turn(run_context)
            aborted = self._abort_result(
                state,
                run_context=run_context,
                check_abort_requested=check_abort_requested,
            )
            if aborted is not None:
                return aborted
            logger.debug(
                f"Calling LLM (iteration {state.iteration}), "
                f"model={state.active_model}, messages={len(state.messages)}"
            )
            prepared = await prepare_turn(
                self,
                state,
                context=run_context,
                event_callback=execution_stream_callback,
            )
            response, used_model = await resolve_turn_response(
                self,
                state,
                prepared,
                stream_callback=stream_callback,
                event_callback=execution_stream_callback,
            )
            state.last_response = response
            usage_event = state.usage.record(
                response,
                model=used_model,
                iteration=state.iteration,
                turn_id=turn_id,
            )
            if execution_stream_callback:
                await execution_stream_callback("usage", usage_event)
            state.usage.enforce_budget(run_context)
            state.active_model = used_model
            logger.debug(
                f"LLM response: has_tool_calls={response.has_tool_calls}, "
                f"content_len={len(response.content or '')}"
            )
            if response.finish_reason == "error":
                raise LLMError(
                    sanitize_error_message(
                        response.content or "Model provider request failed"
                    ),
                    provider=self._resolve_provider_name_for_model(used_model),
                    model=used_model,
                    is_retryable=bool(response.retryable),
                )
            if response.has_tool_calls:
                await ToolTurnExecutor(
                    self,
                    state,
                    context=run_context,
                    event_callback=execution_stream_callback,
                ).execute(response)
                continue
            accepted, state.repairs_used = await accept_or_repair_final_response(
                content=response.content,
                messages=state.messages,
                journal=state.journal,
                context=run_context,
                turn_id=turn_id,
                turn_index=state.iteration,
                max_turns=state.max_iterations,
                repairs_used=state.repairs_used,
                event_callback=execution_stream_callback,
            )
            if accepted:
                state.final_content = response.content
                break
        await self._finish_loop(state, execution_stream_callback)
        return state.final_content, state.tools_used, False, state.last_response

    @staticmethod
    def _abort_result(
        state: TurnLoopState,
        *,
        run_context: RunContext,
        check_abort_requested: Callable[[str], bool] | None,
    ) -> tuple[str | None, list[str], bool, LLMResponse | None] | None:
        if run_context.cancellation.is_cancelled:
            return None, state.tools_used, True, state.last_response
        run_id = run_context.run_id
        if check_abort_requested and run_id and check_abort_requested(run_id):
            run_context.cancellation.cancel("abort requested")
            return None, state.tools_used, True, None
        return None

    @staticmethod
    async def _finish_loop(
        state: TurnLoopState,
        event_callback: Callable[[str, dict], Awaitable[None]] | None,
    ) -> None:
        if state.final_content is None:
            if state.current_turn_id is not None:
                await state.journal.finish(
                    state.current_turn_id,
                    status="exhausted",
                    stop_reason="max_turns",
                )
            if event_callback:
                await event_callback(
                    "loop_exhausted",
                    {
                        "turn_id": state.current_turn_id,
                        "iteration": state.iteration,
                        "max_turns": state.max_iterations,
                    },
                )
            raise AgentLoopExhaustedError(state.max_iterations)
        if event_callback:
            await event_callback("final", {"content": state.final_content})

    async def close_tool_connectors(self) -> None:
        """Close external Tool connector lifecycles and the model client."""
        async with self._tool_connector_lock:
            if self._tool_connector_stack:
                try:
                    await self._tool_connector_stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    pass  # Some connector SDK cancellation scopes are noisy on shutdown.
                self._tool_connector_stack = None
            for stack in self._retired_tool_connector_stacks:
                try:
                    await stack.aclose()
                except (RuntimeError, BaseExceptionGroup):
                    pass
            self._retired_tool_connector_stacks.clear()
            self._tool_connectors_connected = False
        await self.provider.close()

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
