"""User-scoped control use cases for external operation reconciliation."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import AuthorizationError, ConflictError, NotFoundError
from joyhousebot.domain.capabilities import CapabilityResult
from joyhousebot.runtime.models import AgentEvent, EventType


class ReconciliationService:
    def __init__(self, runtime: Any, runs: Any, store: Any) -> None:
        self.runtime = runtime
        self.runs = runs
        self.store = store

    async def list(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_run_operation_reconciliations,
            run_id,
            expected_user_id=context.user_id,
        )

    async def resolve(
        self,
        context: RequestContext,
        run_id: str,
        reconciliation_id: str,
        *,
        resolution: str,
        summary: str | None = None,
        data: dict[str, Any] | None = None,
        error_code: str | None = None,
        note: str | None = None,
    ) -> tuple[Any, Any]:
        await self.runs.get(context, run_id)
        record = await asyncio.to_thread(
            self.store.get_operation_reconciliation,
            reconciliation_id,
            expected_user_id=context.user_id,
        )
        if record is None or record.run_id != run_id:
            raise NotFoundError("operation reconciliation not found")
        if record.required_role == "operator" and not context.principal.can(
            "operations.resolve.operator"
        ):
            raise AuthorizationError("operator resolution is required for this operation")
        if record.status != "manual_required":
            raise ConflictError("operation reconciliation does not require manual resolution")
        action = await asyncio.to_thread(
            self.store.get_action_intent, record.action_id
        )
        if resolution == "retry":
            saved = await asyncio.to_thread(
                self.store.retry_operation_reconciliation,
                reconciliation_id,
                run_id=run_id,
                user_id=context.user_id,
                actor_id=context.principal.subject,
            )
        else:
            result = self._manual_result(
                record.invocation_id,
                resolution=resolution,
                summary=summary,
                data=data or {},
                error_code=error_code,
                note=note,
            )
            saved = await asyncio.to_thread(
                self.store.complete_operation_reconciliation,
                reconciliation_id,
                run_id=run_id,
                user_id=context.user_id,
                result=result.to_dict(),
                operation=record.operation,
                resolution_source="manual",
                resolved_by=context.principal.subject,
            )
        if saved is None:
            raise ConflictError("operation reconciliation is no longer resolvable")
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.OPERATION_RECONCILIATION_RESOLVED.value,
                status=saved.status,
                data={
                    "reconciliation_id": reconciliation_id,
                    "action_id": saved.action_id,
                    "resolution": resolution,
                    "resolved_by": context.principal.subject,
                },
            )
        )
        await asyncio.to_thread(self.store.notify_work, run_id)
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                task_id=(action.task_id if action is not None else None),
                type=(
                    EventType.TASK_QUEUED.value
                    if action is not None and action.task_id
                    else EventType.RUN_QUEUED.value
                ),
                status="queued",
                data={"reason": "operation_reconciled", "reconciliation_id": reconciliation_id},
            )
        )
        return saved, await self.runs.get(context, run_id)

    @staticmethod
    def _manual_result(
        invocation_id: str,
        *,
        resolution: str,
        summary: str | None,
        data: dict[str, Any],
        error_code: str | None,
        note: str | None,
    ) -> CapabilityResult:
        if resolution == "confirm_succeeded":
            return CapabilityResult.succeeded(
                invocation_id,
                summary=summary or note or "外部操作已由人工确认完成",
                data=data,
            )
        if resolution != "confirm_failed":
            raise ValueError("invalid operation resolution")
        return CapabilityResult.failed(
            invocation_id,
            code=error_code or "EXTERNAL_OPERATION_FAILED",
            message=summary or note or "外部操作已由人工确认失败",
        )
