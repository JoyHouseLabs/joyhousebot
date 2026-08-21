"""Storage contract for AgentTeam revisions and Run-scoped Workspace entries."""

from __future__ import annotations

from typing import Any, Protocol

from joyhousebot.domain.agent_teams import AgentTeamRevision


class AgentTeamStore(Protocol):
    def save_agent_team_revision(self, revision: AgentTeamRevision) -> AgentTeamRevision: ...

    def publish_agent_team_revision(
        self, team_id: str, revision_id: str, *, actor_id: str
    ) -> AgentTeamRevision: ...

    def get_agent_team_revision(self, revision_id: str) -> AgentTeamRevision | None: ...

    def get_published_agent_team(self, team_id: str) -> AgentTeamRevision | None: ...

    def list_agent_team_revisions(self, team_id: str | None = None) -> list[AgentTeamRevision]: ...

    def append_team_workspace_entry(self, **values: Any) -> dict[str, Any]: ...

    def list_team_workspace_entries(self, **values: Any) -> list[dict[str, Any]]: ...
