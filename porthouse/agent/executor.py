"""Agent loop: the core processing engine.

在整体架构中：收消息 → ContextBuilder（历史、记忆、技能）→ LLM → 工具调用 → 写回响应；
多 agent 时按 agent_id 选择。
"""

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from porthouse.agent.context import ContextBuilder
from porthouse.agent.memory_lifecycle import MemoryLifecycleMixin
from porthouse.agent.message_processor import MessageProcessorMixin
from porthouse.agent.model_invoker import ModelInvokerMixin
from porthouse.agent.profile_health_repository import ProfileHealthRepository
from porthouse.agent.subagent import SubagentManager
from porthouse.agent.tool_runtime import ToolRuntimeMixin
from porthouse.agent.turn_engine import TurnEngineMixin
from porthouse.capabilities import CapabilityRegistry
from porthouse.config.extensions import (
    deployment_allowed_extension_ids,
    enabled_capability_ids,
    enabled_connector_ids,
    extension_settings,
)
from porthouse.connectors import ToolConnectorRegistry
from porthouse.domain.agents import AgentRevision
from porthouse.domain.remote_connections import materialize_remote_connection
from porthouse.providers.base import LLMProvider
from porthouse.session.protocol import SessionStore

if TYPE_CHECKING:
    from porthouse.cron.service import CronService


class NativeAgentExecutor(
    ModelInvokerMixin,
    ToolRuntimeMixin,
    TurnEngineMixin,
    MessageProcessorMixin,
    MemoryLifecycleMixin,
):
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        provider: LLMProvider,
        scratch_root: Path,
        agent_revision: AgentRevision | None = None,
        model: str | None = None,
        model_fallbacks: list[str] | None = None,
        max_iterations: int = 20,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        memory_window: int = 50,
        max_context_tokens: int | None = None,
        cron_service: "CronService | None" = None,
        session_manager: SessionStore | None = None,
        config: Any | None = None,
        outbound_sink: Any = None,
        embedding_provider_resolver: Any = None,
    ):
        self.outbound_sink = outbound_sink
        self.provider = provider
        self.agent_revision = agent_revision
        self.memory_policy = dict(getattr(agent_revision, "memory_policy", {}) or {})
        self.scratch_root = scratch_root
        self.model = model or provider.get_default_model()
        self.model_fallbacks = self._normalize_model_fallbacks(model_fallbacks)
        # Only configured models (primary + fallback chain) share the cooldown
        # table; per-request user-supplied model names must not poison it.
        self._tracked_models = {self.model, *self.model_fallbacks}
        self._model_failure_count: dict[str, int] = {}
        self._model_cooldown_until: dict[str, float] = {}
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.max_context_tokens = max_context_tokens
        self.cron_service = cron_service
        self.config = config
        if session_manager is None:
            raise ValueError("session_manager is required; use the shared RuntimeSessionManager")
        self.sessions = session_manager
        self.runtime_store = getattr(self.sessions, "store", None)
        if self.runtime_store is None:
            raise ValueError("session_manager must expose the shared runtime store")
        self._profile_health_repository: ProfileHealthRepository | None = None
        self._profile_health_repository = getattr(
            self.runtime_store, "_profile_health_repository", None
        )
        if self._profile_health_repository is None:
            self._profile_health_repository = ProfileHealthRepository(self.runtime_store)
            self.runtime_store._profile_health_repository = self._profile_health_repository
        self._auth_profile_usage = self._profile_health_repository.load()
        self.context = ContextBuilder(
            scratch_root,
            runtime_store=self.runtime_store,
            agent_revision=agent_revision,
        )
        optional_allowlist = []
        if self.config is not None:
            optional_allowlist = list(
                getattr(getattr(self.config, "tools", None), "optional_allowlist", []) or []
            )
        self.subagents = SubagentManager(model=self.model)
        extensions_config = getattr(self.config, "extensions", None)
        enabled_plugins = (
            enabled_capability_ids(self.config)
            if bool(getattr(extensions_config, "discover_entry_points", False))
            else set()
        )
        self.capabilities = CapabilityRegistry(
            store=self.runtime_store,
            scratch_root=self.scratch_root,
            outbound_sink=self.outbound_sink,
            subagent_manager=self.subagents,
            schedule_service=self.cron_service,
            embedding_provider_resolver=embedding_provider_resolver,
            optional_allowlist=optional_allowlist,
            enabled_plugins=enabled_plugins,
        )

        self._running = False
        self.tool_connectors = ToolConnectorRegistry()
        if bool(getattr(extensions_config, "discover_entry_points", False)):
            self.tool_connectors.load_entry_points(enabled=enabled_connector_ids(self.config))
        self._deployment_tool_connector_settings: dict[str, dict[str, Any]] = {}
        for extension_id in deployment_allowed_extension_ids(self.config):
            normalized_id = str(extension_id).strip()
            if normalized_id.startswith("connector-"):
                self._deployment_tool_connector_settings[normalized_id] = extension_settings(
                    self.config, normalized_id
                )
        self._tool_connector_settings = self._effective_tool_connector_settings()
        for manifest in self.tool_connectors.manifests():
            self.runtime_store.upsert_plugin_release(manifest.to_release_dict())
        self._tool_connector_stack: AsyncExitStack | None = None
        self._retired_tool_connector_stacks: list[AsyncExitStack] = []
        self._tool_connectors_connected = False
        self._tool_connector_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_lock_users: dict[str, int] = {}
        configured_concurrency = (
            getattr(getattr(self.config, "gateway", None), "max_concurrent_sessions", None)
            if self.config is not None
            else None
        )
        self._run_semaphore = (
            asyncio.Semaphore(configured_concurrency)
            if isinstance(configured_concurrency, int) and configured_concurrency > 0
            else None
        )

    def _effective_tool_connector_settings(self) -> dict[str, dict[str, Any]]:
        settings = {
            extension_id: dict(value)
            for extension_id, value in self._deployment_tool_connector_settings.items()
        }
        connector_id = "connector-http-capability"
        if connector_id not in settings:
            return settings
        configured = dict(settings.get(connector_id) or {})
        services = dict(configured.get("services") or {})
        reader = getattr(self.runtime_store, "list_active_remote_connection_configurations", None)
        if callable(reader):
            for connection_id, value in reader().items():
                revision = dict(value)
                revision.pop("_revision_id", None)
                services[connection_id] = materialize_remote_connection(revision)
        configured["services"] = services
        settings[connector_id] = configured
        return settings
