"""AgentExecution for the durable Agent runtime."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from joyhousebot.runtime.agent_terminal import AgentTerminalMixin
from joyhousebot.runtime.context import CancellationToken, RunBudgetExceededError, RunContext
from joyhousebot.runtime.models import (
    AgentEvent,
    AgentOptions,
    AgentResult,
    AgentUsage,
    EventType,
    EventVisibility,
    RunStatus,
    utc_now,
)
from joyhousebot.runtime.structured import StructuredOutputError, parse_structured_output
from joyhousebot.runtime.tracking import (
    append_trace_event_async,
    ensure_tracking_ids,
)


class AgentExecutionMixin(AgentTerminalMixin):
    async def _execute_agent_record(
        self,
        record: Any,
        cancellation: CancellationToken,
    ) -> AgentResult:
        options = AgentOptions.from_dict(record.options)
        started_at = utc_now()
        await asyncio.to_thread(
            self.store.update_runtime_run,
            record.run_id,
            status="running",
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
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
        try:
            (
                execution_prompt,
                selected_tools,
                execution_metadata,
                coordinator_usage,
            ) = await self._prepare_execution(record, options, cancellation)
            if execution_prompt is None:
                current = await asyncio.to_thread(self.store.get_runtime_run, record.run_id)
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
            )
            usage.input_tokens += coordinator_usage.input_tokens
            usage.output_tokens += coordinator_usage.output_tokens
            usage.total_tokens += coordinator_usage.total_tokens
            usage.cost_usd = float(usage.cost_usd or 0) + float(coordinator_usage.cost_usd or 0)
            await self._ensure_run_owned(
                record.run_id, cancellation, lease_version=record.lease_version
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.VERIFICATION_STARTED.value,
                    data={
                        "method": "output_schema" if options.output_schema else "completion",
                    },
                )
            )
            structured_output = (
                parse_structured_output(content, options.output_schema)
                if options.output_schema
                else None
            )
            result = AgentResult(
                run_id=record.run_id,
                status=RunStatus.COMPLETED,
                content=content,
                structured_output=structured_output,
                stop_reason="completed",
                usage=usage,
                tools_used=tools_used,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await asyncio.to_thread(
                self.store.add_runtime_artifact,
                artifact_id=f"{record.run_id}:final",
                run_id=record.run_id,
                name="final-output",
                media_type="application/json" if structured_output is not None else "text/plain",
                content=structured_output if structured_output is not None else content,
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.ARTIFACT_CREATED.value,
                    data={
                        "artifact_id": f"{record.run_id}:final",
                        "name": "final-output",
                        "media_type": (
                            "application/json" if structured_output is not None else "text/plain"
                        ),
                    },
                )
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.VERIFICATION_PASSED.value,
                    data={
                        "method": "output_schema" if options.output_schema else "completion",
                        "verified": bool(options.output_schema),
                        "note": (
                            "output matches requested schema"
                            if options.output_schema
                            else "execution completed; no explicit output schema was requested"
                        ),
                    },
                )
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.PHASE_COMPLETED.value,
                    phase="finalizing",
                    data={"name": "execution"},
                )
            )
            persisted = await self._commit_terminal(
                record.run_id,
                status=RunStatus.COMPLETED,
                event_type=EventType.RUN_COMPLETED,
                result=result.to_dict(),
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            if persisted is None:
                cancellation.cancel("run reached a terminal state on another worker")
                raise asyncio.CancelledError(cancellation.reason)
            await self._log(
                record.run_id,
                "run.completed",
                "Agent run completed",
                data={"usage": usage.to_dict(), "tools_used": tools_used},
            )
            return result
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

    async def _call_agent(
        self,
        *,
        run_id: str,
        task_id: str | None,
        prompt: str,
        user_id: str,
        session_id: str,
        agent_id: str,
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
    ) -> tuple[str | None, list[str], AgentUsage]:
        tools_used: list[str] = []
        usage = AgentUsage(model=model)

        async def _observe(method: str, *args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.to_thread(getattr(self.store, method), *args, **kwargs)
            except Exception:
                # Diagnostics must not turn a healthy user task into a failure.
                return None

        async def _execution_event(event_type: str, payload: dict[str, Any]) -> None:
            mapping = {
                "llm_delta": EventType.MESSAGE_DELTA.value,
                "model_request_start": EventType.MODEL_REQUEST_STARTED.value,
                "thinking_start": EventType.MODEL_THINKING_STARTED.value,
                "thinking_end": EventType.MODEL_THINKING_COMPLETED.value,
                "reasoning_delta": EventType.MODEL_REASONING_DELTA.value,
                "model_response_end": EventType.MODEL_RESPONSE_COMPLETED.value,
                "provider_fallback": EventType.MODEL_PROVIDER_FALLBACK.value,
                "model_retry": EventType.MODEL_RETRY_SCHEDULED.value,
                "cache_hit": EventType.MODEL_CACHE_HIT.value,
                "tool_requested": EventType.CAPABILITY_REQUESTED.value,
                "permission_requested": EventType.CAPABILITY_PERMISSION_REQUESTED.value,
                "permission_resolved": EventType.CAPABILITY_PERMISSION_RESOLVED.value,
                "tool_start": EventType.CAPABILITY_STARTED.value,
                "tool_output": EventType.CAPABILITY_PROGRESS.value,
                "tool_end": EventType.CAPABILITY_COMPLETED.value,
                "final": EventType.MESSAGE_COMPLETED.value,
                "usage": EventType.USAGE_UPDATED.value,
            }
            if event_type == "tool_start" and payload.get("tool"):
                tools_used.append(str(payload["tool"]))
            payload = dict(payload)
            if payload.get("tool") and not payload.get("capability_id"):
                payload["capability_id"] = payload["tool"]
            if event_type == "tool_end" and payload.get("ok") is False:
                mapped = EventType.CAPABILITY_FAILED.value
            else:
                mapped = mapping.get(event_type, event_type)
            if event_type == "tool_start" and payload.get("span_id"):
                await _observe(
                    "start_execution_span",
                    span_id=str(payload["span_id"]),
                    trace_id=tracker_id,
                    parent_span_id=execution_span_id,
                    run_id=run_id,
                    task_id=task_id,
                    turn_id=str(payload.get("turn_id") or "") or None,
                    span_kind="tool",
                    name=str(payload.get("tool") or "capability"),
                    worker_id=self.worker_id,
                    attributes={
                        "tool_call_id": payload.get("tool_call_id"),
                        "arguments": payload.get("args") or {},
                    },
                )
            elif event_type == "tool_end" and payload.get("span_id"):
                await _observe(
                    "finish_execution_span",
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
            if event_type == "usage":
                usage.input_tokens = int(payload.get("input_tokens") or 0)
                usage.output_tokens = int(payload.get("output_tokens") or 0)
                usage.total_tokens = int(payload.get("total_tokens") or 0)
                usage.cost_usd = float(payload.get("cost_usd") or 0.0)
                usage.model = str(payload.get("model") or model or "") or None
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    task_id=task_id,
                    type=mapped,
                    data=payload,
                    turn_id=str(payload.get("turn_id") or "") or None,
                    span_id=str(payload.get("span_id") or "") or None,
                    parent_span_id=str(payload.get("parent_span_id") or "") or None,
                    tool_call_id=str(payload.get("tool_call_id") or "") or None,
                    attempt=(
                        int(payload["attempt"]) if payload.get("attempt") is not None else None
                    ),
                    status=(
                        "failed"
                        if (
                            mapped == EventType.CAPABILITY_FAILED.value
                            or (
                                mapped == EventType.MODEL_RESPONSE_COMPLETED.value
                                and payload.get("finish_reason") == "error"
                            )
                        )
                        else "completed"
                        if mapped
                        in {
                            EventType.CAPABILITY_COMPLETED.value,
                            EventType.MODEL_RESPONSE_COMPLETED.value,
                            EventType.MESSAGE_COMPLETED.value,
                        }
                        else "running"
                    ),
                    visibility=(
                        EventVisibility.PRIVATE.value
                        if mapped == EventType.MODEL_REASONING_DELTA.value
                        else EventVisibility.PUBLIC.value
                    ),
                    worker_id=self.worker_id,
                )
            )
            if event_type not in {"llm_delta", "reasoning_delta"}:
                level = "error" if mapped == EventType.CAPABILITY_FAILED.value else "info"
                await self._log(
                    run_id,
                    mapped,
                    f"Execution event: {mapped}",
                    level=level,
                    task_id=task_id,
                    data=payload,
                )

        runtime_record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        stored_options = dict(runtime_record.options or {}) if runtime_record else {}
        request_id, tracker_id = ensure_tracking_ids(
            request_id=stored_options.get("request_id") or f"req_{run_id}",
            tracker_id=stored_options.get("tracker_id"),
        )
        execution_span_id = f"span_exec_{uuid4().hex}"
        granted_permissions = await self._execution_permissions(run_id, agent_id)
        context = RunContext(
            run_id=run_id,
            task_id=task_id,
            root_run_id=(runtime_record.root_run_id if runtime_record else None) or run_id,
            parent_run_id=runtime_record.parent_run_id if runtime_record else None,
            parent_task_id=runtime_record.parent_task_id if runtime_record else None,
            request_id=request_id,
            tracker_id=tracker_id,
            parent_request_id=stored_options.get("parent_request_id"),
            parent_span_id=execution_span_id,
            trace_store=self.store,
            user_id=user_id,
            agent_id=agent_id,
            session_key=self._conversation_key(user_id, agent_id, session_id),
            session_id=session_id,
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
            allowed_tools=frozenset(allowed_tools),
            disallowed_tools=frozenset(disallowed_tools),
            granted_permissions=granted_permissions,
            cancellation=cancellation,
            worker_id=self.worker_id,
            skill_names=tuple(str(item) for item in (metadata or {}).get("skill_names", [])),
            skill_refs=tuple(
                dict(item) for item in (metadata or {}).get("skill_refs", [])
                if isinstance(item, dict)
            ),
        )
        conversation_key = context.session_key
        try:
            await _observe(
                "start_execution_span",
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
                store=self.store,
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
            agent = await self._resolve_execution_agent(run_id, agent_id)
            if agent is None:
                raise ValueError(f"agent not found: {agent_id}")
            async with asyncio.timeout(max(0.001, timeout_seconds)):
                content = await agent.process_direct(
                    content=prompt,
                    session_key=conversation_key,
                    channel=channel,
                    chat_id=chat_id,
                    sender_id=sender_id or user_id,
                    media=media,
                    metadata=metadata,
                    execution_stream_callback=_execution_event,
                    run_context=context,
                )
            await append_trace_event_async(
                store=self.store,
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
            await _observe(
                "finish_execution_span",
                execution_span_id,
                status="completed",
                attributes={"content_length": len(content or "")},
            )
            return content, list(dict.fromkeys(tools_used)), usage
        except BaseException as exc:
            await _observe(
                "finish_execution_span",
                execution_span_id,
                status="failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            await append_trace_event_async(
                store=self.store,
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
