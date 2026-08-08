"""Single invocation boundary for all model-selected tools."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from joyhousebot.capabilities.tool_adapter import ToolCapabilityAdapter, ToolInvocationError
from joyhousebot.domain.capabilities import (
    CapabilityError,
    CapabilityInvocation,
    CapabilityMetrics,
    CapabilityResult,
    InvocationStatus,
)
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.runtime.permissions import permission_engine
from joyhousebot.utils.permissions import missing_permissions


class CapabilityDispatcher:
    def __init__(self, store: Any | None) -> None:
        self.store = store

    async def invoke_tool(
        self,
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        *,
        context: ToolExecutionContext,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> CapabilityResult:
        invocation_id = f"inv_{tool_call_id}" if tool_call_id else f"inv_{uuid4().hex}"
        invocation = CapabilityInvocation(
            capability=adapter.definition.ref,
            user_id=context.user_id,
            agent_id=context.agent_id,
            session_id=context.session_id or context.session_key,
            run_id=context.run_id,
            task_id=context.task_id,
            trace_id=context.tracker_id or context.request_id or context.run_id,
            input=inputs,
            timeout_seconds=adapter.definition.timeout_seconds,
            idempotency_key=f"tool:{tool_call_id or invocation_id}",
            invocation_id=invocation_id,
            permission_mode=context.permission_mode,
        )
        stored = None
        persist = self.store is not None and await asyncio.to_thread(
            self.store.get_runtime_run, context.run_id
        ) is not None
        if persist:
            stored, created = await asyncio.to_thread(
                self.store.create_capability_invocation, invocation
            )
            if not created:
                if stored.result:
                    return capability_result_from_dict(stored.result)
                return CapabilityResult(
                    invocation_id=stored.invocation_id,
                    status=InvocationStatus.ACCEPTED,
                    summary="能力调用正在执行",
                    operation={"run_id": stored.run_id, "task_id": stored.task_id},
                )
            started = await asyncio.to_thread(
                self.store.start_capability_invocation,
                invocation.invocation_id,
                worker_id=context.worker_id or f"agent:{context.agent_id}",
            )
            if not started:
                return CapabilityResult.failed(
                    invocation.invocation_id,
                    code="INVOCATION_CLAIM_FAILED",
                    message="能力调用未能取得执行权",
                    retryable=True,
                )

        started_at = time.monotonic()
        result = await self._execute(adapter, invocation, context, inputs, kwargs, started_at)
        if persist:
            await asyncio.to_thread(
                self.store.finish_capability_invocation,
                invocation.invocation_id,
                status=result.status.value,
                result=result.to_dict(),
                error=result.error.to_dict() if result.error else None,
            )
        return result

    async def _execute(
        self,
        adapter: ToolCapabilityAdapter,
        invocation: CapabilityInvocation,
        context: ToolExecutionContext,
        inputs: dict[str, Any],
        kwargs: dict[str, Any],
        started_at: float,
    ) -> CapabilityResult:
        try:
            context.cancellation.raise_if_cancelled()
            decision = permission_engine.evaluate(adapter.tool.name, context)
            if not decision.allowed:
                raise ToolInvocationError("PERMISSION_DENIED", decision.reason)
            required = {
                str(item).strip()
                for item in (getattr(adapter.definition, "permissions", ()) or ())
                if str(item).strip()
            }
            missing = missing_permissions(context.granted_permissions, required)
            if missing:
                raise ToolInvocationError(
                    "PERMISSION_DENIED",
                    f"Missing capability permissions: {', '.join(missing)}",
                )
            errors = adapter.tool.validate_params(inputs)
            if errors:
                raise ToolInvocationError("INVALID_PARAMETERS", "; ".join(errors))
            output = await asyncio.wait_for(
                adapter.invoke(inputs, tool_context=context, **kwargs),
                timeout=invocation.timeout_seconds,
            )
            context.cancellation.raise_if_cancelled()
            duration = int((time.monotonic() - started_at) * 1000)
            return CapabilityResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.SUCCEEDED,
                summary=output.summary or f"{adapter.tool.name} 执行完成",
                data=output.data,
                artifacts=output.artifacts,
                operation=output.operation,
                metrics=CapabilityMetrics(duration_ms=duration),
            )
        except ToolInvocationError as exc:
            return CapabilityResult.failed(
                invocation.invocation_id,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                metrics=CapabilityMetrics(
                    duration_ms=int((time.monotonic() - started_at) * 1000)
                ),
            )
        except TimeoutError:
            return CapabilityResult(
                invocation_id=invocation.invocation_id,
                status=InvocationStatus.TIMED_OUT,
                summary=f"{adapter.tool.name} 执行超时",
                metrics=CapabilityMetrics(
                    duration_ms=int((time.monotonic() - started_at) * 1000)
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return CapabilityResult.failed(
                invocation.invocation_id,
                code="CAPABILITY_EXCEPTION",
                message=str(exc),
                metrics=CapabilityMetrics(
                    duration_ms=int((time.monotonic() - started_at) * 1000)
                ),
            )


def capability_result_from_dict(value: dict[str, Any]) -> CapabilityResult:
    error_value = value.get("error")
    metrics_value = value.get("metrics") or {}
    return CapabilityResult(
        schema_version=int(value.get("schema_version") or 1),
        invocation_id=str(value["invocation_id"]),
        status=InvocationStatus(str(value["status"])),
        summary=str(value.get("summary") or ""),
        data=dict(value.get("data") or {}),
        artifacts=tuple(value.get("artifacts") or ()),
        operation=dict(value["operation"]) if value.get("operation") else None,
        error=(
            CapabilityError(
                code=str(error_value.get("code") or "CAPABILITY_FAILED"),
                message=str(error_value.get("message") or "capability failed"),
                retryable=bool(error_value.get("retryable")),
                retry_after_seconds=error_value.get("retry_after_seconds"),
                details=dict(error_value.get("details") or {}),
            )
            if error_value
            else None
        ),
        metrics=CapabilityMetrics(
            duration_ms=int(metrics_value.get("duration_ms") or 0),
            input_tokens=int(metrics_value.get("input_tokens") or 0),
            output_tokens=int(metrics_value.get("output_tokens") or 0),
            cost_usd=float(metrics_value.get("cost_usd") or 0.0),
        ),
    )


def capability_result_prompt(result: CapabilityResult) -> str:
    if result.status == InvocationStatus.SUCCEEDED:
        content = result.data.get("content")
        return str(content) if content is not None else str(result.data)
    if result.status == InvocationStatus.ACCEPTED:
        return f"Accepted: {result.summary}; operation={result.operation or {}}"
    if result.error:
        return f"Error [{result.error.code}]: {result.error.message}"
    return f"Error [{result.status.value}]: {result.summary}"
