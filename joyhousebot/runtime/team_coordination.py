"""Resolve the immutable AgentTeam scope used by coordinator planning."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from joyhousebot.domain.agent_teams import AgentTeamRevision
from joyhousebot.domain.capabilities import resolve_capability_policy
from joyhousebot.storage.contracts import AgentCatalogStorePort


@dataclass(slots=True)
class TeamCoordinationScope:
    team: AgentTeamRevision | None = None
    effective_capabilities: set[str] = field(default_factory=set)
    member_capabilities: dict[str, set[str]] = field(default_factory=dict)
    member_skills: dict[str, set[str]] = field(default_factory=dict)
    member_skill_refs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


async def resolve_team_coordination_scope(
    store: AgentCatalogStorePort,
    *,
    record: Any,
    metadata: dict[str, Any],
    capability_catalog: list[dict[str, Any]],
    snapshot: Any,
) -> TeamCoordinationScope:
    """Load one frozen Team revision and the exact member capability surfaces."""

    scope = TeamCoordinationScope(
        effective_capabilities=set(
            resolve_capability_policy(
                snapshot.capability_policy if snapshot is not None else {},
                capability_catalog,
            )["resolved"]
        )
    )
    team_ref = metadata.get("team_ref")
    if not isinstance(team_ref, dict):
        return scope
    team = await asyncio.to_thread(
        store.get_agent_team_revision,
        str(team_ref.get("revision_id") or ""),
    )
    if (
        team is None
        or team.status not in {"published", "retired"}
        or team.team_id != str(team_ref.get("team_id") or "")
        or team.coordinator.agent_id != record.agent_id
    ):
        raise ValueError("Run references an unavailable AgentTeam revision")
    scope.team = team
    scope.effective_capabilities.clear()
    for member in team.members:
        revision = await asyncio.to_thread(
            store.get_agent_revision, member.agent_revision_id
        )
        if (
            revision is None
            or revision.status not in {"published", "retired"}
            or revision.agent_id != member.agent_id
        ):
            raise ValueError(
                f"AgentTeam member revision is unavailable: {member.member_id}"
            )
        resolved = set(
            resolve_capability_policy(
                revision.capability_policy, capability_catalog
            )["resolved"]
        )
        scope.member_capabilities[member.member_id] = resolved
        scope.effective_capabilities.update(resolved)
        bindings = await asyncio.to_thread(
            store.list_agent_skill_bindings, member.agent_revision_id
        )
        scope.member_skills[member.member_id] = {
            str(item.get("skill_id") or "").removeprefix("skill.")
            for item in bindings
            if str(item.get("skill_id") or "").startswith("skill.")
        }
        scope.member_skill_refs[member.member_id] = [
            {
                "skill_id": str(item["skill_id"]),
                "version": str(item["skill_version"]),
                "content_sha256": str(item.get("content_sha256") or ""),
            }
            for item in bindings
        ]
    return scope
