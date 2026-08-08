"""Durable reconciliation for capability operations that outlive one Worker call."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.capabilities.tool_adapter import ToolCapabilityAdapter
from joyhousebot.contracts import OperationReconciliationResult
from joyhousebot.domain.capabilities import (
    CapabilityError,
    CapabilityResult,
    InvocationStatus,
)
from joyhousebot.runtime.context import ToolExecutionContext


class OperationReconciliationCoordinator:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def ensure(
        self,
        adapter: ToolCapabilityAdapter,
        context: ToolExecutionContext,
        *,
        action_id: str,
        invocation_id: str,
        idempotency_key: str,
        operation: dict[str, Any] | None,
        required_role: str,
    ) -> Any:
        descriptor = dict(operation or {})
        descriptor.setdefault("idempotency_key", idempotency_key)
        descriptor.setdefault("invocation_id", invocation_id)
        descriptor.setdefault("action_id", action_id)
        record, _ = await asyncio.to_thread(
            self.store.ensure_operation_reconciliation,
            reconciliation_id=f"rec_{action_id}",
            run_id=context.run_id,
            action_id=action_id,
            invocation_id=invocation_id,
            user_id=context.user_id,
            capability_ref=adapter.definition.ref.to_dict(),
            idempotency_key=idempotency_key,
            operation=descriptor,
            status="pending" if adapter.supports_reconciliation else "manual_required",
            required_role=required_role,
        )
        return record

    async def reconcile(
        self,
        adapter: ToolCapabilityAdapter,
        context: ToolExecutionContext,
        *,
        action_id: str,
        invocation_id: str,
        idempotency_key: str,
        operation: dict[str, Any] | None,
        required_role: str,
    ) -> CapabilityResult | None:
        record = await self.ensure(
            adapter,
            context,
            action_id=action_id,
            invocation_id=invocation_id,
            idempotency_key=idempotency_key,
            operation=operation,
            required_role=required_role,
        )
        if record.result and record.status in {"succeeded", "failed"}:
            return _capability_result(record.result)
        if not adapter.supports_reconciliation or record.status == "manual_required":
            return None
        worker_id = context.worker_id or f"agent:{context.agent_id}"
        claimed = await asyncio.to_thread(
            self.store.claim_operation_reconciliation,
            record.reconciliation_id,
            worker_id=worker_id,
            lease_seconds=30,
        )
        if claimed is None:
            return None
        try:
            context.cancellation.raise_if_cancelled()
            outcome = await asyncio.wait_for(
                adapter.reconcile_operation(claimed.operation, tool_context=context),
                timeout=max(1, int(adapter.definition.timeout_seconds)),
            )
            context.cancellation.raise_if_cancelled()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._defer(
                claimed,
                worker_id,
                retry_after_seconds=_backoff(claimed.attempt_count),
                error={
                    "code": "RECONCILIATION_TIMEOUT",
                    "message": "capability reconciliation timed out",
                },
            )
            return None
        except Exception as exc:
            await self._defer(
                claimed,
                worker_id,
                retry_after_seconds=_backoff(claimed.attempt_count),
                error={"code": "RECONCILIATION_EXCEPTION", "message": str(exc)},
            )
            return None
        if outcome.status == "pending":
            await self._defer(
                claimed,
                worker_id,
                retry_after_seconds=(
                    outcome.retry_after_seconds
                    if outcome.retry_after_seconds is not None
                    else _backoff(claimed.attempt_count)
                ),
                error={"code": "OPERATION_PENDING", "message": outcome.summary},
                operation=outcome.operation,
            )
            return None
        if outcome.status == "unknown":
            await self._defer(
                claimed,
                worker_id,
                retry_after_seconds=0,
                error={"code": "OPERATION_UNKNOWN", "message": outcome.summary},
                manual_required=True,
                operation=outcome.operation,
            )
            return None
        result = _result_from_outcome(invocation_id, outcome)
        saved = await asyncio.to_thread(
            self.store.complete_operation_reconciliation,
            claimed.reconciliation_id,
            result=result.to_dict(),
            operation=outcome.operation or claimed.operation,
            resolution_source="provider",
            worker_id=worker_id,
            lease_version=claimed.lease_version,
        )
        return _capability_result(saved.result) if saved and saved.result else None

    async def _defer(
        self,
        record: Any,
        worker_id: str,
        *,
        retry_after_seconds: int,
        error: dict[str, Any],
        manual_required: bool = False,
        operation: dict[str, Any] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self.store.defer_operation_reconciliation,
            record.reconciliation_id,
            worker_id=worker_id,
            lease_version=record.lease_version,
            retry_after_seconds=retry_after_seconds,
            last_error=error,
            manual_required=manual_required or record.attempt_count >= record.max_attempts,
            operation=operation,
        )


def _backoff(attempt: int) -> int:
    return min(300, max(1, 2 ** min(max(0, attempt - 1), 8)))


def _result_from_outcome(
    invocation_id: str, outcome: OperationReconciliationResult
) -> CapabilityResult:
    if outcome.status == "failed":
        error = outcome.error or {}
        return CapabilityResult.failed(
            invocation_id,
            code=str(error.get("code") or "EXTERNAL_OPERATION_FAILED"),
            message=str(error.get("message") or outcome.summary or "external operation failed"),
            retryable=bool(error.get("retryable")),
            details=dict(error.get("details") or {}),
        )
    output = outcome.output
    data = output if isinstance(output, dict) else {"output": output}
    return CapabilityResult.succeeded(
        invocation_id,
        summary=outcome.summary or "外部操作已完成",
        data=data,
        artifacts=tuple(item.to_dict() for item in outcome.artifacts),
    )


def _capability_result(value: dict[str, Any]) -> CapabilityResult:
    error = value.get("error")
    return CapabilityResult(
        invocation_id=str(value["invocation_id"]),
        status=InvocationStatus(str(value["status"])),
        summary=str(value.get("summary") or ""),
        data=dict(value.get("data") or {}),
        artifacts=tuple(value.get("artifacts") or ()),
        operation=dict(value["operation"]) if value.get("operation") else None,
        error=(
            CapabilityError(
                code=str(error.get("code") or "CAPABILITY_FAILED"),
                message=str(error.get("message") or "capability failed"),
                retryable=bool(error.get("retryable")),
                retry_after_seconds=error.get("retry_after_seconds"),
                details=dict(error.get("details") or {}),
            )
            if error
            else None
        ),
    )
