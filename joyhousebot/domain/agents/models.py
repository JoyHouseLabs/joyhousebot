"""Immutable, database-backed platform Agent definitions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_ROLES = {"coordinator", "executor", "specialist"}
_DEFINITION_STATUSES = {"active", "disabled", "archived"}
_REVISION_STATUSES = {"draft", "published", "retired"}


@dataclass(frozen=True, slots=True)
class PluginReleaseRequirement:
    """Exact plugin artifact required to execute an Agent revision."""

    plugin_id: str
    version: str
    build_digest: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.plugin_id, self.version, self.build_digest)):
            raise ValueError("plugin release requirement is incomplete")

    def to_dict(self) -> dict[str, str]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "build_digest": self.build_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PluginReleaseRequirement":
        return cls(
            plugin_id=str(value["plugin_id"]),
            version=str(value["version"]),
            build_digest=str(value["build_digest"]),
        )


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str = ""
    role: str = "executor"
    status: str = "active"
    is_default: bool = False
    current_revision_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_id.strip() or not self.name.strip():
            raise ValueError("agent_id and name are required")
        if self.role not in _ROLES:
            raise ValueError("invalid Agent role")
        if self.status not in _DEFINITION_STATUSES:
            raise ValueError("invalid Agent status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AgentRevision:
    revision_id: str
    agent_id: str
    version: int
    persona: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""
    model_policy: dict[str, Any] = field(default_factory=dict)
    planning_policy: dict[str, Any] = field(default_factory=dict)
    capability_policy: dict[str, Any] = field(default_factory=dict)
    memory_policy: dict[str, Any] = field(default_factory=dict)
    output_policy: dict[str, Any] = field(default_factory=dict)
    plugin_requirements: tuple[PluginReleaseRequirement, ...] = ()
    status: str = "draft"
    created_by: str = "system"
    created_at: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.revision_id.strip() or not self.agent_id.strip() or self.version < 1:
            raise ValueError("invalid Agent revision identity")
        if self.status not in _REVISION_STATUSES:
            raise ValueError("invalid Agent revision status")
        primary = str(self.model_policy.get("primary") or "").strip()
        if not primary:
            raise ValueError("Agent revision model_policy.primary is required")
        if any(not isinstance(item, PluginReleaseRequirement) for item in self.plugin_requirements):
            raise ValueError("Agent revision plugin_requirements must be pinned releases")
        identities = [(item.plugin_id, item.version) for item in self.plugin_requirements]
        if len(identities) != len(set(identities)):
            raise ValueError("Agent revision plugin requirements must be unique")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["plugin_requirements"] = [item.to_dict() for item in self.plugin_requirements]
        return value

    def definition_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        for key in ("status", "created_at", "published_at"):
            value.pop(key, None)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentRevision":
        return cls(
            revision_id=str(value["revision_id"]),
            agent_id=str(value["agent_id"]),
            version=int(value["version"]),
            persona=dict(value.get("persona") or {}),
            instructions=str(value.get("instructions") or ""),
            model_policy=dict(value.get("model_policy") or {}),
            planning_policy=dict(value.get("planning_policy") or {}),
            capability_policy=dict(value.get("capability_policy") or {}),
            memory_policy=dict(value.get("memory_policy") or {}),
            output_policy=dict(value.get("output_policy") or {}),
            plugin_requirements=tuple(
                PluginReleaseRequirement.from_dict(dict(item))
                for item in value.get("plugin_requirements") or ()
            ),
            status=str(value.get("status") or "draft"),
            created_by=str(value.get("created_by") or "system"),
            created_at=(str(value["created_at"]) if value.get("created_at") else None),
            published_at=(
                str(value["published_at"]) if value.get("published_at") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentProfile:
    definition: AgentDefinition
    revision: AgentRevision

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.definition.to_dict(),
            "revision": self.revision.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentExecutionSnapshot:
    run_id: str
    agent_id: str
    agent_revision_id: str
    model_policy: dict[str, Any]
    planning_policy: dict[str, Any]
    capability_policy: dict[str, Any]
    memory_policy: dict[str, Any]
    output_policy: dict[str, Any]
    plugin_requirements: tuple[PluginReleaseRequirement, ...] = ()
    skill_bindings: tuple[dict[str, Any], ...] = ()
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["skill_bindings"] = list(self.skill_bindings)
        value["plugin_requirements"] = [item.to_dict() for item in self.plugin_requirements]
        return value
