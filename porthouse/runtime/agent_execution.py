"""AgentExecution for the durable Agent runtime."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from porthouse.runtime.agent_execution_outcomes import (
    fail_loop_guard,
    suspend_for_action,
)
from porthouse.runtime.agent_finalization import finalize_agent_result
from porthouse.runtime.agent_terminal import AgentTerminalMixin
from porthouse.runtime.context import (
    ActionApprovalRequiredError,
    ActionOutcomeUnknownError,
    AgentLoopExhaustedError,
    AgentLoopStalledError,
    CancellationToken,
    PlannerLoopExhaustedError,
    RunBudgetExceededError,
    VerificationFailedError,
)
from porthouse.runtime.execution_context import (
    AgentCallContextRequest,
    prepare_agent_call_context,
)
from porthouse.runtime.models import (
    AgentEvent,
    AgentOptions,
    AgentResult,
    AgentUsage,
    EventType,
    RunStatus,
    utc_now,
)
from porthouse.runtime.structured import StructuredOutputError, parse_structured_output
from porthouse.runtime.tracking import append_trace_event_async
from porthouse.runtime.verification import verify_output


class AgentExecutionMixin(AgentTerminalMixin):
    async def _execute_agent_record(
        self,
        record: Any,
        cancellation: CancellationToken,
    ) -> AgentResult:
        options = AgentOptions.from_dict(record.options)
        started_at = utc_now()
        await self._start_agent_record(record, options, cancellation, started_at)
        try:
            return await self._run_agent_record(
                record, options, cancellation, started_at
            )
        except TimeoutError:
            return await self._finish_error(
                record.run_id,
                RunStatus.TIMED_OUT,
                EventType.RUN_TIMED_OUT,
                "run timed out",
                started_at,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
        except asyncio.CancelledError:
            await self._finish_error(
                record.run_id,
                RunStatus.CANCELLED,
                EventType.RUN_CANCELLED,
                cancellation.reason or "run cancelled",
                started_at,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            raise
        except (ActionOutcomeUnknownError, ActionApprovalRequiredError) as exc:
            return await suspend_for_action(self, record, cancellation, started_at, exc)
        except AgentLoopExhaustedError as exc:
            return await fail_loop_guard(
                self, record, started_at, exc, stop_reason="loop_exhausted"
            )
        except PlannerLoopExhaustedError as exc:
            return await fail_loop_guard(
                self, record, started_at, exc, stop_reason="max_replans_exhausted"
            )
        except AgentLoopStalledError as exc:
            return await fail_loop_guard(self, record, started_at, exc, stop_reason="loop_stalled")
        except RunBudgetExceededError as exc:
            return await self._finish_error(
                record.run_id,
                RunStatus.FAILED,
                EventType.RUN_FAILED,
                str(exc),
                started_at,
                stop_reason="budget_exceeded",
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
        except StructuredOutputError as exc:
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.VERIFICATION_FAILED.value,
                    data={"method": "output_schema", "error": str(exc)},
                )
            )
            return await self._finish_error(
                record.run_id,
                RunStatus.FAILED,
                EventType.RUN_FAILED,
                str(exc),
                started_at,
                stop_reason="structured_output_error",
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
        except VerificationFailedError as exc:
            schema_only = bool(exc.failures) and all(
                item.get("type") == "schema" for item in exc.failures
            )
            return await self._finish_error(
                record.run_id,
                RunStatus.FAILED,
                EventType.RUN_FAILED,
                str(exc),
                started_at,
                stop_reason="structured_output_error" if schema_only else "verification_failed",
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
        except Exception as exc:
            return await self._finish_error(
                record.run_id,
                RunStatus.FAILED,
                EventType.RUN_FAILED,
                str(exc),
                started_at,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )

    async def _run_agent_record(
        self,
        record: Any,
        options: AgentOptions,
        cancellation: CancellationToken,
        started_at: str,
    ) -> AgentResult:
        (
            execution_prompt,
            selected_tools,
            execution_metadata,
            coordinator_usage,
        ) = await self._prepare_execution(record, options, cancellation)
        if execution_prompt is None:
            current = await asyncio.to_thread(
                self.stores.runs.get_runtime_run, record.run_id
            )
            current_status = (
                RunStatus(current.status) if current is not None else RunStatus.WAITING_INPUT
            )
            return AgentResult(
                run_id=record.run_id,
                status=current_status,
                stop_reason=(
                    "delegated_to_graph"
                    if current_status == RunStatus.QUEUED
                    else current_status.value
                ),
                usage=coordinator_usage,
                started_at=started_at,
            )
        content, tools_used, usage = await self._call_agent(
            run_id=record.run_id,
            task_id=None,
            prompt=execution_prompt,
            user_id=record.user_id,
            session_id=options.session_id,
            agent_id=record.agent_id,
            channel=options.channel,
            chat_id=options.chat_id,
            model=options.model,
            system_prompt=options.system_prompt,
            output_schema=options.output_schema,
            timeout_seconds=options.timeout_seconds,
            max_turns=options.max_turns,
            max_input_tokens=options.max_input_tokens,
            max_output_tokens=options.max_output_tokens,
            max_cost_usd=options.max_cost_usd,
            permission_mode=options.permission_mode,
            allowed_tools=selected_tools,
            disallowed_tools=options.disallowed_tools,
            cancellation=cancellation,
            sender_id=options.sender_id or record.user_id,
            media=options.media,
            metadata=execution_metadata,
            verification_policy=options.verification_policy,
            max_repairs=options.max_repairs,
            run_lease_version=record.lease_version,
        )
        usage.add(coordinator_usage)
        await self._ensure_run_owned(
            record.run_id, cancellation, lease_version=record.lease_version
        )
        structured_output = (
            parse_structured_output(content, options.output_schema)
            if options.output_schema
            else None
        )
        return await finalize_agent_result(
            self,
            record=record,
            cancellation=cancellation,
            content=content,
            structured_output=structured_output,
            usage=usage,
            tools_used=tools_used,
            started_at=started_at,
        )

    async def _start_agent_record(
        self,
        record: Any,
        options: AgentOptions,
        cancellation: CancellationToken,
        started_at: str,
    ) -> None:
        claimed = await asyncio.to_thread(
            self.stores.runs.update_runtime_run,
            record.run_id,
            status="running",
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        if not claimed:
            # The run was cancelled or reached a terminal state between the
            # claim and execution start. Abort before any model/tool call;
            # the fenced terminal commit is a no-op when another worker won.
            await self._finish_error(
                record.run_id,
                RunStatus.CANCELLED,
                EventType.RUN_CANCELLED,
                record.cancel_reason or "run was cancelled before execution started",
                started_at,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            cancellation.cancel("run was cancelled before execution started")
            raise asyncio.CancelledError(cancellation.reason)
        await self._log(
            record.run_id,
            "run.started",
            "Agent run claimed",
            data={"lease_version": record.lease_version},
        )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.RUN_CLAIMED.value,
                status=RunStatus.RUNNING.value,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
                data={
                    "lease_version": record.lease_version,
                    **self._run_claim_details.pop(record.run_id, {}),
                },
            )
        )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PHASE_STARTED.value,
                phase="planning",
                data={"name": "planning"},
            )
        )
        if record.lease_version > 1:
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.LEASE_TAKEOVER.value,
                    worker_id=self.worker_id,
                    lease_version=record.lease_version,
                    data={"lease_version": record.lease_version},
                )
            )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.RUN_STARTED.value,
                status=RunStatus.RUNNING.value,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
                data={"session_id": options.session_id, "kind": "agent"},
            )
        )

    async def _call_agent(
        self,
        *,
        run_id: str,
        task_id: str | None,
        prompt: str,
        user_id: str,
        session_id: str,
        agent_id: str,
        agent_revision_id: str | None = None,
        channel: str,
        chat_id: str,
        model: str | None,
        system_prompt: str | None,
        output_schema: dict[str, Any] | None,
        timeout_seconds: float,
        max_turns: int | None,
        max_input_tokens: int | None,
        max_output_tokens: int | None,
        max_cost_usd: float | None,
        permission_mode: str,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        cancellation: CancellationToken,
        sender_id: str | None = None,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        verification_policy: dict[str, Any] | None = None,
        max_repairs: int | None = None,
        run_lease_version: int | None = None,
        task_lease_version: int | None = None,
        turn_scope: str = "execution",
    ) -> tuple[str | None, list[str], AgentUsage]:
        context, event_bridge = await prepare_agent_call_context(
            self,
            AgentCallContextRequest(
                run_id=run_id,
                task_id=task_id,
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                agent_revision_id=agent_revision_id,
                channel=channel,
                chat_id=chat_id,
                model=model,
                system_prompt=system_prompt,
                output_schema=output_schema,
                max_turns=max_turns,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
                max_cost_usd=max_cost_usd,
                permission_mode=permission_mode,
                allowed_tools=tuple(allowed_tools),
                disallowed_tools=tuple(disallowed_tools),
                cancellation=cancellation,
                metadata=dict(metadata or {}),
                verification_policy=dict(verification_policy or {}),
                max_repairs=max_repairs,
                run_lease_version=run_lease_version,
                task_lease_version=task_lease_version,
                turn_scope=turn_scope,
            ),
        )
        request_id = context.request_id or f"req_{run_id}"
        tracker_id = context.tracker_id or request_id
        execution_span_id = context.parent_span_id or f"span_exec_{run_id}"
        conversation_key = context.session_key
        try:
            await event_bridge.observe(
                self.stores.observability.start_execution_span,
                span_id=execution_span_id,
                trace_id=tracker_id,
                run_id=run_id,
                task_id=task_id,
                span_kind="agent",
                name="agent.execute",
                worker_id=self.worker_id,
                attributes={"agent_id": agent_id, "model": model, "channel": channel},
            )
            await append_trace_event_async(
                store=self.stores.traces,
                tracker_id=tracker_id,
                request_id=request_id,
                parent_request_id=context.parent_request_id,
                user_id=user_id,
                run_id=run_id,
                transport="runtime",
                direction="internal",
                operation="agent.execute",
                stage="request",
                status="running",
                data={"agent_id": agent_id, "channel": channel, "task_id": task_id},
            )
            agent = await self._resolve_execution_agent(run_id, agent_id, agent_revision_id)
            if agent is None:
                raise ValueError(f"agent not found: {agent_id}")
            revision = getattr(agent, "agent_revision", None)
            revision_policy = dict(getattr(revision, "output_policy", {}) or {})
            context = replace(
                context,
                verification_policy={**revision_policy, **context.verification_policy},
            )
            async with asyncio.timeout(max(0.001, timeout_seconds)):
                content = await agent.process_direct(
                    content=prompt,
                    session_key=conversation_key,
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id or user_id,
                    media=media,
                    metadata=metadata,
                    execution_stream_callback=event_bridge.handle,
                    run_context=context,
                )
                verification = await verify_output(
                    context,
                    content,
                    turn_id=None,
                    attempt=None,
                    event_callback=event_bridge.handle,
                )
                if not verification.passed:
                    raise VerificationFailedError(verification.failures, verification.attempt)
            await append_trace_event_async(
                store=self.stores.traces,
                tracker_id=tracker_id,
                request_id=request_id,
                parent_request_id=context.parent_request_id,
                user_id=user_id,
                run_id=run_id,
                transport="runtime",
                direction="internal",
                operation="agent.execute",
                stage="response",
                status="completed",
                data={"task_id": task_id, "content_length": len(content or "")},
            )
            await event_bridge.observe(
                self.stores.observability.finish_execution_span,
                execution_span_id,
                status="completed",
                attributes={"content_length": len(content or "")},
            )
            return (
                content,
                list(dict.fromkeys(event_bridge.tools_used)),
                event_bridge.usage,
            )
        except BaseException as exc:
            await event_bridge.observe(
                self.stores.observability.finish_execution_span,
                execution_span_id,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            await append_trace_event_async(
                store=self.stores.traces,
                tracker_id=tracker_id,
                request_id=request_id,
                parent_request_id=context.parent_request_id,
                user_id=user_id,
                run_id=run_id,
                transport="runtime",
                direction="internal",
                operation="agent.execute",
                stage="error",
                status="failed",
                data={"task_id": task_id, "error_type": type(exc).__name__, "message": str(exc)},
            )
            raise
