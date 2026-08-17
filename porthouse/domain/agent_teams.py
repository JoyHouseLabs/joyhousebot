"""Immutable AgentTeam revisions and shared Workspace contracts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from porthouse.domain.collaboration_blueprints import (
    normalize_collaboration_blueprint,
    resolve_effective_blueprint,
)

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REVISION_STATUSES = {"draft", "published", "retired"}
_VISIBILITIES = {"team", "coordinator"}
_REQUIRED_TEAM_CONTEXT = (
    "root_goal",
    "team_identity",
    "assigned_objective",
    "confirmed_inputs",
    "dependency_results",
    "policy_snapshot",
)
_EXCLUDED_TEAM_CONTEXT = (
    "full_session_history",
    "member_private_memory",
    "system_prompt",
    "secrets",
    "raw_tool_arguments",
    "private_reasoning",
)
_WORKSPACE_ENTRY_TYPES = {"task_result", "subagent_result", "decision", "evidence"}
_WORKSPACE_FIELDS = {
    "summary",
    "content",
    "structured_output",
    "artifact_id",
    "tools_used",
    "usage",
}


def _stable_id(value: str, field_name: str) -> str:
    result = str(value or "").strip()
    if not _ID.fullmatch(result):
        raise ValueError(f"{field_name} must be a stable identifier")
    return result


@dataclass(frozen=True, slots=True)
class AgentTeamMember:
    """One named role pinned to an immutable Agent revision."""

    member_id: str
    agent_id: str
    agent_revision_id: str
    role: str
    responsibility: str
    can_delegate: bool = False
    allowed_handoffs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("member_id", "agent_id", "agent_revision_id"):
            object.__setattr__(self, name, _stable_id(getattr(self, name), name))
        if not self.role.strip() or len(self.role) > 128:
            raise ValueError("AgentTeam member role is required and must be <= 128 characters")
        if not self.responsibility.strip() or len(self.responsibility) > 2000:
            raise ValueError("AgentTeam member responsibility is required")
        handoffs = tuple(dict.fromkeys(_stable_id(item, "allowed_handoff") for item in self.allowed_handoffs))
        if self.member_id in handoffs:
            raise ValueError("AgentTeam member cannot hand off to itself")
        object.__setattr__(self, "allowed_handoffs", handoffs)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["allowed_handoffs"] = list(self.allowed_handoffs)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentTeamMember":
        return cls(
            member_id=str(value.get("member_id") or ""),
            agent_id=str(value.get("agent_id") or ""),
            agent_revision_id=str(value.get("agent_revision_id") or ""),
            role=str(value.get("role") or ""),
            responsibility=str(value.get("responsibility") or ""),
            can_delegate=bool(value.get("can_delegate")),
            allowed_handoffs=tuple(str(item) for item in value.get("allowed_handoffs") or ()),
        )


@dataclass(frozen=True, slots=True)
class AgentTeamRevision:
    """Published collaboration boundary consumed by one or more Runs."""

    team_id: str
    revision_id: str
    version: int
    name: str
    description: str
    coordinator_member_id: str
    members: tuple[AgentTeamMember, ...]
    context_policy: dict[str, Any] = field(default_factory=dict)
    budget_policy: dict[str, Any] = field(default_factory=dict)
    approval_policy: dict[str, Any] = field(default_factory=dict)
    collaboration_blueprint: dict[str, Any] | None = None
    status: str = "draft"
    created_by: str = "system"
    created_at: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "team_id", _stable_id(self.team_id, "team_id"))
        object.__setattr__(self, "revision_id", _stable_id(self.revision_id, "revision_id"))
        object.__setattr__(
            self,
            "coordinator_member_id",
            _stable_id(self.coordinator_member_id, "coordinator_member_id"),
        )
        if self.version < 1:
            raise ValueError("AgentTeam version must be >= 1")
        if not self.name.strip() or len(self.name) > 160:
            raise ValueError("AgentTeam name is required and must be <= 160 characters")
        if len(self.description) > 4000:
            raise ValueError("AgentTeam description must be <= 4000 characters")
        if self.status not in _REVISION_STATUSES:
            raise ValueError("invalid AgentTeam revision status")
        if not 2 <= len(self.members) <= 32:
            raise ValueError("AgentTeam must contain between 2 and 32 members")
        member_ids = [item.member_id for item in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("AgentTeam member_id values must be unique")
        if self.coordinator_member_id not in member_ids:
            raise ValueError("AgentTeam coordinator must reference a member")
        known = set(member_ids)
        for member in self.members:
            unknown = set(member.allowed_handoffs) - known
            if unknown:
                raise ValueError(
                    f"AgentTeam member {member.member_id} has unknown handoffs: {sorted(unknown)}"
                )
            if member.allowed_handoffs and not member.can_delegate:
                raise ValueError(
                    f"AgentTeam member {member.member_id} declares handoffs but cannot delegate"
                )
        object.__setattr__(self, "context_policy", normalize_team_context_policy(self.context_policy))
        object.__setattr__(self, "budget_policy", normalize_team_budget_policy(self.budget_policy))
        object.__setattr__(
            self, "approval_policy", normalize_team_approval_policy(self.approval_policy)
        )
        object.__setattr__(
            self,
            "collaboration_blueprint",
            normalize_collaboration_blueprint(
                self.collaboration_blueprint,
                member_ids=known,
                coordinator_member_id=self.coordinator_member_id,
                budget_policy=self.budget_policy,
            ),
        )

    @property
    def effective_blueprint(self) -> dict[str, Any]:
        """Explicit blueprint, else the implicit parallel_synthesize default."""
        return resolve_effective_blueprint(
            collaboration_blueprint=self.collaboration_blueprint,
            member_ids=[item.member_id for item in self.members],
            coordinator_member_id=self.coordinator_member_id,
            budget_policy=self.budget_policy,
        )

    @property
    def coordinator(self) -> AgentTeamMember:
        return next(item for item in self.members if item.member_id == self.coordinator_member_id)

    def member(self, member_id: str) -> AgentTeamMember | None:
        return next((item for item in self.members if item.member_id == member_id), None)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["members"] = [item.to_dict() for item in self.members]
        return value

    def definition_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        for key in ("status", "created_at", "published_at"):
            value.pop(key, None)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentTeamRevision":
        return cls(
            team_id=str(value.get("team_id") or ""),
            revision_id=str(value.get("revision_id") or ""),
            version=int(value.get("version") or 0),
            name=str(value.get("name") or ""),
            description=str(value.get("description") or ""),
            coordinator_member_id=str(value.get("coordinator_member_id") or ""),
            members=tuple(
                AgentTeamMember.from_dict(dict(item)) for item in value.get("members") or ()
            ),
            context_policy=dict(value.get("context_policy") or {}),
            budget_policy=dict(value.get("budget_policy") or {}),
            approval_policy=dict(value.get("approval_policy") or {}),
            collaboration_blueprint=(
                dict(value["collaboration_blueprint"])
                if value.get("collaboration_blueprint") is not None
                else None
            ),
            status=str(value.get("status") or "draft"),
            created_by=str(value.get("created_by") or "system"),
            created_at=str(value["created_at"]) if value.get("created_at") else None,
            published_at=(
                str(value["published_at"]) if value.get("published_at") else None
            ),
        )


def normalize_team_context_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    visibility = str(source.get("default_visibility") or "team")
    if visibility not in _VISIBILITIES:
        raise ValueError("AgentTeam context default_visibility must be team or coordinator")
    entry_types = list(
        dict.fromkeys(
            str(item) for item in source.get("workspace_entry_types") or (
                "task_result",
                "subagent_result",
            )
        )
    )
    fields = list(
        dict.fromkeys(
            str(item) for item in source.get("workspace_fields") or (
                "summary",
                "content",
                "structured_output",
                "artifact_id",
                "tools_used",
            )
        )
    )
    if unknown := set(entry_types) - _WORKSPACE_ENTRY_TYPES:
        raise ValueError(f"AgentTeam context has unknown Workspace entry types: {sorted(unknown)}")
    if unknown := set(fields) - _WORKSPACE_FIELDS:
        raise ValueError(f"AgentTeam context has unknown Workspace fields: {sorted(unknown)}")
    max_chars = max(1000, min(int(source.get("max_chars") or 20000), 100000))
    return {
        "required_context": list(_REQUIRED_TEAM_CONTEXT),
        "excluded_context": list(_EXCLUDED_TEAM_CONTEXT),
        "workspace_enabled": bool(source.get("workspace_enabled", True)),
        "default_visibility": visibility,
        "max_entries": max(1, min(int(source.get("max_entries") or 20), 200)),
        "max_chars": max_chars,
        "max_entry_chars": max(
            500,
            min(int(source.get("max_entry_chars") or 6000), max_chars),
        ),
        "workspace_entry_types": entry_types,
        "workspace_fields": fields,
    }


def normalize_team_budget_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    result: dict[str, Any] = {
        "max_tasks": max(2, min(int(source.get("max_tasks") or 32), 256)),
        "max_parallel_tasks": max(
            1, min(int(source.get("max_parallel_tasks") or 4), 32)
        ),
        "max_handoffs": max(1, min(int(source.get("max_handoffs") or 32), 256)),
        "max_review_rounds": max(
            0, min(int(source.get("max_review_rounds", 2)), 8)
        ),
    }
    for name in ("max_input_tokens", "max_output_tokens"):
        if source.get(name) is not None:
            number = int(source[name])
            if number <= 0:
                raise ValueError(f"AgentTeam {name} must be greater than zero")
            result[name] = number
    if source.get("max_cost_usd") is not None:
        cost = float(source["max_cost_usd"])
        if cost <= 0:
            raise ValueError("AgentTeam max_cost_usd must be greater than zero")
        result["max_cost_usd"] = cost
    return result


def normalize_team_approval_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    role = str(source.get("required_role") or "owner")
    if role not in {"owner", "operator"}:
        raise ValueError("AgentTeam approval required_role must be owner or operator")
    risk = str(source.get("risk") or "medium")
    if risk not in {"low", "medium", "high"}:
        raise ValueError("AgentTeam approval risk is invalid")
    classification = str(source.get("data_classification") or "internal")
    if classification not in {"public", "internal", "confidential", "restricted"}:
        raise ValueError("AgentTeam approval data_classification is invalid")
    expires = int(source.get("expires_in_seconds") or 86400)
    if not 1 <= expires <= 604800:
        raise ValueError("AgentTeam approval expires_in_seconds is out of range")
    return {
        "require_result_approval": bool(source.get("require_result_approval", False)),
        "required_role": role,
        "risk": risk,
        "data_classification": classification,
        "expires_in_seconds": expires,
        "title": str(source.get("title") or "Approve AgentTeam result")[:200],
        "description": str(source.get("description") or "")[:2000],
    }
