"""Single invocation boundary for all model-selected tools."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4

from porthouse.capabilities.reconciliation import OperationReconciliationCoordinator
from porthouse.capabilities.tool_adapter import ToolCapabilityAdapter, ToolInvocationError
from porthouse.domain.capabilities import (
    CapabilityError,
    CapabilityInvocation,
    CapabilityMetrics,
    CapabilityResult,
    InvocationStatus,
)
from porthouse.runtime.action_identity import durable_action_id, payload_hash
from porthouse.runtime.approval_policy import (
    ApprovalPolicy,
    approval_input_preview,
    capability_approval_policy,
)
from porthouse.runtime.context import (
    ActionApprovalRequiredError,
    ActionOutcomeUnknownError,
    ToolExecutionContext,
)
from porthouse.runtime.permissions import permission_engine
from porthouse.utils.permissions import missing_permissions


class CapabilityDispatcher:
    def __init__(self, store: Any | None) -> None:
        self.store = store
        self.reconciliations = (
            OperationReconciliationCoordinator(store)
            if store is not None
            and hasattr(store, "ensure_operation_reconciliation")
            else None
        )

    async def invoke_tool(
        self,
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        *,
        context: ToolExecutionContext,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> CapabilityResult:
        action_id = self._resolve_action_id(adapter, inputs, context)
        side_effect = str(adapter.definition.side_effect or "unknown").strip().lower()
        requires_durable_action = side_effect not in {"none", "read"}
        if requires_durable_action and action_id is None:
            return CapabilityResult.failed(
                f"inv_{tool_call_id or 'unfrozen'}",
                code="DURABLE_ACTION_REQUIRED",
                message=(
                    f"Side-effecting capability '{adapter.definition.ref.capability_id}' "
                    "requires a Runtime-frozen turn/action identity"
                ),
            )
        invocation_id = (
            f"inv_{action_id}" if action_id else f"inv_{tool_call_id or uuid4().hex}"
        )
        idempotency_key = (
            f"action:{action_id}" if action_id else f"tool:{tool_call_id or invocation_id}"
        )
        execution_context = replace(
            context,
            action_id=action_id,
            idempotency_key=idempotency_key,
        )
        invocation = CapabilityInvocation(
            capability=adapter.definition.ref,
            user_id=execution_context.user_id,
            agent_id=execution_context.agent_id,
            session_id=execution_context.session_id or execution_context.session_key,
            run_id=execution_context.run_id,
            task_id=execution_context.task_id,
            trace_id=(
                execution_context.tracker_id
                or execution_context.request_id
                or execution_context.run_id
            ),
            input=inputs,
            timeout_seconds=adapter.definition.timeout_seconds,
            idempotency_key=idempotency_key,
            invocation_id=invocation_id,
            permission_mode=execution_context.permission_mode,
        )
        stored = None
        persist = self.store is not None and await asyncio.to_thread(
            self.store.get_runtime_run, execution_context.run_id
        ) is not None
        if requires_durable_action and not persist:
            return CapabilityResult.failed(
                invocation_id,
                code="DURABLE_RUNTIME_REQUIRED",
                message="Side-effecting capabilities require a durable PostgreSQL Run",
            )
        persist_action = bool(
            persist
            and action_id
            and all(
                hasattr(self.store, method)
                for method in (
                    "create_action_intent",
                    "get_action_observation",
                    "claim_action_intent",
                    "record_action_observation",
                )
            )
        )
        if persist_action:
            approval_policy = await self._prepare_action(
                adapter,
                inputs,
                execution_context,
                action_id=action_id,
                invocation_id=invocation_id,
                idempotency_key=idempotency_key,
            )
            recovered = await self._recover_action_result(action_id)
            if recovered is not None:
                if not recovered.terminal:
                    reconciled = await self._reconcile_action(
                        adapter,
                        execution_context,
                        action_id=action_id,
                        invocation_id=recovered.invocation_id,
                        idempotency_key=idempotency_key,
                        operation=recovered.operation,
                        required_role=approval_policy.required_role,
                    )
                    if reconciled is not None:
                        return reconciled
                    raise ActionOutcomeUnknownError(action_id, recovered.invocation_id)
                return recovered
            worker_id = execution_context.worker_id or f"agent:{execution_context.agent_id}"
            if approval_policy.required:
                claimed = await self._claim_or_wait_for_approval(
                    action_id, worker_id=worker_id
                )
            else:
                claimed = await asyncio.to_thread(
                    self.store.claim_action_intent, action_id, worker_id=worker_id
                )
            if not claimed:
                recovered = await self._recover_invocation_result(
                    action_id=action_id,
                    invocation_id=invocation_id,
                    run_id=execution_context.run_id,
                )
                if recovered is not None:
                    if not recovered.terminal:
                        reconciled = await self._reconcile_action(
                            adapter,
                            execution_context,
                            action_id=action_id,
                            invocation_id=recovered.invocation_id,
                            idempotency_key=idempotency_key,
                            operation=recovered.operation,
                            required_role=approval_policy.required_role,
                        )
                        if reconciled is not None:
                            return reconciled
                        raise ActionOutcomeUnknownError(action_id, recovered.invocation_id)
                    return recovered
                reconciled = await self._reconcile_action(
                    adapter,
                    execution_context,
                    action_id=action_id,
                    invocation_id=invocation_id,
                    idempotency_key=idempotency_key,
                    operation=None,
                    required_role=approval_policy.required_role,
                )
                if reconciled is not None:
                    return reconciled
                raise ActionOutcomeUnknownError(action_id, invocation_id)

        result: CapabilityResult | None = None
        if persist:
            stored, created = await asyncio.to_thread(
                self.store.create_capability_invocation, invocation
            )
            if not created:
                if stored.result:
                    result = capability_result_from_dict(stored.result)
                elif persist_action and action_id:
                    raise ActionOutcomeUnknownError(action_id, stored.invocation_id)
                else:
                    return CapabilityResult(
                        invocation_id=stored.invocation_id,
                        status=InvocationStatus.ACCEPTED,
                        summary="能力调用正在执行",
                        operation={"run_id": stored.run_id, "task_id": stored.task_id},
                    )
            else:
                started = await asyncio.to_thread(
                    self.store.start_capability_invocation,
                    invocation.invocation_id,
                    worker_id=(
                        execution_context.worker_id
                        or f"agent:{execution_context.agent_id}"
                    ),
                )
                if not started:
                    result = CapabilityResult.failed(
                        invocation.invocation_id,
                        code="INVOCATION_CLAIM_FAILED",
                        message="能力调用未能取得执行权",
                        retryable=True,
                    )

        if result is None:
            started_at = time.monotonic()
            result = await self._execute(
                adapter,
                invocation,
                execution_context,
                inputs,
                kwargs,
                started_at,
            )
        if persist and (stored is None or stored.result is None):
            await asyncio.to_thread(
                self.store.finish_capability_invocation,
                invocation.invocation_id,
                status=result.status.value,
                result=result.to_dict(),
                error=result.error.to_dict() if result.error else None,
            )
        if persist_action and action_id:
            await self._record_action_result(
                action_id=action_id,
                run_id=execution_context.run_id,
                result=result,
            )
            if not result.terminal:
                await self._ensure_reconciliation(
                    adapter,
                    execution_context,
                    action_id=action_id,
                    invocation_id=invocation_id,
                    idempotency_key=idempotency_key,
                    operation=result.operation,
                    required_role=approval_policy.required_role,
                )
                raise ActionOutcomeUnknownError(action_id, invocation_id)
        return result

    async def _ensure_reconciliation(
        self,
        adapter: ToolCapabilityAdapter,
        context: ToolExecutionContext,
        **kwargs: Any,
    ) -> Any | None:
        if self.reconciliations is None:
            return None
        return await self.reconciliations.ensure(adapter, context, **kwargs)

    async def _reconcile_action(
        self,
        adapter: ToolCapabilityAdapter,
        context: ToolExecutionContext,
        **kwargs: Any,
    ) -> CapabilityResult | None:
        if self.reconciliations is None:
            return None
        return await self.reconciliations.reconcile(adapter, context, **kwargs)

    @staticmethod
    def _resolve_action_id(
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        context: ToolExecutionContext,
    ) -> str | None:
        if context.turn_index is None or context.action_index is None:
            return None
        return durable_action_id(
            run_id=context.run_id,
            task_id=context.task_id,
            turn_index=context.turn_index,
            action_index=context.action_index,
            capability_ref=adapter.definition.ref,
            inputs=inputs,
        )

    async def _prepare_action(
        self,
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        context: ToolExecutionContext,
        *,
        action_id: str,
        invocation_id: str,
        idempotency_key: str,
    ) -> ApprovalPolicy:
        definition = adapter.definition
        side_effect = str(definition.side_effect or "unknown")
        policy = capability_approval_policy(definition)
        await asyncio.to_thread(
            self.store.create_action_intent,
            action_id=action_id,
            turn_id=context.turn_id,
            run_id=context.run_id,
            task_id=context.task_id,
            turn_index=context.turn_index,
            action_index=context.action_index,
            capability_ref=definition.ref.to_dict(),
            input=inputs,
            input_hash=payload_hash(inputs),
            side_effect=side_effect,
            idempotent=definition.idempotent,
            retryable=definition.retryable,
            risk=policy.risk,
            approval_policy=policy.to_dict(),
            idempotency_key=idempotency_key,
            invocation_id=invocation_id,
        )
        if policy.required:
            await asyncio.to_thread(
                self.store.create_approval_request,
                approval_id=f"apr_{action_id}",
                run_id=context.run_id,
                action_id=action_id,
                user_id=context.user_id,
                capability_ref=definition.ref.to_dict(),
                input_hash=payload_hash(inputs),
                input_preview=approval_input_preview(
                    inputs, definition.data_classification
                ),
                risk=policy.risk,
                data_classification=definition.data_classification,
                required_role=policy.required_role,
                requested_by=context.agent_id,
                expires_in_seconds=86_400,
            )
        return policy

    async def _claim_or_wait_for_approval(
        self, action_id: str, *, worker_id: str
    ) -> bool:
        approval = await asyncio.to_thread(self.store.get_action_approval, action_id)
        if approval is None:
            raise RuntimeError(f"approval request missing for Action: {action_id}")
        if approval.status == "pending":
            raise ActionApprovalRequiredError(
                approval.approval_id, action_id, approval.required_role
            )
        if approval.status == "approved":
            return await asyncio.to_thread(
                self.store.claim_approved_action, action_id, worker_id=worker_id
            )
        if approval.status in {"rejected", "changes_requested", "revoked", "expired"}:
            raise RuntimeError(f"Action approval is {approval.status}: {action_id}")
        return False

    async def _recover_action_result(self, action_id: str) -> CapabilityResult | None:
        observation = await asyncio.to_thread(self.store.get_action_observation, action_id)
        if observation is None or observation.result is None:
            return None
        return capability_result_from_dict(observation.result)

    async def _recover_invocation_result(
        self,
        *,
        action_id: str,
        invocation_id: str,
        run_id: str,
    ) -> CapabilityResult | None:
        getter = getattr(self.store, "get_capability_invocation", None)
        invocation = (
            await asyncio.to_thread(getter, invocation_id) if getter is not None else None
        )
        if invocation is None or invocation.result is None:
            return None
        result = capability_result_from_dict(invocation.result)
        await self._record_action_result(action_id=action_id, run_id=run_id, result=result)
        return result

    async def _record_action_result(
        self,
        *,
        action_id: str,
        run_id: str,
        result: CapabilityResult,
    ) -> None:
        await asyncio.to_thread(
            self.store.record_action_observation,
            action_id=action_id,
            run_id=run_id,
            invocation_id=result.invocation_id,
            status=result.status.value,
            result=result.to_dict(),
            error=result.error.to_dict() if result.error else None,
            operation=result.operation,
            reconciliation_status="confirmed" if result.terminal else "pending",
        )

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
                status=output.status,
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
