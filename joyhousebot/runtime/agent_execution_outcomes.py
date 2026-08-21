"""Non-terminal suspension and loop-guard outcomes for Agent execution."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.runtime.context import (
    ActionApprovalRequiredError,
    ActionOutcomeUnknownError,
    CancellationToken,
)
from joyhousebot.runtime.models import AgentEvent, AgentResult, EventType, RunStatus, utc_now


async def suspend_for_action(
    runtime: Any,
    record: Any,
    cancellation: CancellationToken,
    started_at: str,
    exc: ActionOutcomeUnknownError | ActionApprovalRequiredError,
) -> AgentResult:
    if isinstance(exc, ActionApprovalRequiredError):
        return await _suspend_for_approval(runtime, record, cancellation, started_at, exc)
    reconciliation = await asyncio.to_thread(
        runtime.stores.reconciliations.get_action_reconciliation, exc.action_id
    )
    waiting_result = {
        "stop_reason": "waiting_external",
        "action_id": exc.action_id,
        "invocation_id": exc.invocation_id,
    }
    if reconciliation is not None:
        waiting_result["reconciliation_id"] = reconciliation.reconciliation_id
        transitioned = await asyncio.to_thread(
            runtime.stores.runs.suspend_run_for_reconciliation,
            run_id=record.run_id,
            reconciliation_id=reconciliation.reconciliation_id,
            action_id=exc.action_id,
            invocation_id=exc.invocation_id,
            worker_id=runtime.worker_id,
            lease_version=record.lease_version,
        )
    else:
        transitioned = await asyncio.to_thread(
            runtime.stores.runs.update_runtime_run,
            record.run_id,
            status=RunStatus.WAITING_EXTERNAL.value,
            result=waiting_result,
            worker_id=runtime.worker_id,
            lease_version=record.lease_version,
        )
    if not transitioned:
        cancellation.cancel("run state changed while waiting for reconciliation")
        raise asyncio.CancelledError(cancellation.reason)
    if reconciliation is not None:
        await runtime.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.OPERATION_RECONCILIATION_REQUESTED.value,
                status=reconciliation.status,
                data={
                    "reconciliation_id": reconciliation.reconciliation_id,
                    "action_id": exc.action_id,
                    "required_role": reconciliation.required_role,
                },
            )
        )
    await runtime.events.publish(
        AgentEvent(
            run_id=record.run_id,
            type=EventType.RUN_WAITING_EXTERNAL.value,
            status=RunStatus.WAITING_EXTERNAL.value,
            data={
                "reason": "action_outcome_unknown",
                "action_id": exc.action_id,
                "invocation_id": exc.invocation_id,
                "reconciliation_id": waiting_result.get("reconciliation_id"),
                "reconciliation_status": getattr(reconciliation, "status", None),
                "next_action": "reconcile capability operation before resuming",
            },
        )
    )
    await runtime._log(
        record.run_id,
        EventType.RUN_WAITING_EXTERNAL.value,
        str(exc),
        data=waiting_result,
    )
    return AgentResult(
        run_id=record.run_id,
        status=RunStatus.WAITING_EXTERNAL,
        stop_reason="waiting_external",
        error=str(exc),
        started_at=started_at,
        finished_at=utc_now(),
    )


async def _suspend_for_approval(
    runtime: Any,
    record: Any,
    cancellation: CancellationToken,
    started_at: str,
    exc: ActionApprovalRequiredError,
) -> AgentResult:
    transitioned = await asyncio.to_thread(
        runtime.stores.runs.suspend_run_for_approval,
        run_id=record.run_id,
        approval_id=exc.approval_id,
        action_id=exc.action_id,
        worker_id=runtime.worker_id,
        lease_version=record.lease_version,
    )
    if not transitioned:
        cancellation.cancel("run state changed while waiting for approval")
        raise asyncio.CancelledError(cancellation.reason)
    data = {
        "approval_id": exc.approval_id,
        "action_id": exc.action_id,
        "required_role": exc.required_role,
        "waiting_on": exc.approval_id,
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=record.run_id,
            type=EventType.APPROVAL_REQUESTED.value,
            status="pending",
            data=data,
        )
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=record.run_id,
            type=EventType.RUN_WAITING_APPROVAL.value,
            status=RunStatus.WAITING_APPROVAL.value,
            data={**data, "next_action": "review approval request"},
        )
    )
    await runtime._log(
        record.run_id,
        EventType.RUN_WAITING_APPROVAL.value,
        str(exc),
        data=data,
    )
    return AgentResult(
        run_id=record.run_id,
        status=RunStatus.WAITING_APPROVAL,
        stop_reason="waiting_approval",
        error=str(exc),
        started_at=started_at,
        finished_at=utc_now(),
    )


async def fail_loop_guard(
    runtime: Any,
    record: Any,
    started_at: str,
    exc: RuntimeError,
    *,
    stop_reason: str,
) -> AgentResult:
    return await runtime._finish_error(
        record.run_id,
        RunStatus.FAILED,
        EventType.RUN_FAILED,
        str(exc),
        started_at,
        stop_reason=stop_reason,
        worker_id=runtime.worker_id,
        lease_version=record.lease_version,
    )
