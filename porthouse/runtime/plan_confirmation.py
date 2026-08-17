"""Worker-side plan confirmation freeze for team planning Runs.

Split from ``RequestCoordinationMixin`` so coordination keeps one stable
responsibility. This mixin freezes the plan preview (plan + compiled graph
spec artifacts), creates the ``run_plan_confirmations`` gate, and parks the
Run in ``waiting_input`` until the owner confirms, regenerates or cancels
through the public plan API.
"""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.runtime.models import AgentEvent, AgentOptions, EventType


class PlanConfirmationMixin:
    """Plan confirmation freeze mixed into the runtime coordinator."""

    async def _await_plan_confirmation(
        self,
        record: Any,
        *,
        options: AgentOptions,
        team: Any,
        plan: dict[str, Any],
        graph: Any,
        generation: int,
    ) -> None:
        """Freeze the plan preview and park the Run for owner confirmation.

        No executable Task exists yet; confirmation requeues the Run and the
        accepted plan replays deterministically into the same graph.
        """
        plan_artifact_id = f"{record.run_id}:plan:v{generation}"
        spec_artifact_id = f"{record.run_id}:plan-spec:v{generation}"
        provenance = {
            "worker_id": self.worker_id,
            "lease_version": record.lease_version,
            "phase": "planning",
            "plan_generation": generation,
        }
        await asyncio.to_thread(
            self.store.add_runtime_artifact,
            artifact_id=plan_artifact_id,
            run_id=record.run_id,
            name="coordinator-plan",
            media_type="application/json",
            content=plan,
            provenance=provenance,
        )
        await asyncio.to_thread(
            self.store.add_runtime_artifact,
            artifact_id=spec_artifact_id,
            run_id=record.run_id,
            name="coordinator-graph-spec",
            media_type="application/json",
            content=graph.to_dict(),
            provenance=provenance,
        )
        confirmation = await asyncio.to_thread(
            self.store.create_plan_confirmation,
            run_id=record.run_id,
            user_id=record.user_id,
            team_id=team.team_id,
            team_revision_id=team.revision_id,
            plan_version=generation,
            plan_artifact_id=plan_artifact_id,
            graph_spec_artifact_id=spec_artifact_id,
        )
        transitioned = await asyncio.to_thread(
            self.store.update_runtime_run,
            record.run_id,
            status="waiting_input",
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        if not transitioned:
            raise asyncio.CancelledError("run ownership lost before plan confirmation")
        await self._publish_coordination_progress(
            record.run_id,
            "计划已生成，等待所有者确认后执行",
            stage="plan_awaiting_confirmation",
            status="waiting_input",
            data={
                "plan_generation": generation,
                "task_count": len(graph.tasks),
                "expires_at": confirmation["expires_at"],
            },
        )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PLAN_CONFIRMATION_REQUESTED.value,
                phase="planning",
                status="waiting_input",
                summary="协作计划等待确认",
                data={
                    "plan_generation": generation,
                    "plan_version": generation,
                    "actions": ["confirm", "regenerate", "cancel"],
                    "expires_at": confirmation["expires_at"],
                    "task_count": len(graph.tasks),
                    "team_id": team.team_id,
                },
            )
        )
