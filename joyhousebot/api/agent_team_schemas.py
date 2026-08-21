"""HTTP schemas for AgentTeam revision management."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class AgentTeamMemberRequest(BaseModel):
    member_id: str = Field(pattern=_ID_PATTERN)
    agent_id: str = Field(pattern=_ID_PATTERN)
    agent_revision_id: str = Field(pattern=_ID_PATTERN)
    role: str = Field(min_length=1, max_length=128)
    responsibility: str = Field(min_length=1, max_length=2000)
    can_delegate: bool = False
    allowed_handoffs: list[str] = Field(default_factory=list, max_length=32)


class SaveAgentTeamRevisionRequest(BaseModel):
    revision_id: str = Field(pattern=_ID_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    coordinator_member_id: str = Field(pattern=_ID_PATTERN)
    members: list[AgentTeamMemberRequest] = Field(min_length=2, max_length=32)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    budget_policy: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    # Either the full canonical blueprint, or a preset + role_bindings the
    # server derives canonical phases from. Omitted/null keeps the team on the
    # implicit default (non-binding for the Coordinator).
    collaboration_blueprint: dict[str, Any] | None = None
    role_bindings: dict[str, list[str]] | None = None


class ValidateBlueprintRequest(BaseModel):
    blueprint: dict[str, Any] | None = None
    members: list[AgentTeamMemberRequest] = Field(min_length=2, max_length=32)
    coordinator_member_id: str = Field(pattern=_ID_PATTERN)
    budget_policy: dict[str, Any] = Field(default_factory=dict)
