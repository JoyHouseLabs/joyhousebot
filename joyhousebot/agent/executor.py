"""Agent loop: the core processing engine.

在整体架构中：收消息 → ContextBuilder（历史、记忆、技能）→ LLM → 工具调用 → 写回响应；
多 agent 时按 agent_id 选择。
"""

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.memory_lifecycle import MemoryLifecycleMixin
from joyhousebot.agent.message_processor import MessageProcessorMixin
from joyhousebot.agent.model_invoker import ModelInvokerMixin
from joyhousebot.agent.profile_health_repository import ProfileHealthRepository
from joyhousebot.agent.subagent import SubagentManager
from joyhousebot.agent.tool_runtime import ToolRuntimeMixin
from joyhousebot.agent.turn_engine import TurnEngineMixin
from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.domain.agents import AgentRevision
from joyhousebot.providers.base import LLMProvider
from joyhousebot.session.protocol import SessionStore

if TYPE_CHECKING:
    from joyhousebot.config.schema import ExecToolConfig
    from joyhousebot.cron.service import CronService


# Default user message sent after tool results when messages.after_tool_results_prompt is not set
_default_after_tool_results_prompt = (
    "Summarize the tool results briefly for the user (1-4 sentences). "
    "If the task is done, give the outcome; if more steps are needed, state the next action only."
)


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
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionStore | None = None,
        mcp_servers: dict | None = None,
        config: Any | None = None,
        transcribe_provider: Any = None,
        outbound_sink: Any = None,
    ):
        from joyhousebot.config.schema import ExecToolConfig

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
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.config = config
        self.transcribe_provider = transcribe_provider
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
        self.capabilities = CapabilityRegistry(
            store=self.runtime_store,
            optional_allowlist=optional_allowlist,
            plugin_modules=list(
                getattr(getattr(self.config, "tools", None), "capability_plugins", []) or []
            ),
            discover_entry_points=bool(
                getattr(getattr(self.config, "tools", None), "discover_capability_plugins", False)
            ),
        )
        self.subagents = SubagentManager(model=self.model)

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connect_lock = asyncio.Lock()
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
        self._register_default_tools()
