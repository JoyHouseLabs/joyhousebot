"""Application service for versioned AgentTeam collaboration boundaries."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.domain.agent_teams import AgentTeamRevision


class AgentTeamService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def save_draft(self, revision: AgentTeamRevision) -> dict[str, Any]:
        try:
            stored = await asyncio.to_thread(
                self.store.save_agent_team_revision, revision
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        return stored.to_dict()

    async def publish(
        self, team_id: str, revision_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        revision = await asyncio.to_thread(
            self.store.get_agent_team_revision, revision_id
        )
        if revision is None or revision.team_id != team_id:
            raise NotFoundError("AgentTeam revision not found")
        errors: list[str] = []
        for member in revision.members:
            agent_revision = await asyncio.to_thread(
                self.store.get_agent_revision, member.agent_revision_id
            )
            definition = await asyncio.to_thread(
                self.store.get_agent_definition, member.agent_id
            )
            if (
                agent_revision is None
                or definition is None
                or agent_revision.agent_id != member.agent_id
                or agent_revision.status != "published"
                or definition.current_revision_id != member.agent_revision_id
            ):
                errors.append(
                    f"member {member.member_id} does not pin the current published "
                    f"Agent revision {member.agent_id}@{member.agent_revision_id}"
                )
        if errors:
            raise ValidationError("; ".join(errors))
        try:
            stored = await asyncio.to_thread(
                self.store.publish_agent_team_revision,
                team_id,
                revision_id,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        return stored.to_dict()

    async def list_catalog(self) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.list_agent_team_revisions)
        return [item.to_dict() for item in rows]

    async def list_revisions(self, team_id: str) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.store.list_agent_team_revisions, team_id
        )
        return [item.to_dict() for item in rows]

    async def resolve_published(self, team_id: str) -> AgentTeamRevision:
        revision = await asyncio.to_thread(
            self.store.get_published_agent_team, team_id
        )
        if revision is None:
            raise NotFoundError("published AgentTeam not found")
        return revision
