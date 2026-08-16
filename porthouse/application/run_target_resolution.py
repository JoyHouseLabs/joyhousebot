"""Resolve one explicit top-level Run authority into immutable runtime references."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from porthouse.application.errors import ValidationError
from porthouse.application.run_commands import (
    AgentRunTarget,
    RunTarget,
    ScenarioRunTarget,
    TeamRunTarget,
)


@dataclass(slots=True)
class ResolvedRunTarget:
    agent_id: str
    decision: Any
    orchestration: dict[str, Any]
    agent_revision_id: str | None = None
    team: Any | None = None
    scenario: Any | None = None


async def resolve_run_target(
    store: Any, router: Any, target: RunTarget, *, prompt: str
) -> ResolvedRunTarget:
    if isinstance(target, AgentRunTarget):
        if target.revision_id:
            revision = await asyncio.to_thread(
                store.get_agent_revision, target.revision_id
            )
            if (
                revision is None
                or revision.agent_id != target.agent_id
                or revision.status != "published"
            ):
                raise ValidationError("published Agent revision not found")
        return ResolvedRunTarget(
            agent_id=target.agent_id,
            decision=router.open_decision(reason_code="EXPLICIT_AGENT_MODE"),
            orchestration={
                "mode": "agent",
                "agent_id": target.agent_id,
                **(
                    {"revision_id": target.revision_id}
                    if target.revision_id
                    else {}
                ),
            },
            agent_revision_id=target.revision_id,
        )
    if isinstance(target, TeamRunTarget):
        team = (
            await asyncio.to_thread(
                store.get_agent_team_revision,
                target.revision_id,
            )
            if target.revision_id
            else await asyncio.to_thread(
                store.get_published_agent_team,
                target.team_id,
            )
        )
        if (
            team is None
            or team.team_id != target.team_id
            or team.status != "published"
        ):
            raise ValidationError("published AgentTeam not found")
        return ResolvedRunTarget(
            agent_id=team.coordinator.agent_id,
            decision=router.open_decision(reason_code="EXPLICIT_TEAM_MODE"),
            orchestration={
                "mode": "team",
                "team_id": team.team_id,
                "revision_id": team.revision_id,
                "version": team.version,
            },
            team=team,
        )
    if isinstance(target, ScenarioRunTarget):
        if target.revision_id:
            revision = await asyncio.to_thread(
                store.get_agent_revision, target.revision_id
            )
            if (
                revision is None
                or revision.agent_id != target.agent_id
                or revision.status != "published"
            ):
                raise ValidationError("published Scenario Agent revision not found")
        try:
            decision, scenario = await asyncio.to_thread(
                router.route,
                prompt,
                explicit_scenario_id=target.scenario_id,
                explicit_scenario_version=target.version,
                supplied_inputs=target.inputs,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        assert scenario is not None
        return ResolvedRunTarget(
            agent_id=target.agent_id,
            decision=decision,
            orchestration={
                "mode": "scenario",
                "scenario_id": scenario.scenario_id,
                "version": scenario.version,
                **(
                    {"agent_revision_id": target.revision_id}
                    if target.revision_id
                    else {}
                ),
            },
            agent_revision_id=target.revision_id,
            scenario=scenario,
        )
    raise ValidationError("unsupported Run execution mode")
