"""Single invocation boundary for all model-selected tools."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class _DispatchSetup:
    action_id: str | None
    invocation_id: str
    idempotency_key: str
    context: ToolExecutionContext
    invocation: CapabilityInvocation
    persist: bool
    persist_action: bool


@dataclass(frozen=True)
class _InvocationClaim:
    stored: Any | None = None
    result: CapabilityResult | None = None
    return_immediately: bool = False


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
        setup = await self._prepare_dispatch(
            adapter, inputs, context=context, tool_call_id=tool_call_id
        )
        if isinstance(setup, CapabilityResult):
            return setup

        approval_policy: ApprovalPolicy | None = None
        if setup.persist_action:
            approval_policy, recovered = await self._claim_durable_action(
                adapter, inputs, setup
            )
            if recovered is not None:
                return recovered

        claim = await self._claim_invocation(setup)
        if claim.return_immediately:
            assert claim.result is not None
            return claim.result
        result = claim.result or await self._execute(
            adapter,
            setup.invocation,
            setup.context,
            inputs,
            kwargs,
            time.monotonic(),
        )
        await self._finalize_dispatch(
            adapter,
            setup,
            stored=claim.stored,
            result=result,
            approval_policy=approval_policy,
        )
        return result

    async def _prepare_dispatch(
        self,
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        *,
        context: ToolExecutionContext,
        tool_call_id: str | None,
    ) -> _DispatchSetup | CapabilityResult:
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
        return _DispatchSetup(
            action_id=action_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            context=execution_context,
            invocation=invocation,
            persist=persist,
            persist_action=persist_action,
        )

    async def _claim_durable_action(
        self,
        adapter: ToolCapabilityAdapter,
        inputs: dict[str, Any],
        setup: _DispatchSetup,
    ) -> tuple[ApprovalPolicy, CapabilityResult | None]:
        assert setup.action_id is not None
        policy = await self._prepare_action(
            adapter,
            inputs,
            setup.context,
            action_id=setup.action_id,
            invocation_id=setup.invocation_id,
            idempotency_key=setup.idempotency_key,
        )
        recovered = await self._recover_action_result(setup.action_id)
        if recovered is not None:
            return policy, await self._resolve_recovered_action(
                adapter, setup, recovered, policy
            )
        worker_id = setup.context.worker_id or f"agent:{setup.context.agent_id}"
        claimed = (
            await self._claim_or_wait_for_approval(setup.action_id, worker_id=worker_id)
            if policy.required
            else await asyncio.to_thread(
                self.store.claim_action_intent, setup.action_id, worker_id=worker_id
            )
        )
        if claimed:
            return policy, None
        recovered = await self._recover_invocation_result(
            action_id=setup.action_id,
            invocation_id=setup.invocation_id,
            run_id=setup.context.run_id,
        )
        if recovered is not None:
            return policy, await self._resolve_recovered_action(
                adapter, setup, recovered, policy
            )
        reconciled = await self._reconcile_action(
            adapter,
            setup.context,
            action_id=setup.action_id,
            invocation_id=setup.invocation_id,
            idempotency_key=setup.idempotency_key,
            operation=None,
            required_role=policy.required_role,
        )
        if reconciled is not None:
            return policy, reconciled
        raise ActionOutcomeUnknownError(setup.action_id, setup.invocation_id)

    async def _resolve_recovered_action(
        self,
        adapter: ToolCapabilityAdapter,
        setup: _DispatchSetup,
        recovered: CapabilityResult,
        policy: ApprovalPolicy,
    ) -> CapabilityResult:
        if recovered.terminal:
            return recovered
        assert setup.action_id is not None
        reconciled = await self._reconcile_action(
            adapter,
            setup.context,
            action_id=setup.action_id,
            invocation_id=recovered.invocation_id,
            idempotency_key=setup.idempotency_key,
            operation=recovered.operation,
            required_role=policy.required_role,
        )
        if reconciled is not None:
            return reconciled
        raise ActionOutcomeUnknownError(setup.action_id, recovered.invocation_id)

    async def _claim_invocation(self, setup: _DispatchSetup) -> _InvocationClaim:
        if not setup.persist:
            return _InvocationClaim()
        stored, created = await asyncio.to_thread(
            self.store.create_capability_invocation, setup.invocation
        )
        if not created:
            if stored.result:
                return _InvocationClaim(
                    stored=stored,
                    result=capability_result_from_dict(stored.result),
                )
            if setup.persist_action and setup.action_id:
                raise ActionOutcomeUnknownError(setup.action_id, stored.invocation_id)
            return _InvocationClaim(
                stored=stored,
                result=CapabilityResult(
                    invocation_id=stored.invocation_id,
                    status=InvocationStatus.ACCEPTED,
                    summary="能力调用正在执行",
                    operation={"run_id": stored.run_id, "task_id": stored.task_id},
                ),
                return_immediately=True,
            )
        worker_id = setup.context.worker_id or f"agent:{setup.context.agent_id}"
        started = await asyncio.to_thread(
            self.store.start_capability_invocation,
            setup.invocation.invocation_id,
            worker_id=worker_id,
        )
        if started:
            return _InvocationClaim(stored=stored)
        return _InvocationClaim(
            stored=stored,
            result=CapabilityResult.failed(
                setup.invocation.invocation_id,
                code="INVOCATION_CLAIM_FAILED",
                message="能力调用未能取得执行权",
                retryable=True,
            ),
        )

    async def _finalize_dispatch(
        self,
        adapter: ToolCapabilityAdapter,
        setup: _DispatchSetup,
        *,
        stored: Any | None,
        result: CapabilityResult,
        approval_policy: ApprovalPolicy | None,
    ) -> None:
        if setup.persist and (stored is None or stored.result is None):
            await asyncio.to_thread(
                self.store.finish_capability_invocation,
                setup.invocation.invocation_id,
                status=result.status.value,
                result=result.to_dict(),
                error=result.error.to_dict() if result.error else None,
            )
        if not setup.persist_action or setup.action_id is None:
            return
        assert approval_policy is not None
        await self._record_action_result(
            action_id=setup.action_id,
            run_id=setup.context.run_id,
            result=result,
        )
        if result.terminal:
            return
        await self._ensure_reconciliation(
            adapter,
            setup.context,
            action_id=setup.action_id,
            invocation_id=setup.invocation_id,
            idempotency_key=setup.idempotency_key,
            operation=result.operation,
            required_role=approval_policy.required_role,
        )
        raise ActionOutcomeUnknownError(setup.action_id, setup.invocation_id)

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
