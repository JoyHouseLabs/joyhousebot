"""Versioned HTTP request schemas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    max_turns: int | None = Field(default=None, gt=0)
    max_repairs: int | None = Field(default=None, ge=0, le=10)
    max_replans: int | None = Field(default=None, ge=0, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerateWorkflowRequest(BaseModel):
    """Ask an Agent to design or revise an executable Workflow draft."""

    goal: str = Field(min_length=1, max_length=4000)
    instruction: str = Field(default="", max_length=2000)
    workflow_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    base_graph: dict[str, Any] | None = None


class SaveWorkflowRequest(BaseModel):
    """Persist an immutable Workflow revision owned by the current user."""

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    goal: str = Field(min_length=1, max_length=4000)
    graph: dict[str, Any]
    change_note: str = Field(default="", max_length=1000)
    source_run_id: str | None = Field(default=None, pattern=_ID_PATTERN)


class PublishWorkflowRequest(BaseModel):
    revision_id: str = Field(pattern=_ID_PATTERN)


class ExecuteWorkflowRequest(BaseModel):
    revision_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    input: str = Field(default="", max_length=4000)
    preview: bool = False


class ResolveRunInputRequest(BaseModel):
    input_request_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    answers: dict[str, Any] = Field(min_length=1)


class ResolveApprovalRequest(BaseModel):
    resolution: Literal["approve", "reject", "request_changes", "revoke"]
    note: str | None = Field(default=None, max_length=2000)


class ResolveOperationRequest(BaseModel):
    resolution: Literal["confirm_succeeded", "confirm_failed", "retry"]
    summary: str | None = Field(default=None, max_length=2000)
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=4000)


class ResolveMemoryCandidateRequest(BaseModel):
    resolution: Literal["accept", "reject"]
    note: str | None = Field(default=None, max_length=4000)


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class UpdateKnowledgeBaseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None


class ReceiveGraphEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    payload: Any


class ReceiveWebhookEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    payload: Any


class CreateEventTriggerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    event_type_filter: str = Field(
        default="*", min_length=1, max_length=128, pattern=r"^(\*|[A-Za-z0-9_.:-]+)$"
    )
    instruction: str = Field(min_length=1, max_length=4000)
    session_mode: Literal["shared", "per_event"] = "shared"
    session_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    enabled: bool = True


class UpdateEventTriggerRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    event_type_filter: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^(\*|[A-Za-z0-9_.:-]+)$"
    )
    instruction: str | None = Field(default=None, min_length=1, max_length=4000)
    session_mode: Literal["shared", "per_event"] | None = None
    session_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    enabled: bool | None = None


class CreateRunFeedbackRequest(BaseModel):
    """Human evaluation of a concrete Run output."""

    feedback_type: Literal[
        "incorrect", "missing_data", "needs_optimization", "helpful", "other"
    ] = "other"
    rating: Literal["positive", "negative", "neutral"] | None = None
    comment: str = Field(min_length=1, max_length=10000)
    output_excerpt: str | None = Field(default=None, max_length=4000)
    turn_id: str | None = Field(default=None, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PluginPlaygroundInvocationRequest(BaseModel):
    """Administrator-only direct invocation of a safe plugin Tool."""

    capability_id: str = Field(pattern=_ID_PATTERN)
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, min_length=1, pattern=_ID_PATTERN)


class SavePlatformAdminRequest(BaseModel):
    role: Literal["admin", "operator", "viewer"] = "admin"
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    is_test_user: bool = False


class CreateAccessTokenRequest(BaseModel):
    user_id: str = Field(pattern=_ID_PATTERN)
    label: str = Field(default="", max_length=128)
    token_type: Literal["user", "service"] = "user"
    scopes: list[str] = Field(default_factory=lambda: ["*"] , min_length=1, max_length=64)
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=90)
    )
    rotation_due_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=60)
    )

    @model_validator(mode="after")
    def validate_token_lifetime(self) -> "CreateAccessTokenRequest":
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        expires_at = self.expires_at.astimezone(timezone.utc)
        if expires_at <= now:
            raise ValueError("expires_at must be in the future")
        if expires_at > now + timedelta(days=366):
            raise ValueError("API token lifetime cannot exceed 366 days")
        if self.rotation_due_at is not None:
            if self.rotation_due_at.tzinfo is None:
                raise ValueError("rotation_due_at must include a timezone")
            rotation_due_at = self.rotation_due_at.astimezone(timezone.utc)
            if rotation_due_at <= now or rotation_due_at >= expires_at:
                raise ValueError("rotation_due_at must be in the future and before expires_at")
        if self.token_type == "service" and "*" in self.scopes:
            raise ValueError("service API tokens cannot use the global wildcard scope")
        return self


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
    monitor_policy: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Desired-state policy for a user-scoped managed Agent Monitor. "
            "Set enabled=true to reconcile one standard Schedule per user after Agent use."
        ),
    )
    plugin_requirements: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_monitor_policy(self) -> "SaveAgentRevisionRequest":
        from joyhousebot.cron.managed_monitor import validate_managed_monitor_policy

        self.monitor_policy = validate_managed_monitor_policy(self.monitor_policy)
        return self


class RolloutPolicyRequest(BaseModel):
    activation_mode: Literal["automatic", "manual"] = "automatic"
    timeout_seconds: int = Field(default=300, ge=10, le=86400)
    auto_rollback: bool = True
    require_healthy_workers: bool = True


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


class CapabilityRefRequest(BaseModel):
    capability_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    version: str = Field(min_length=1, max_length=128)
    kind: Literal["tool", "agent", "workflow", "skill", "connector"]
    plugin_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    plugin_version: str = Field(min_length=1, max_length=128)
    plugin_build_digest: str = Field(min_length=1, max_length=256)


class PublishCapabilityRequest(BaseModel):
    kind: Literal["tool", "agent", "workflow", "skill", "connector"]
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    adapter: str = Field(min_length=1)
    plugin_id: str = Field(default="joyhousebot.core", min_length=1, pattern=_ID_PATTERN)
    plugin_version: str = Field(default="0.1.2", min_length=1, max_length=128)
    plugin_build_digest: str = Field(default="builtin", min_length=1, max_length=256)
    tags: list[str] = Field(default_factory=list)
    execution_mode: str = "immediate"
    expected_duration_seconds: int = Field(default=10, ge=0)
    timeout_seconds: int = Field(default=60, gt=0, le=86400)
    idempotent: bool = True
    retryable: bool = True
    side_effect: str = "none"
    compensation: CapabilityRefRequest | None = None
    invocation_concurrency: Literal["sequential", "parallel_safe"] = "parallel_safe"
    max_concurrent_invocations: int = Field(default=4, ge=1, le=1024)
    supports_stream: bool = False
    permissions: list[str] = Field(default_factory=list)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    connection_ids: list[str] = Field(default_factory=list)
    cost_policy: dict[str, Any] = Field(default_factory=dict)
    configuration_schema: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    rollout_policy: RolloutPolicyRequest = Field(default_factory=RolloutPolicyRequest)


class SaveCapabilityRuntimeSettingsRequest(BaseModel):
    enabled: bool = True
    configuration: dict[str, Any] = Field(default_factory=dict)


class EvalScorerRequest(BaseModel):
    type: Literal[
        "status",
        "exact_match",
        "contains",
        "not_contains",
        "matches_regex",
        "json_schema",
        "json_path_equals",
        "json_path_exists",
        "list_min_items",
        "numeric_range",
        "max_latency_ms",
        "max_cost_usd",
    ]
    required: bool = True
    weight: float = Field(default=1.0, gt=0, le=100)
    path: str = Field(default="", max_length=512)
    value: Any = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    min: float | None = None
    max: float | None = None


class EvalCaseRequest(BaseModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    input: dict[str, Any] = Field(default_factory=dict)
    expected: Any = None
    scorers: list[EvalScorerRequest] = Field(min_length=1, max_length=32)
    tags: list[str] = Field(default_factory=list, max_length=64)
    min_score: float = Field(default=1.0, ge=0, le=1)


class SaveEvalSuiteRequest(BaseModel):
    suite_id: str = Field(pattern=_ID_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)
    status: Literal["draft", "active"] = "active"
    target_types: list[Literal["agent", "scenario", "capability"]] = Field(
        min_length=1, max_length=3
    )
    thresholds: dict[str, Any] = Field(default_factory=dict)
    cases: list[EvalCaseRequest] = Field(min_length=1, max_length=1000)


class CreateEvalRunRequest(BaseModel):
    suite_id: str = Field(pattern=_ID_PATTERN)
    suite_version: int = Field(ge=1)
    target_type: Literal["agent", "scenario", "capability"]
    target_id: str = Field(pattern=_ID_PATTERN)
    target_revision_id: str = Field(pattern=_ID_PATTERN)
    idempotency_key: str | None = Field(default=None, max_length=256)


class ExecuteEvalRunRequest(BaseModel):
    max_concurrency: int = Field(default=4, ge=1, le=16)
    case_timeout_seconds: float = Field(default=300.0, ge=1, le=3600)


class SaveEvalScheduleRequest(BaseModel):
    policy_id: str = Field(pattern=_ID_PATTERN)
    suite_id: str = Field(pattern=_ID_PATTERN)
    suite_version: int = Field(ge=1)
    target_type: Literal["agent", "scenario", "capability"]
    target_id: str = Field(pattern=_ID_PATTERN)
    target_revision_id: str = Field(pattern=_ID_PATTERN)
    cadence_seconds: int = Field(ge=60, le=31_536_000)
    enabled: bool = True
    next_run_at: str | None = None
    execution_configuration: dict[str, Any] = Field(default_factory=dict)


class RecordEvalObservationRequest(BaseModel):
    case_id: str = Field(pattern=_ID_PATTERN)
    output: Any = None
    status: str = Field(default="completed", max_length=64)
    latency_ms: float | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReleaseGateRequirementRequest(BaseModel):
    suite_id: str = Field(pattern=_ID_PATTERN)
    suite_version: int = Field(ge=1)
    min_pass_rate: float = Field(default=1.0, ge=0, le=1)
    max_age_hours: int = Field(default=168, ge=1, le=8760)
    max_total_cost_usd: float | None = Field(default=None, ge=0)
    max_p95_latency_ms: float | None = Field(default=None, ge=0)
    min_cost_coverage: float = Field(default=0.0, ge=0, le=1)
    require_automated: bool = False


class SaveReleaseGateRequest(BaseModel):
    required: bool = True
    requirements: list[ReleaseGateRequirementRequest] = Field(
        min_length=1, max_length=32
    )


class CreateWorkRequest(BaseModel):
    run_id: str = Field(pattern=_ID_PATTERN)
    artifact_id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=10000)
    data_classification: Literal[
        "public", "internal", "confidential", "restricted"
    ] = "internal"
    change_note: str = Field(default="Initial version", max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateWorkVersionRequest(BaseModel):
    run_id: str = Field(pattern=_ID_PATTERN)
    artifact_id: str = Field(pattern=_ID_PATTERN)
    change_note: str = Field(default="", max_length=2000)


class UpdateWorkRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=10000)
    status: Literal["draft", "published", "archived"] | None = None
    visibility: Literal["private", "unlisted", "public"] | None = None
    data_classification: Literal[
        "public", "internal", "confidential", "restricted"
    ] | None = None
    metadata: dict[str, Any] | None = None


class CreateWorkShareRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)
    permission: Literal["view", "download"] = "view"
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


class GrantWorkCollaboratorRequest(BaseModel):
    role: Literal["viewer", "editor"] = "viewer"


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
    input_mode: Literal[
        "auto", "text", "textarea", "single_choice", "multi_choice", "boolean", "number"
    ] = "auto"
    options: list[dict[str, Any]] = Field(default_factory=list)
    allow_other: bool = False
    min_selections: int | None = Field(default=None, ge=0)
    max_selections: int | None = Field(default=None, ge=1)
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
    allowed_capabilities: list[CapabilityRefRequest] = Field(default_factory=list)
    planning_mode: Literal["fixed", "dynamic"] = "dynamic"
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    routing_rules: list[dict[str, Any]] = Field(default_factory=list)


class SimulateScenarioRequest(BaseModel):
    prompt: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class GraphTaskRequest(BaseModel):
    id: str = Field(min_length=1, pattern=_ID_PATTERN)
    prompt: str = ""
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    dependencies: list[str] = Field(default_factory=list)
    name: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=20)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    capability: CapabilityRefRequest | None = None
    capability_input: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = Field(default_factory=dict)
    max_repairs: int | None = Field(default=None, ge=0, le=10)
    allowed_tools: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    node_type: (
        Literal[
            "agent",
            "capability",
            "branch",
            "foreach",
            "wait_event",
            "approval",
            "verify",
            "compensation",
            "bounded_loop",
            "aggregate",
        ]
        | None
    ) = None
    branch: dict[str, Any] = Field(default_factory=dict)
    foreach: dict[str, Any] = Field(default_factory=dict)
    wait_event: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] = Field(default_factory=dict)
    verify: dict[str, Any] = Field(default_factory=dict)
    compensation: dict[str, Any] = Field(default_factory=dict)
    bounded_loop: dict[str, Any] = Field(default_factory=dict)
    aggregate: dict[str, Any] = Field(default_factory=dict)


class CreateGraphRequest(BaseModel):
    goal: str = Field(min_length=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    session_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    tasks: list[GraphTaskRequest] = Field(min_length=1)
    max_concurrent: int = Field(default=4, ge=1, le=32)
    fail_fast: bool = True
    failure_policy: dict[str, Any] = Field(default_factory=dict)
    aggregate: bool = True
    aggregation_policy: dict[str, Any] = Field(default_factory=dict)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, gt=0)


class GraphPatchOperationRequest(BaseModel):
    op: Literal["append", "replace_pending"]
    node: GraphTaskRequest


class CreateGraphPatchRequest(BaseModel):
    base_revision_id: str = Field(min_length=1, pattern=_ID_PATTERN)
    reason: str = Field(min_length=1, max_length=2000)
    operations: list[GraphPatchOperationRequest] = Field(min_length=1, max_length=32)
    approve_high_risk: bool = False


class ResolveGraphPatchProposalRequest(BaseModel):
    resolution: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=4000)


class ScheduleSpec(BaseModel):
    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = Field(default=None, ge=60000)
    cron_expr: str | None = None
    timezone: str | None = None


class SchedulePayload(BaseModel):
    kind: Literal["agent_turn", "agent_monitor"] = "agent_turn"
    message: str = Field(min_length=1)
    deliver: bool = False
    channel: str | None = Field(default=None, max_length=32)
    to: str | None = Field(default=None, max_length=128)
    session_mode: Literal["isolated", "main"] = "isolated"
    session_id: str | None = Field(default=None, min_length=1, pattern=_ID_PATTERN)
    quiet_token: str = Field(default="NO_ACTION", min_length=1, max_length=64)
    defer_when_busy: bool = True
    busy_backoff_ms: int = Field(default=60_000, ge=1_000, le=3_600_000)
    preflight_mode: Literal["always", "runtime_attention"] = "always"
    context_mode: Literal["full", "light"] = "full"
    active_hours: dict[str, str] | None = None

    @model_validator(mode="after")
    def normalize_monitor_session(self) -> "SchedulePayload":
        if self.kind == "agent_monitor" and self.session_mode == "main":
            self.session_id = self.session_id or "main"
        if self.kind != "agent_monitor" and self.preflight_mode != "always":
            raise ValueError("preflight_mode is only available for agent_monitor")
        if self.kind != "agent_monitor" and self.context_mode != "full":
            raise ValueError("context_mode is only available for agent_monitor")
        if self.kind != "agent_monitor" and self.active_hours is not None:
            raise ValueError("active_hours is only available for agent_monitor")
        if self.active_hours is not None:
            from joyhousebot.cron.active_hours import normalize_active_hours

            self.active_hours = normalize_active_hours(self.active_hours)
        if not self.quiet_token.strip():
            raise ValueError("quiet_token must contain a non-whitespace character")
        self.quiet_token = self.quiet_token.strip()
        return self


class SchedulePolicy(BaseModel):
    max_submit_attempts: int = Field(default=3, ge=1, le=10)
    max_run_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_ms: int = Field(default=60_000, ge=1_000, le=3_600_000)
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"
    misfire_grace_ms: int = Field(default=300_000, ge=0, le=86_400_000)
    overlap_policy: Literal["serialize", "skip"] = "serialize"


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    schedule: ScheduleSpec
    payload: SchedulePayload
    policy: SchedulePolicy = Field(default_factory=SchedulePolicy)
    enabled: bool = True


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    schedule: ScheduleSpec | None = None
    payload: SchedulePayload | None = None
    policy: SchedulePolicy | None = None
    enabled: bool | None = None


class UpdateMonitorScratchRequest(BaseModel):
    content: str = Field(max_length=16384)
    expected_revision: int = Field(ge=0)
