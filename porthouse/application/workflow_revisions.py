"""Immutable Agent references used by published Workflow revisions."""

from __future__ import annotations

from typing import Any

from porthouse.application.errors import ValidationError


def freeze_workflow_agent_revision(
    store: Any,
    agent_id: str,
    revision_id: str | None,
    *,
    field: str,
) -> str:
    if revision_id:
        revision = store.get_agent_revision(revision_id)
        if (
            revision is None
            or revision.agent_id != agent_id
            or revision.status not in {"published", "retired"}
        ):
            raise ValidationError(f"{field} Agent revision is unavailable")
        return revision.revision_id
    profile = store.get_agent_profile(agent_id)
    if profile is None:
        raise ValidationError(f"{field} Agent is not published")
    return profile.revision.revision_id


__all__ = ["freeze_workflow_agent_revision"]
