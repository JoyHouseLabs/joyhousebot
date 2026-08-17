"""Plan preview and confirmation use cases for team planning Runs.

Split from ``RunService`` so each module keeps one stable responsibility:
this mixin owns the frozen-plan read model (preview + stage graph) and the
confirm / regenerate / cancel state transitions on ``run_plan_confirmations``.
It deliberately shares no logic with Tool Approvals; the two are audited
separately.
"""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.application.presenters import record_dict
from porthouse.orchestration.blueprint_compiler import render_stage_graph
from porthouse.runtime.models import AgentEvent, EventType

# Plan regeneration budget shared by the API contract and store guard.
_MAX_PLAN_GENERATIONS = 5


class RunPlanMixin:
    """Plan confirmation surface mixed into ``RunService``."""

    async def plan(self, context: RequestContext, run_id: str) -> dict[str, Any]:
        """Return the frozen plan preview and its confirmation state."""
        run = await self.get(context, run_id)
        confirmation = await asyncio.to_thread(
            self.stores.plan_confirmations.get_plan_confirmation, run_id
        )
        if confirmation is None:
            raise NotFoundError("plan not ready")
        artifacts = await asyncio.to_thread(
            self.stores.execution.list_runtime_artifacts, run_id
        )
        by_id = {str(item["artifact_id"]): item for item in artifacts}
        plan_artifact = by_id.get(confirmation["plan_artifact_id"])
        spec_artifact = by_id.get(confirmation["graph_spec_artifact_id"])
        plan = dict(plan_artifact["content"]) if plan_artifact else {}
        spec = dict(spec_artifact["content"]) if spec_artifact else {}
        awaiting = (
            confirmation["status"] == "awaiting_confirmation"
            and str(run.status) == "waiting_input"
        )
        blueprint = dict(
            dict((run.options or {}).get("metadata") or {}).get(
                "team_collaboration_blueprint"
            )
            or {}
        )
        return {
            "run_id": run_id,
            "plan_version": confirmation["plan_version"],
            "status": "awaiting_confirmation" if awaiting else confirmation["status"],
            "awaiting_confirmation": awaiting,
            "actions": ["confirm", "regenerate", "cancel"] if awaiting else [],
            "plan": {
                "intent": plan.get("intent"),
                "summary": plan.get("summary"),
                "planned_steps": plan.get("planned_steps") or [],
                "estimated_duration_seconds": plan.get("estimated_duration_seconds"),
            },
            "stage_graph": render_stage_graph(blueprint, plan),
            "estimate": {
                "task_count": len(spec.get("tasks") or []),
                "phase_count": len(blueprint.get("phases") or []),
                "max_concurrent": spec.get("max_concurrent"),
            },
            "confirmation": {
                "requested_at": confirmation["requested_at"],
                "expires_at": confirmation["expires_at"],
                "feedback": confirmation["feedback"],
                "action_at": confirmation["action_at"],
                "action_by": confirmation["action_by"],
                "team_id": confirmation["team_id"],
                "team_revision_id": confirmation["team_revision_id"],
            },
        }

    async def act_on_plan(
        self,
        context: RequestContext,
        run_id: str,
        *,
        action: str,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        """Confirm, regenerate (with feedback) or cancel a frozen plan."""
        if action not in {"confirm", "regenerate", "cancel"}:
            raise ValidationError("action must be confirm, regenerate or cancel")
        run = await self.get(context, run_id)
        confirmation = await asyncio.to_thread(
            self.stores.plan_confirmations.get_plan_confirmation, run_id
        )
        if confirmation is None:
            raise NotFoundError("plan not ready")
        resolved_status = {
            "confirm": "confirmed",
            "regenerate": "regenerate_requested",
            "cancel": "cancelled",
        }[action]
        awaiting = (
            confirmation["status"] == "awaiting_confirmation"
            and str(run.status) == "waiting_input"
        )
        if not awaiting:
            # Idempotent repeats succeed; a conflicting action conflicts.
            if confirmation["status"] == resolved_status:
                return {
                    "run": record_dict(run),
                    "plan_confirmation": confirmation,
                    "no_op": True,
                }
            raise ConflictError("plan_already_actioned")
        trimmed = (feedback or "").strip()
        if action == "regenerate":
            if not trimmed:
                raise ValidationError("plan_feedback_required")
            if int(confirmation["plan_version"]) >= _MAX_PLAN_GENERATIONS:
                raise ValidationError("plan_regeneration_exhausted")
        resolved = await asyncio.to_thread(
            self.stores.plan_confirmations.act_plan_confirmation,
            run_id=run_id,
            user_id=context.user_id,
            action=action,
            feedback=trimmed or None,
        )
        if resolved is None:
            raise ConflictError("plan_already_actioned")
        if action == "confirm":
            await asyncio.to_thread(
                self.stores.plan_confirmations.queue_plan_confirmed_run, run_id
            )
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.RUN_QUEUED.value,
                    status="queued",
                    data={"reason": "plan_confirmed"},
                )
            )
        elif action == "regenerate":
            bumped = await asyncio.to_thread(
                self.stores.plan_confirmations.requeue_plan_regeneration,
                run_id,
                feedback=trimmed,
            )
            if bumped is None:
                raise ConflictError("plan_already_actioned")
        else:
            if not await self.runtime.cancel(run_id, "plan cancelled by owner"):
                raise ConflictError("plan_already_actioned")
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.PLAN_CONFIRMATION_RESOLVED.value,
                status="completed",
                data={
                    "action": action,
                    "plan_version": confirmation["plan_version"],
                    "feedback": trimmed or None,
                },
            )
        )
        return {
            "run": record_dict(await self.get(context, run_id)),
            "plan_confirmation": resolved,
        }
