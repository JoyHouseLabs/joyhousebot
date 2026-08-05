"""Storage contract for the durable, distributed agent runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from joyhousebot.domain.agents import (
    AgentDefinition,
    AgentExecutionSnapshot,
    AgentProfile,
    AgentRevision,
)
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityInvocation
from joyhousebot.domain.scenarios import ScenarioVersion
from joyhousebot.runtime.models import AgentEvent
from joyhousebot.storage.observability_records import (
    ExecutionSpanRecord,
    ModelInvocationRecord,
    ReasoningSegmentRecord,
    ReplayRunRecord,
    TraceBlobRecord,
)
from joyhousebot.storage.platform_records import (
    CapabilityInvocationRecord,
    ConfigurationEventRecord,
    ConfigurationRolloutRecord,
    InputRequestRecord,
    PlatformAdminRecord,
    RunFeedbackRecord,
    RunScenarioStateRecord,
)


def destructive_migrate_enabled() -> bool:
    """Whether legacy destructive schema migrations are explicitly allowed."""
    return os.environ.get("JOYHOUSEBOT_DESTRUCTIVE_MIGRATE", "").strip() == "1"


@dataclass(slots=True)
class RuntimeRunRecord:
    run_id: str
    user_id: str
    session_id: str
    agent_id: str
    kind: str
    status: str
    prompt: str
    options: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    idempotency_key: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    lease_version: int = 0
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    current_phase: str | None = None
    status_summary: str | None = None
    status_reason: str | None = None
    next_action: str | None = None
    waiting_on: str | None = None
    active_turn_id: str | None = None
    active_span_count: int = 0
    completed_task_count: int = 0
    total_task_count: int = 0
    last_event_sequence: int = 0
    last_progress_at: str | None = None


@dataclass(slots=True)
class RuntimeTaskRecord:
    task_id: str
    run_id: str
    agent_id: str
    parent_task_id: str | None
    name: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    priority: int
    attempt: int
    max_attempts: int
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    lease_version: int = 0


@dataclass(slots=True)
class RuntimeLogRecord:
    sequence: int
    run_id: str
    task_id: str | None
    worker_id: str | None
    level: str
    stage: str
    message: str
    data: dict[str, Any]
    created_at: str


@dataclass(slots=True)
class RequestTraceEventRecord:
    """One immutable milestone in an end-to-end request timeline."""

    sequence: int
    event_id: str
    tracker_id: str
    request_id: str
    parent_request_id: str | None
    user_id: str | None
    run_id: str | None
    transport: str
    direction: str
    operation: str
    stage: str
    status: str | None
    data: dict[str, Any]
    created_at: str


@runtime_checkable
class RuntimeStore(Protocol):
    """Backend-neutral runtime contract used by FastAPI and workers.

    Methods are deliberately synchronous: database drivers remain isolated from
    the event loop with ``asyncio.to_thread`` in the runtime.
    """

    backend_name: str

    def upsert_platform_admin(self, **kwargs: Any) -> PlatformAdminRecord: ...

    def get_platform_admin(self, user_id: str) -> PlatformAdminRecord | None: ...

    def list_platform_admins(self) -> list[PlatformAdminRecord]: ...

    def delete_platform_admin(self, user_id: str, *, actor_id: str) -> bool: ...

    def list_platform_admin_events(self, *, limit: int = 200) -> list[dict[str, Any]]: ...

    def create_api_access_token(self, **kwargs: Any) -> tuple[dict[str, Any], str]: ...

    def authenticate_api_access_token(self, token: str) -> dict[str, Any] | None: ...

    def list_api_access_tokens(self, *, limit: int = 500) -> list[dict[str, Any]]: ...

    def revoke_api_access_token(self, token_id: str, *, actor_id: str) -> bool: ...

    def save_agent_revision(
        self, definition: AgentDefinition, revision: AgentRevision
    ) -> None: ...

    def publish_agent_revision(
        self, agent_id: str, revision_id: str, *, actor_id: str = "system"
    ) -> AgentProfile: ...

    def get_agent_revision(self, revision_id: str) -> AgentRevision | None: ...

    def get_agent_definition(self, agent_id: str) -> AgentDefinition | None: ...

    def list_agent_definitions(self, *, active_only: bool = False) -> list[AgentDefinition]: ...

    def list_agent_revisions(self, agent_id: str) -> list[AgentRevision]: ...

    def get_agent_profile(self, agent_id: str | None = None) -> AgentProfile | None: ...

    def list_agent_profiles(self, *, active_only: bool = True) -> list[AgentProfile]: ...

    def list_published_agent_profiles(self) -> list[AgentProfile]: ...

    def create_run_execution_snapshot(
        self, run_id: str, agent_id: str
    ) -> AgentExecutionSnapshot: ...

    def get_run_execution_snapshot(self, run_id: str) -> AgentExecutionSnapshot | None: ...

    def bind_agent_skill(self, **kwargs: Any) -> None: ...

    def list_agent_skill_bindings(self, agent_revision_id: str) -> list[dict[str, Any]]: ...

    def list_configuration_events(self, *, limit: int = 200) -> list[ConfigurationEventRecord]: ...

    def list_mcp_servers(self) -> list[dict[str, Any]]: ...

    def save_mcp_server(self, name: str, value: dict[str, Any]) -> None: ...

    def delete_mcp_server(self, name: str) -> bool: ...

    def list_configuration_rollouts(
        self, *, limit: int = 100
    ) -> list[ConfigurationRolloutRecord]: ...

    def list_configuration_rollout_targets(self, rollout_id: str) -> list[dict[str, Any]]: ...

    def list_pending_agent_revisions(self, worker_id: str) -> list[dict[str, str]]: ...

    def acknowledge_agent_revision(self, **kwargs: Any) -> bool: ...

    def publish_capability(
        self, definition: CapabilityDefinition, *, actor_id: str = "system"
    ) -> None: ...

    def get_capability_definition(
        self, capability_id: str, version: str | None = None
    ) -> dict[str, Any] | None: ...

    def list_capability_definitions(self) -> list[dict[str, Any]]: ...

    def get_capability_runtime_settings(self, capability_id: str) -> dict[str, Any]: ...

    def save_capability_runtime_settings(
        self, capability_id: str, *, enabled: bool, configuration: dict[str, Any], actor_id: str
    ) -> dict[str, Any]: ...

    def upsert_plugin_release(self, manifest: dict[str, Any]) -> None: ...

    def sync_plugin_components(
        self, plugin_id: str, plugin_version: str, components: list[dict[str, Any]], **kwargs: Any
    ) -> None: ...

    def list_plugin_releases(self) -> list[dict[str, Any]]: ...

    def get_plugin_release(self, plugin_id: str, version: str | None = None) -> dict[str, Any] | None: ...

    def list_plugin_components(self, plugin_id: str) -> list[dict[str, Any]]: ...

    def list_plugin_workers(self, plugin_id: str) -> list[dict[str, Any]]: ...

    def record_plugin_check_result(self, plugin_id: str, plugin_version: str, check_name: str, status: str, summary: str, **kwargs: Any) -> None: ...

    def list_plugin_check_results(self, plugin_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def get_plugin_metrics(self, plugin_id: str, *, hours: int = 24) -> dict[str, Any]: ...

    def list_plugin_recent_invocations(self, plugin_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def create_capability_invocation(
        self, invocation: CapabilityInvocation
    ) -> tuple[CapabilityInvocationRecord, bool]: ...

    def start_capability_invocation(self, invocation_id: str, *, worker_id: str) -> bool: ...

    def finish_capability_invocation(self, invocation_id: str, **kwargs: Any) -> bool: ...

    def list_capability_invocations(
        self, run_id: str, *, expected_user_id: str | None = None
    ) -> list[CapabilityInvocationRecord]: ...

    def save_scenario_version(
        self, scenario: ScenarioVersion, *, status: str = "draft", actor_id: str = "system"
    ) -> None: ...

    def publish_scenario(
        self, scenario_id: str, version: int, *, actor_id: str = "system"
    ) -> None: ...

    def get_scenario_version(
        self, scenario_id: str, version: int | None = None
    ) -> ScenarioVersion | None: ...

    def list_scenario_versions(self, *, published_only: bool = True) -> list[ScenarioVersion]: ...

    def save_run_scenario_state(self, **kwargs: Any) -> RunScenarioStateRecord: ...

    def get_run_scenario_state(
        self, run_id: str, *, expected_user_id: str
    ) -> RunScenarioStateRecord | None: ...

    def create_input_request(self, **kwargs: Any) -> InputRequestRecord: ...

    def get_input_request(
        self, input_request_id: str, *, expected_user_id: str
    ) -> InputRequestRecord | None: ...

    def list_pending_input_requests(
        self, run_id: str, *, expected_user_id: str
    ) -> list[InputRequestRecord]: ...

    def resolve_input_request(self, **kwargs: Any) -> bool: ...

    def resolve_dynamic_input_request(self, **kwargs: Any) -> bool: ...

    def create_runtime_run(self, **kwargs: Any) -> tuple[RuntimeRunRecord, bool]: ...

    def materialize_runtime_graph(self, **kwargs: Any) -> RuntimeRunRecord: ...

    def check_api_rate_limit(self, rate_key: str, **kwargs: Any) -> bool: ...

    def get_runtime_run(
        self, run_id: str, expected_user_id: str | None = None
    ) -> RuntimeRunRecord | None: ...

    def claim_runtime_run(self, run_id: str, **kwargs: Any) -> RuntimeRunRecord | None: ...

    def heartbeat_runtime_run(self, run_id: str, **kwargs: Any) -> bool: ...

    def list_runtime_runs(self, **kwargs: Any) -> list[RuntimeRunRecord]: ...

    def count_runtime_runs(self, **kwargs: Any) -> int: ...

    def delete_runtime_session(self, **kwargs: Any) -> int:
        """Delete terminal runs owned by one user/session/agent."""
        ...

    def list_incomplete_runtime_runs(self, limit: int = 500) -> list[RuntimeRunRecord]: ...

    async def purge_old_runtime_data(self, older_than_ms: int) -> dict[str, int]:
        """Delete runtime events/logs/trace events older than the epoch-ms cutoff.

        Returns the number of rows deleted per table.
        """
        ...

    def update_runtime_run(self, run_id: str, **kwargs: Any) -> bool: ...

    def finish_runtime_run(self, run_id: str, **kwargs: Any) -> AgentEvent | None:
        """Atomically fence the owner, commit terminal state, and append its event."""
        ...

    def reset_runtime_run(self, run_id: str) -> bool: ...

    def append_runtime_event(self, event: AgentEvent) -> AgentEvent: ...

    def list_runtime_events(self, run_id: str, **kwargs: Any) -> list[AgentEvent]: ...

    def create_runtime_task(self, **kwargs: Any) -> RuntimeTaskRecord: ...

    def get_runtime_task(self, task_id: str) -> RuntimeTaskRecord | None: ...

    def list_runtime_tasks(self, **kwargs: Any) -> list[RuntimeTaskRecord]: ...

    def get_runtime_task_dependencies(self, task_id: str) -> list[str]: ...

    def update_runtime_task(self, task_id: str, **kwargs: Any) -> bool: ...

    def claim_runtime_task(self, **kwargs: Any) -> RuntimeTaskRecord | None: ...

    def heartbeat_runtime_task(self, task_id: str, **kwargs: Any) -> bool: ...

    def cancel_runtime_tasks(self, run_id: str) -> int: ...

    def reset_runtime_tasks(self, run_id: str) -> int: ...

    def reconcile_runtime_graph(self, run_id: str) -> dict[str, int]: ...

    def start_runtime_graph(self, run_id: str) -> bool: ...

    def add_runtime_artifact(self, **kwargs: Any) -> None: ...

    def list_runtime_artifacts(self, run_id: str) -> list[dict[str, Any]]: ...

    def append_runtime_log(self, **kwargs: Any) -> RuntimeLogRecord: ...

    def list_runtime_logs(self, run_id: str, **kwargs: Any) -> list[RuntimeLogRecord]: ...

    def get_session_state(self, storage_key: str) -> dict[str, Any] | None: ...

    def save_session_state(
        self,
        storage_key: str,
        *,
        session_key: str,
        namespace: str,
        state: dict[str, Any],
    ) -> None: ...

    def delete_session_state(self, storage_key: str) -> bool: ...

    def list_session_states(self, *, namespace: str, limit: int = 1000) -> list[dict[str, Any]]: ...

    def append_request_trace_event(self, **kwargs: Any) -> RequestTraceEventRecord:
        """Persist one append-only request/response/error milestone."""
        ...

    def list_request_trace_events(
        self, tracker_id: str, **kwargs: Any
    ) -> list[RequestTraceEventRecord]:
        """Return a tracker timeline in creation order."""
        ...

    def expire_stale_runtime_workers(self, *, stale_after_seconds: int = 120) -> int:
        """Mark abandoned worker registrations offline using their heartbeat lease."""
        ...

    def list_runtime_workers(self, *, limit: int = 500) -> list[dict[str, Any]]: ...

    def get_platform_overview(self) -> dict[str, Any]: ...

    def put_trace_blob(self, *, run_id: str, kind: str, content: Any, **kwargs: Any) -> TraceBlobRecord: ...

    def get_trace_blob(self, blob_id: str) -> TraceBlobRecord | None: ...

    def list_trace_blobs(self, run_id: str) -> list[TraceBlobRecord]: ...

    def start_execution_span(self, **kwargs: Any) -> ExecutionSpanRecord: ...

    def mark_execution_span_first_token(self, span_id: str) -> bool: ...

    def finish_execution_span(self, span_id: str, **kwargs: Any) -> bool: ...

    def list_execution_spans(self, run_id: str) -> list[ExecutionSpanRecord]: ...

    def create_model_invocation(self, **kwargs: Any) -> ModelInvocationRecord: ...

    def mark_model_invocation_first_token(self, invocation_id: str) -> bool: ...

    def finish_model_invocation(self, invocation_id: str, **kwargs: Any) -> bool: ...

    def list_model_invocations(self, run_id: str) -> list[ModelInvocationRecord]: ...

    def append_reasoning_segment(self, **kwargs: Any) -> ReasoningSegmentRecord: ...

    def list_reasoning_segments(self, run_id: str, **kwargs: Any) -> list[ReasoningSegmentRecord]: ...

    def create_replay_run(self, **kwargs: Any) -> ReplayRunRecord: ...

    def update_replay_run(self, replay_id: str, **kwargs: Any) -> bool: ...

    def get_replay_run(self, replay_id: str) -> ReplayRunRecord | None: ...

    def list_replay_runs(self, source_run_id: str) -> list[ReplayRunRecord]: ...

    def create_run_feedback(self, **kwargs: Any) -> RunFeedbackRecord: ...

    def list_run_feedback(self, run_id: str, **kwargs: Any) -> list[RunFeedbackRecord]: ...

    def get_model_response_cache(self, cache_key: str) -> dict[str, Any] | None: ...

    def put_model_response_cache(self, cache_key: str, **kwargs: Any) -> None: ...

    def notify_work(self, run_id: str | None = None) -> None: ...

    def wait_for_work(self, timeout: float) -> bool: ...

    def healthcheck(self) -> dict[str, Any]: ...

    def runtime_metrics(self) -> dict[str, Any]: ...

    def operational_metrics(self) -> dict[str, Any]: ...

    def close(self) -> None: ...
