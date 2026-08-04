"""Versioned HTTP request schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Resource identifiers supplied by clients stay within a small, safe alphabet.
_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class RunInput(BaseModel):
    type: Literal["message"] = "message"
    content: str = Field(min_length=1)


class CreateRunRequest(BaseModel):
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    session_id: str | None = Field(default=None, min_length=1, pattern=_ID_PATTERN)
    scenario_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    scenario_inputs: dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["auto", "interactive", "background"] = "auto"
    input: RunInput
    model: str | None = None
    system_prompt: str | None = None
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    max_turns: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolveRunInputRequest(BaseModel):
    input_request_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    answers: dict[str, Any] = Field(min_length=1)


class SavePlatformAdminRequest(BaseModel):
    role: Literal["admin", "operator", "viewer"] = "admin"
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    is_test_user: bool = False


class CreateAccessTokenRequest(BaseModel):
    user_id: str = Field(pattern=_ID_PATTERN)
    label: str = Field(default="", max_length=128)
    expires_at: str | None = None


class SaveAgentRevisionRequest(BaseModel):
    revision_id: str = Field(pattern=_ID_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = ""
    role: Literal["coordinator", "executor", "specialist"] = "executor"
    definition_status: Literal["active", "disabled", "archived"] = "active"
    persona: dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""
    model_policy: dict[str, Any]
    planning_policy: dict[str, Any] = Field(default_factory=dict)
    capability_policy: dict[str, Any] = Field(default_factory=dict)
    memory_policy: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Agent-level memory policy. Use enabled/mode/scope, layers for working/session/episodic/profile/long_term/agent, "
            "read_mode (auto/tool_only/none), and write_mode (candidate/direct/none)."
        ),
    )
    output_policy: dict[str, Any] = Field(default_factory=dict)


class SaveMCPServerRequest(BaseModel):
    enabled: bool = True
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""


class BindAgentSkillRequest(BaseModel):
    skill_id: str = Field(pattern=_ID_PATTERN)
    skill_version: str = Field(min_length=1, max_length=64)
    activation_mode: Literal["always", "coordinator_selected", "scenario_required"] = (
        "coordinator_selected"
    )
    priority: int = Field(default=100, ge=0, le=10000)
    configuration: dict[str, Any] = Field(default_factory=dict)


class PublishCapabilityRequest(BaseModel):
    kind: Literal["tool", "agent", "workflow", "skill", "connector"]
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    adapter: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    execution_mode: str = "immediate"
    expected_duration_seconds: int = Field(default=10, ge=0)
    timeout_seconds: int = Field(default=60, gt=0, le=86400)
    idempotent: bool = True
    retryable: bool = True
    side_effect: str = "none"
    supports_stream: bool = False
    permissions: list[str] = Field(default_factory=list)
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)


class SaveCapabilityRuntimeSettingsRequest(BaseModel):
    enabled: bool = True
    configuration: dict[str, Any] = Field(default_factory=dict)


class CreateReplayRequest(BaseModel):
    mode: Literal["offline", "frozen", "branch", "live"] = "offline"
    source_turn_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    prompt: str | None = None
    model: str | None = None
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    system_prompt: str | None = None


class ScenarioFieldRequest(BaseModel):
    name: str = Field(min_length=1, pattern=_ID_PATTERN)
    value_type: Literal["string", "integer", "number", "boolean", "array", "object"]
    required: bool = False
    description: str = ""
    default: Any = None
    enum: list[Any] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    sensitive: bool = False


class ClarificationNodeRequest(BaseModel):
    node_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    kind: Literal["question", "confirmation", "terminal"]
    question: str = ""
    field_names: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


class ClarificationEdgeRequest(BaseModel):
    source_node_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    target_node_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    condition: str = "true"
    priority: int = 100


class SaveScenarioVersionRequest(BaseModel):
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = ""
    fields: list[ScenarioFieldRequest] = Field(default_factory=list)
    nodes: list[ClarificationNodeRequest] = Field(default_factory=list)
    edges: list[ClarificationEdgeRequest] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    planning_mode: Literal["fixed", "dynamic"] = "dynamic"
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    routing_rules: list[dict[str, Any]] = Field(default_factory=list)


class SimulateScenarioRequest(BaseModel):
    prompt: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class GraphTaskRequest(BaseModel):
    id: str = Field(min_length=1, pattern=_ID_PATTERN)
    prompt: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    dependencies: list[str] = Field(default_factory=list)
    name: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=20)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    capability_input: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)


class CreateGraphRequest(BaseModel):
    goal: str = Field(min_length=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    session_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    tasks: list[GraphTaskRequest] = Field(min_length=1)
    max_concurrent: int = Field(default=4, ge=1, le=32)
    fail_fast: bool = True


class ScheduleSpec(BaseModel):
    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = Field(default=None, ge=60000)
    cron_expr: str | None = None
    timezone: str | None = None


class SchedulePayload(BaseModel):
    message: str = Field(min_length=1)
    deliver: bool = False
    channel: str | None = Field(default=None, max_length=32)
    to: str | None = Field(default=None, max_length=128)


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    schedule: ScheduleSpec
    payload: SchedulePayload
    enabled: bool = True


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    schedule: ScheduleSpec | None = None
    payload: SchedulePayload | None = None
    enabled: bool | None = None
