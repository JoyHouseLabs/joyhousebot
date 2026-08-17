"""Agent revision authority resolution for durable Run submission."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from porthouse.runtime.models import TaskGraphSpec
from porthouse.storage.contracts import AgentCatalogStorePort


def resolve_graph_agent_authority(
    store: AgentCatalogStorePort,
    spec: TaskGraphSpec,
    *,
    top_level: bool,
) -> tuple[TaskGraphSpec, Any, Any | None]:
    """Validate the current Agent boundary and an optional frozen revision."""
    profile = store.get_agent_profile(spec.agent_id)
    if profile is None:
        raise ValueError(f"active published Agent not found: {spec.agent_id}")
    if spec.agent_id != profile.definition.agent_id:
        spec = replace(spec, agent_id=profile.definition.agent_id)
    pinned_revision = None
    if spec.agent_revision_id:
        pinned_revision = store.get_agent_revision(spec.agent_revision_id)
        allowed_statuses = {"published"} if top_level else {"published", "retired"}
        if (
            pinned_revision is None
            or pinned_revision.agent_id != spec.agent_id
            or pinned_revision.status not in allowed_statuses
        ):
            raise ValueError(
                "pinned Graph Agent revision does not match an executable Agent"
            )
    return spec, profile, pinned_revision


__all__ = ["resolve_graph_agent_authority"]
