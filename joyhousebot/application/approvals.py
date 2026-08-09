"""User-scoped approval use cases for frozen capability Actions."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import AuthorizationError, ConflictError, NotFoundError
from joyhousebot.runtime.models import AgentEvent, EventType


class ApprovalService:
    def __init__(self, runtime: Any, runs: Any, store: Any) -> None:
        self.runtime = runtime
        self.runs = runs
        self.store = store

    async def list(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_run_approval_requests,
            run_id,
            expected_user_id=context.user_id,
        )

    async def resolve(
        self,
        context: RequestContext,
        run_id: str,
        approval_id: str,
        *,
        resolution: str,
        note: str | None = None,
    ) -> tuple[Any, Any]:
        await self.runs.get(context, run_id)
        request = await asyncio.to_thread(
            self.store.get_approval_request,
            approval_id,
            expected_user_id=context.user_id,
        )
        if request is None or request.run_id != run_id:
            raise NotFoundError("approval request not found")
        if request.required_role == "operator" and not context.principal.can(
            "approvals.resolve.operator"
        ):
            raise AuthorizationError("operator approval is required")
        action = (
            await asyncio.to_thread(self.store.get_action_intent, request.action_id)
            if request.action_id is not None
            else None
        )
        resolved = await asyncio.to_thread(
            self.store.resolve_approval_request,
            approval_id=approval_id,
            run_id=run_id,
            user_id=context.user_id,
            resolution=resolution,
            note=note,
            actor_id=context.principal.subject,
        )
        if resolved is None:
            raise ConflictError("approval request is no longer resolvable")
        graph_task_id = request.task_id or (action.task_id if action is not None else None)
        await self.runtime.events.publish(
            AgentEvent(
                event_id=f"approval:{approval_id}:resolved:{resolved.status}",
                run_id=run_id,
                task_id=graph_task_id,
                type=EventType.APPROVAL_RESOLVED.value,
                status=resolved.status,
                data={
                    "approval_id": approval_id,
                    "action_id": resolved.action_id,
                    "resolution": resolved.resolution,
                    "resolved_by": resolved.resolved_by,
                },
            )
        )
        if resolved.status == "approved":
            await asyncio.to_thread(self.store.notify_work, run_id)
            await self.runtime.events.publish(
                AgentEvent(
                    event_id=(
                        f"approval:{approval_id}:task.completed"
                        if request.subject_type == "graph_node"
                        else f"approval:{approval_id}:task.queued"
                        if graph_task_id
                        else f"approval:{approval_id}:run.queued"
                    ),
                    run_id=run_id,
                    task_id=graph_task_id,
                    type=(
                        EventType.TASK_COMPLETED.value
                        if request.subject_type == "graph_node"
                        else EventType.TASK_QUEUED.value
                        if graph_task_id
                        else EventType.RUN_QUEUED.value
                    ),
                    status=("completed" if request.subject_type == "graph_node" else "queued"),
                    data={"reason": "approval_granted", "approval_id": approval_id},
                )
            )
        else:
            await self.runtime.events.publish(
                AgentEvent(
                    event_id=(
                        f"approval:{approval_id}:task.failed"
                        if graph_task_id
                        else f"approval:{approval_id}:run.failed"
                    ),
                    run_id=run_id,
                    task_id=graph_task_id,
                    type=(
                        EventType.TASK_FAILED.value if graph_task_id else EventType.RUN_FAILED.value
                    ),
                    status="failed",
                    data={"reason": f"approval_{resolved.status}"},
                )
            )
        run = await self.runs.get(context, run_id)
        return resolved, run
