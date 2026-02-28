"""Agent loop: the core processing engine.

在整体架构中：收消息 → ContextBuilder（历史、记忆、技能）→ LLM → 工具调用 → 写回响应；
多 agent 时按 agent_id 选择。
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import json_repair
from pathlib import Path
import time
from typing import Any

from loguru import logger

from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.bus.queue import MessageBus
from joyhousebot.providers.base import LLMProvider, LLMResponse
from joyhousebot.utils.exceptions import (
    LLMError,
    sanitize_error_message,
    classify_exception,
    ErrorCategory,
)
from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.tools.registry import ToolRegistry
from joyhousebot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.agent.tools.web import WebSearchTool, WebFetchTool
from joyhousebot.agent.tools.retrieve import RetrieveTool
from joyhousebot.agent.tools.fetch_url_to_knowledgebase import FetchUrlToKnowledgebaseTool
from joyhousebot.agent.tools.memory_get import MemoryGetTool
from joyhousebot.agent.tools.message import MessageTool
from joyhousebot.agent.tools.spawn import SpawnTool
from joyhousebot.agent.tools.cron import CronTool
from joyhousebot.agent.tools.process_tool import ProcessTool
from joyhousebot.agent.tools.code_runner import CodeRunnerTool
from joyhousebot.agent.tools.open_app import OpenAppTool
from joyhousebot.agent.tools.plugin_invoke import PluginInvokeTool
from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.response_prefix import resolve_response_prefix
from joyhousebot.agent.subagent import SubagentManager
from joyhousebot.agent.auth_profiles import (
    classify_failover_reason,
    is_profile_available,
    load_profile_usage,
    mark_profile_failure,
    mark_profile_success,
    resolve_profile_order,
    save_profile_usage,
)
from joyhousebot.session.manager import Session, SessionManager
from joyhousebot.plugins.hooks.types import (
    HookName,
    HookContext,
    BeforeToolCallEvent,
    BeforeToolCallResult,
    AfterToolCallEvent,
    MessageReceivedEvent,
    MessageSendingEvent,
    MessageSendingResult,
    MessageSentEvent,
    SessionStartEvent,
    SessionEndEvent,
    BeforeAgentStartEvent,
    AgentEndEvent,
)
from joyhousebot.plugins.hooks.dispatcher import get_hook_dispatcher

def _make_get_skill_env(workspace: Path) -> Callable[[str], dict[str, str]]:
    """Build get_skill_env(cwd) that returns skills.entries.<name>.env when cwd is under workspace/skills/<name> (OpenClaw-aligned)."""
    workspace_resolved = Path(workspace).expanduser().resolve()
    skills_root = workspace_resolved / "skills"

    def get_skill_env(cwd: str) -> dict[str, str]:
        try:
            from joyhousebot.config.access import get_config
            config = get_config()
            c = Path(cwd).expanduser().resolve()
            rel = c.relative_to(skills_root)
            skill_name = rel.parts[0] if rel.parts else None
        except (ValueError, OSError):
            return {}
        if not skill_name or not getattr(config, "skills", None) or not getattr(config.skills, "entries", None):
            return {}
        entry = config.skills.entries.get(skill_name)
        if not entry or not getattr(entry, "env", None):
            return {}
        return dict(entry.env or {})

    return get_skill_env


# Default user message sent after tool results when messages.after_tool_results_prompt is not set
_default_after_tool_results_prompt = (
    "Summarize the tool results briefly for the user (1-4 sentences). "
    "If the task is done, give the outcome; if more steps are needed, state the next action only."
)


class AgentLoop:
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
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
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
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        config: Any | None = None,
        browser_request_runner: Any = None,
        node_invoke_runner: Any = None,
        exec_approval_request: Any = None,
        approval_resolve_fn: Callable[[str, str], Awaitable[tuple[bool, str]]] | None = None,
        transcribe_provider: Any = None,
        mcp_memory_search_callable: Any = None,
        mcp_knowledge_search_callable: Any = None,
    ):
        from joyhousebot.config.schema import ExecToolConfig
        from joyhousebot.cron.service import CronService
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.model_fallbacks = self._normalize_model_fallbacks(model_fallbacks)
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
        self._auth_profile_usage = load_profile_usage()

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        optional_allowlist = []
        if self.config is not None:
            optional_allowlist = list(getattr(getattr(self.config, "tools", None), "optional_allowlist", []) or [])
        self.tools = ToolRegistry(optional_allowlist=optional_allowlist)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            model_fallbacks=self.model_fallbacks,
            config=self.config,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )
        
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._browser_request_runner = browser_request_runner
        self._node_invoke_runner = node_invoke_runner
        self._exec_approval_request = exec_approval_request
        self._approval_resolve_fn = approval_resolve_fn
        self._mcp_memory_search_callable = mcp_memory_search_callable
        self._mcp_knowledge_search_callable = mcp_knowledge_search_callable
        self._current_session_key: str = ""
        self._register_default_tools()

    @staticmethod
    def _parse_approve_command(content: str) -> tuple[str, str] | None:
        """Parse /approve <id> <decision>. Returns (request_id, decision) or None. Decision: allow-once|allow-always|deny."""
        raw = (content or "").strip()
        if not raw.lower().startswith("/approve"):
            return None
        parts = raw.split()
        if len(parts) < 3:
            return None
        request_id = (parts[1] or "").strip()
        raw_decision = (parts[2] or "").strip().lower()
        if not request_id:
            return None
        decision_map = {
            "allow-once": "allow-once",
            "allow_once": "allow-once",
            "allow": "allow-once",
            "once": "allow-once",
            "allow-always": "allow-always",
            "allow_always": "allow-always",
            "always": "allow-always",
            "deny": "deny",
            "reject": "deny",
        }
        decision = decision_map.get(raw_decision)
        if not decision:
            return None
        return (request_id, decision)

    def _wrap_exec_approval_request(self) -> Callable[..., Awaitable[str | None]] | None:
        """Return a wrapper that injects current session_key into exec approval request (for forwarder)."""
        if not self._exec_approval_request:
            return None

        async def _wrapped(command: str, timeout_ms: int, request_id: str | None = None) -> str | None:
            sk = getattr(self, "_current_session_key", None) or ""
            return await self._exec_approval_request(
                command, timeout_ms, request_id, session_key=sk
            )

        return _wrapped

    def _normalize_model_fallbacks(self, raw_fallbacks: list[str] | None) -> list[str]:
        seen = {self.model}
        out: list[str] = []
        for raw in raw_fallbacks or []:
            model = str(raw or "").strip()
            if not model or model in seen:
                continue
            seen.add(model)
            out.append(model)
        return out

    def _resolve_provider_name_for_model(self, model: str) -> str:
        if "/" in model:
            return str(model.split("/", 1)[0]).strip()
        if self.config and hasattr(self.config, "get_provider_name"):
            resolved = self.config.get_provider_name(model)
            if resolved:
                return str(resolved).strip()
        return ""

    def _build_runtime_provider(self, *, model: str, profile_id: str | None) -> LLMProvider:
        if self.config is None:
            return self.provider
        try:
            from joyhousebot.providers.litellm_provider import LiteLLMProvider
        except Exception:
            return self.provider
        if not isinstance(self.provider, LiteLLMProvider):
            return self.provider

        provider_name = self._resolve_provider_name_for_model(model) or self.config.get_provider_name(model)
        base_cfg = self.config.get_provider(model)
        api_key = base_cfg.api_key if base_cfg else None
        api_base = self.config.get_api_base(model)
        extra_headers = dict(base_cfg.extra_headers or {}) if base_cfg else {}

        if profile_id:
            profile = (getattr(self.config, "auth", None).profiles or {}).get(profile_id)
            if profile is not None:
                if getattr(profile, "api_key", ""):
                    api_key = profile.api_key
                elif getattr(profile, "token", ""):
                    api_key = profile.token
                if getattr(profile, "api_base", None):
                    api_base = profile.api_base
                if getattr(profile, "extra_headers", None):
                    extra_headers.update(dict(profile.extra_headers))
                if getattr(profile, "provider", ""):
                    provider_name = str(profile.provider).strip() or provider_name

        return LiteLLMProvider(
            api_key=api_key,
            api_base=api_base,
            default_model=model,
            extra_headers=extra_headers or None,
            provider_name=provider_name or None,
        )

    def _resolve_profile_candidates(self, provider_name: str) -> list[str | None]:
        if self.config is None or not provider_name:
            return [None]
        profile_ids = resolve_profile_order(self.config, provider_name)
        if not profile_ids:
            return [None]

        now_ms = time.time() * 1000
        available: list[str] = []
        in_cooldown: list[str] = []
        for pid in profile_ids:
            if is_profile_available(self._auth_profile_usage, pid, now_ms=now_ms):
                available.append(pid)
            else:
                in_cooldown.append(pid)
        ordered = available if available else in_cooldown
        return [None] + ordered

    async def _call_provider_with_fallback(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        primary_model: str,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        allow_stream: bool = False,
    ) -> tuple[LLMResponse, str]:
        candidates = [primary_model] + [m for m in self.model_fallbacks if m != primary_model]
        now = time.time()
        available: list[str] = []
        in_cooldown: list[str] = []
        for candidate in candidates:
            until = self._model_cooldown_until.get(candidate, 0.0)
            if until > now:
                in_cooldown.append(candidate)
            else:
                available.append(candidate)
        # Prefer non-cooled models. If all are cooled, still try to avoid deadlock.
        if available:
            candidates = available
        elif in_cooldown:
            candidates = in_cooldown
        last_response: LLMResponse | None = None
        stream_used = False
        for idx, candidate in enumerate(candidates):
            provider_name = self._resolve_provider_name_for_model(candidate)
            profile_candidates = self._resolve_profile_candidates(provider_name)
            for pidx, profile_id in enumerate(profile_candidates):
                runtime_provider = self._build_runtime_provider(model=candidate, profile_id=profile_id)
                use_stream = (
                    allow_stream
                    and stream_callback is not None
                    and not stream_used
                    and idx == 0
                    and pidx == 0
                )
                if use_stream and hasattr(runtime_provider, "chat_stream"):
                    response: LLMResponse | None = None
                    stream_buffer: list[str] = []
                    try:
                        async for kind, data in runtime_provider.chat_stream(
                            messages=messages,
                            tools=tools,
                            model=candidate,
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                        ):
                            if kind == "delta" and isinstance(data, str):
                                stream_buffer.append(data)
                                if stream_callback is not None:
                                    await stream_callback(data)
                            elif kind == "done" and data is not None:
                                response = data
                                break
                    except asyncio.TimeoutError:
                        response = LLMResponse(content="Stream timeout", finish_reason="error")
                        logger.warning(f"Stream timeout for model {candidate}")
                    except ConnectionError as e:
                        response = LLMResponse(content=f"Connection error: {sanitize_error_message(str(e))}", finish_reason="error")
                        logger.error(f"Stream connection error for model {candidate}")
                    except Exception as e:
                        code, _, _ = classify_exception(e)
                        sanitized = sanitize_error_message(str(e))
                        response = LLMResponse(content=f"Stream error [{code}]: {sanitized}", finish_reason="error")
                        logger.error(f"Stream error [{code}] for model {candidate}: {sanitized}")
                    if response is None:
                        response = LLMResponse(content="Stream ended without response", finish_reason="error")
                    stream_used = True
                else:
                    response = await runtime_provider.chat(
                        messages=messages,
                        tools=tools,
                        model=candidate,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                if response.finish_reason != "error":
                    if profile_id:
                        mark_profile_success(self._auth_profile_usage, profile_id)
                        save_profile_usage(self._auth_profile_usage)
                    self._mark_model_success(candidate)
                    if candidate != primary_model:
                        logger.warning(f"Model fallback selected: {primary_model} -> {candidate}")
                    return response, candidate
                # DeepSeek occasionally reports invalid tools name on long legacy sessions.
                # Retry once with a shorter context (keep system + most recent turns).
                err_text = str(response.content or "")
                if (
                    "invalid 'tools[" in err_text.lower()
                    and "function.name" in err_text.lower()
                    and len(messages) > 8
                ):
                    compact_messages: list[dict[str, Any]] = []
                    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                        compact_messages.append(messages[0])
                    recent = [m for m in messages[-6:] if isinstance(m, dict)]
                    compact_messages.extend(recent)
                    try:
                        retry_response = await runtime_provider.chat(
                            messages=compact_messages,
                            tools=tools,
                            model=candidate,
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                        )
                        if retry_response.finish_reason != "error":
                            logger.warning(
                                "Recovered from provider tool-name validation error by using compact history"
                            )
                            return retry_response, candidate
                    except Exception:
                        pass
                last_response = response
                reason = str(response.error_kind or "").strip() or classify_failover_reason(response.content or "")
                if profile_id and self.config is not None:
                    mark_profile_failure(
                        self._auth_profile_usage,
                        profile_id=profile_id,
                        provider=provider_name,
                        reason=reason,
                        config=self.config,
                    )
                    save_profile_usage(self._auth_profile_usage)
                self._mark_model_failure(candidate)
                if pidx < len(profile_candidates) - 1:
                    logger.warning(
                        f"Model call failed on {candidate} profile={profile_id}, trying next profile"
                    )
            if idx < len(candidates) - 1:
                logger.warning(f"Model call failed on {candidate}, trying fallback")
        return last_response or LLMResponse(content="All models failed", finish_reason="error"), primary_model

    def _mark_model_success(self, model: str) -> None:
        self._model_failure_count.pop(model, None)
        self._model_cooldown_until.pop(model, None)

    def _mark_model_failure(self, model: str) -> None:
        failures = int(self._model_failure_count.get(model, 0)) + 1
        self._model_failure_count[model] = failures
        # Exponential backoff cooldown: 15s, 30s, 60s, ... capped at 5min.
        cooldown_s = min(300.0, 15.0 * (2 ** max(0, failures - 1)))
        self._model_cooldown_until[model] = time.time() + cooldown_s
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools (restrict to workspace if configured)
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        
        # Shell tool (direct or Docker backend via exec_config.container_*)
        # get_skill_env: inject skills.entries.<name>.env when cwd is under workspace/skills/<name> (OpenClaw-aligned)
        get_skill_env = _make_get_skill_env(Path(self.workspace).expanduser().resolve())
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            shell_mode=getattr(self.exec_config, "shell_mode", False),
            container_enabled=getattr(self.exec_config, "container_enabled", False),
            container_image=getattr(self.exec_config, "container_image", "alpine:3.18"),
            container_workspace_mount=getattr(self.exec_config, "container_workspace_mount", "") or "",
            container_user=getattr(self.exec_config, "container_user", "") or "",
            container_network=getattr(self.exec_config, "container_network", "none") or "none",
            get_skill_env=get_skill_env,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key), optional=True)
        self.tools.register(WebFetchTool(), optional=True)

        # Knowledge base: retrieve (index from pipeline); optional fetch URL into knowledgebase; optional QMD for knowledge/memory
        self.tools.register(RetrieveTool(
            workspace=self.workspace,
            config=self.config,
            mcp_memory_search_callable=self._mcp_memory_search_callable,
            mcp_knowledge_search_callable=self._mcp_knowledge_search_callable,
        ))
        self.tools.register(FetchUrlToKnowledgebaseTool(workspace=self.workspace, config=self.config), optional=True)
        self.tools.register(MemoryGetTool(workspace=self.workspace))

        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool, optional=True)
        
        # Cron tool (for scheduling)
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service), optional=True)

        # Process tool (list/kill processes; optional, subject to allowlist)
        self.tools.register(ProcessTool(), optional=True)

        # Code runner (Claude Code / Codex / OpenCode backends, optional)
        if self.config and getattr(self.config.tools, "code_runner", None):
            cr_cfg = self.config.tools.code_runner
            if getattr(cr_cfg, "enabled", False):
                self.tools.register(
                    CodeRunnerTool(
                        working_dir=str(self.workspace),
                        default_backend=getattr(cr_cfg, "default_backend", "claude_code"),
                        default_mode=getattr(cr_cfg, "default_mode", "auto"),
                        timeout=getattr(cr_cfg, "timeout", 300),
                        claude_code_command=getattr(cr_cfg, "claude_code_command", "claude"),
                        container_image=getattr(cr_cfg, "container_image", "") or "",
                        container_workspace_mount=getattr(cr_cfg, "container_workspace_mount", "") or "",
                        container_user=getattr(cr_cfg, "container_user", "") or "",
                        container_network=getattr(cr_cfg, "container_network", "none") or "none",
                        require_approval=getattr(cr_cfg, "require_approval", False),
                        approval_request_fn=self._wrap_exec_approval_request(),
                    ),
                    optional=True,
                )

        # Browser tool (when runner provided and browser not disabled)
        if self._browser_request_runner is not None:
            browser_mode = ""
            if self.config and getattr(self.config, "gateway", None):
                browser_mode = str(getattr(self.config.gateway, "node_browser_mode", "auto") or "auto").strip().lower()
            if browser_mode != "off":
                from joyhousebot.agent.tools.browser import BrowserTool
                self.tools.register(BrowserTool(browser_request_runner=self._browser_request_runner))

        # Canvas tool (when node invoke runner provided)
        if self._node_invoke_runner is not None:
            from joyhousebot.agent.tools.canvas import CanvasTool
            self.tools.register(CanvasTool(node_invoke_runner=self._node_invoke_runner))

        # App-first: domain actions are done in App; Agent only uses open_app.
        self.tools.register(OpenAppTool())
        # Plugin tools: Agent calls plugin capabilities via plugin_invoke.
        self.tools.register(PluginInvokeTool())

        # x402 payment tools (optional, requires wallet unlock)
        from joyhousebot.agent.tools.x402_payment import register_x402_tools
        register_x402_tools(self.tools, enabled=True)

        # Deliberation tool (multi-round progressive analysis)
        from joyhousebot.agent.tools.deliberate import DeliberateTool
        self.tools.register(
            DeliberateTool(workspace_path=self.workspace, config=self.config, provider=self.provider, model=self.model),
            optional=True,
        )

        # Knowledge pipeline: source dir (knowledgebase) -> convert to markdown -> processed -> FTS5
        self._knowledge_subprocess = None
        self._knowledge_queue: Any = None
        self._knowledge_watcher_thread: Any = None
        if self.config and getattr(getattr(self.config, "tools", None), "knowledge_pipeline", None):
            kp = self.config.tools.knowledge_pipeline
            source_dir = self.workspace / getattr(kp, "knowledge_source_dir", "knowledgebase")
            processed_dir = self.workspace / getattr(kp, "knowledge_processed_dir", "knowledge/processed")
            
            use_subprocess = getattr(kp, "subprocess_enabled", True)
            
            if use_subprocess:
                from joyhousebot.services.knowledge_pipeline.service import start_knowledge_pipeline_subprocess
                self._knowledge_subprocess = start_knowledge_pipeline_subprocess(
                    workspace=str(self.workspace),
                    source_dir=str(source_dir),
                    processed_dir=str(processed_dir),
                    config=self.config,
                )
            else:
                from joyhousebot.services.knowledge_pipeline import (
                    KnowledgePipelineQueue,
                    start_watcher,
                    sync_processed_dir_to_store,
                )
                ingest_cfg = getattr(self.config.tools, "ingest", None)
                self._knowledge_queue = KnowledgePipelineQueue(
                    self.workspace,
                    source_dir,
                    processed_dir,
                    ingest_config=ingest_cfg,
                    pipeline_config=kp,
                )
                chunk_sz = getattr(kp, "convert_chunk_size", 1200)
                chunk_ol = getattr(kp, "convert_chunk_overlap", 200)
                n = sync_processed_dir_to_store(self.workspace, processed_dir, chunk_size=chunk_sz, chunk_overlap=chunk_ol)
                if n > 0:
                    logger.debug(f"Knowledge pipeline: synced {n} processed files to index")
                self._knowledge_queue.start()
                self._knowledge_watcher_thread = start_watcher(
                    self.workspace, source_dir, processed_dir, self._knowledge_queue, config=self.config
                )

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy). Inject QMD memory/knowledge callables into retrieve if present."""
        if self._mcp_connected or not self._mcp_servers:
            return
        self._mcp_connected = True
        from joyhousebot.agent.tools.mcp import MCPToolWrapper, connect_mcp_servers
        self._mcp_stack = AsyncExitStack()
        await self._mcp_stack.__aenter__()
        await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
        # Inject QMD (or any MCP) memory_search / knowledge_search callables into retrieve tool
        retrieve_tool = self.tools.get("retrieve")
        if retrieve_tool and hasattr(retrieve_tool, "set_mcp_memory_search_callable") and hasattr(retrieve_tool, "set_mcp_knowledge_search_callable"):
            for _name, tool in getattr(self.tools, "_tools", {}).items():
                if not isinstance(tool, MCPToolWrapper):
                    continue
                orig = getattr(tool, "_original_name", "")
                session = getattr(tool, "_session", None)
                if not session or not orig:
                    continue
                if orig == "memory_search":
                    def _make_memory_callable(sess, tool_name):
                        async def _call(query: str, top_k: int):
                            result = await sess.call_tool(tool_name, arguments={"query": query, "top_k": top_k})
                            text = "".join(getattr(b, "text", "") or "" for b in result.content)
                            try:
                                data = json.loads(text) if text.strip() else {}
                                hits = data.get("hits", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                                return hits if isinstance(hits, list) else []
                            except Exception:
                                return []
                        return _call
                    retrieve_tool.set_mcp_memory_search_callable(_make_memory_callable(session, orig))
                elif orig == "knowledge_search":
                    def _make_knowledge_callable(sess, tool_name):
                        async def _call(query: str, top_k: int):
                            result = await sess.call_tool(tool_name, arguments={"query": query, "top_k": top_k})
                            text = "".join(getattr(b, "text", "") or "" for b in result.content)
                            try:
                                data = json.loads(text) if text.strip() else {}
                                hits = data.get("hits", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                                return hits if isinstance(hits, list) else []
                            except Exception:
                                return []
                        return _call
                    retrieve_tool.set_mcp_knowledge_search_callable(_make_knowledge_callable(session, orig))

    def _resolve_memory_scope_key(
        self,
        session_key: str,
        sender_id: str = "",
        metadata: dict | None = None,
    ) -> str | None:
        """Resolve memory scope key from config. Returns None for shared, else scope_key (session or user)."""
        if not self.config:
            return None
        retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
        if not retrieval:
            return None
        scope = getattr(retrieval, "memory_scope", "shared") or "shared"
        if scope == "shared":
            return None
        if scope == "session":
            return session_key
        if scope == "user":
            from_id = getattr(retrieval, "memory_user_id_from", "sender_id") or "sender_id"
            meta_key = getattr(retrieval, "memory_user_id_metadata_key", "user_id") or "user_id"
            meta = metadata or {}
            if from_id == "metadata":
                user_id = (meta.get(meta_key) or "").strip() if isinstance(meta.get(meta_key), str) else ""
            else:
                user_id = (sender_id or "").strip()
            if not user_id:
                user_id = session_key.split(":", 1)[-1] if ":" in session_key else session_key
            channel = session_key.split(":", 1)[0] if ":" in session_key else "unknown"
            return f"{channel}:{user_id}"
        return None

    def _set_tool_context(self, channel: str, chat_id: str) -> None:
        """Update context for all tools that need routing info."""
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id)

        if spawn_tool := self.tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

    def _set_memory_scope(self, scope_key: str | None) -> None:
        """Set memory scope for retrieve and memory_get tools (per-session/per-user isolation)."""
        if retrieve_tool := self.tools.get("retrieve"):
            if hasattr(retrieve_tool, "set_memory_scope"):
                retrieve_tool.set_memory_scope(scope_key)
        if memory_get_tool := self.tools.get("memory_get"):
            if hasattr(memory_get_tool, "set_memory_scope"):
                memory_get_tool.set_memory_scope(scope_key)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
    ) -> tuple[str | None, list[str], bool, LLMResponse | None]:
        """
        Run the agent iteration loop.

        Args:
            initial_messages: Starting messages for the LLM conversation.
            stream_callback: If set and provider supports chat_stream, called with each content delta.
            execution_stream_callback: If set, called with (event_type, payload) for llm_delta, tool_start, tool_output, tool_end, final.
            check_abort_requested: If set, called at start of each iteration with current run_id; when True, loop breaks and returns (None, tools_used, True, None).

        Returns:
            Tuple of (final_content, list_of_tools_used, aborted, last_response for usage persistence).
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        last_response: LLMResponse | None = None
        tools_used: list[str] = []
        active_model = self.model

        async def _stream_cb(content: str) -> None:
            if stream_callback:
                await stream_callback(content)
            if execution_stream_callback:
                await execution_stream_callback("llm_delta", {"content": content})

        while iteration < self.max_iterations:
            iteration += 1

            if check_abort_requested:
                from joyhousebot.services.chat.trace_context import trace_run_id
                run_id = trace_run_id.get() or ""
                if run_id and check_abort_requested(run_id):
                    return (None, tools_used, True, None)

            logger.debug(f"Calling LLM (iteration {iteration}), model={active_model}, messages={len(messages)}")
            use_stream = (
                (stream_callback is not None or execution_stream_callback is not None)
                and hasattr(self.provider, "chat_stream")
                and iteration == 1
            )
            response, used_model = await self._call_provider_with_fallback(
                messages=messages,
                tools=self.tools.get_definitions(),
                primary_model=active_model,
                stream_callback=_stream_cb if use_stream else None,
                allow_stream=use_stream,
            )
            last_response = response
            active_model = used_model
            logger.debug(
                f"LLM response: has_tool_calls={response.has_tool_calls}, content_len={len(response.content or '')}"
            )

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                messages_config_loop = getattr(self.config, "messages", None) if self.config else None
                suppress_tool_errors = bool(
                    messages_config_loop and getattr(messages_config_loop, "suppress_tool_errors", False)
                )
                for tool_call in response.tool_calls:
                    tool_name = (tool_call.name or "").strip() if isinstance(tool_call.name, str) else ""
                    tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                    if not tool_name:
                        logger.warning("Tool call with empty name; returning error result to keep message sync")
                        result = "Error: invalid tool call (missing name or arguments)."
                        messages = self.context.add_tool_result(
                            messages, tool_call.id, tool_call.name or "", result
                        )
                        continue
                    
                    hook_dispatcher = get_hook_dispatcher()
                    hook_ctx = HookContext(
                        session_key=getattr(self, "_current_session_key", "") or "",
                        channel="",
                    )
                    
                    before_event = BeforeToolCallEvent(
                        tool_name=tool_name,
                        params=dict(tool_args),
                    )
                    before_result = await hook_dispatcher.emit_first_result(
                        HookName.BEFORE_TOOL_CALL, before_event, hook_ctx
                    )
                    
                    if before_result and isinstance(before_result, BeforeToolCallResult):
                        if before_result.block:
                            logger.info(f"Tool {tool_name} blocked by hook: {before_result.block_reason}")
                            result = before_result.block_reason or "Tool execution blocked by plugin"
                            messages = self.context.add_tool_result(
                                messages, tool_call.id, tool_name, result
                            )
                            continue
                        if before_result.params:
                            tool_args = before_result.params
                    
                    tools_used.append(tool_name)
                    args_str = json.dumps(tool_args, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_name}({args_str[:200]})")
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_start",
                            {"tool": tool_name, "args": tool_args},
                        )
                    result = await self.tools.execute(
                        tool_name,
                        tool_args,
                        execution_stream_callback=execution_stream_callback,
                    )
                    if execution_stream_callback:
                        await execution_stream_callback(
                            "tool_end",
                            {"tool": tool_name, "result": result},
                        )
                    if suppress_tool_errors and (result or "").strip().startswith("Error"):
                        logger.debug(f"Tool {tool_name} error (suppressed for user): {result[:300]}")
                        result = "Error: Tool execution failed."
                    
                    after_event = AfterToolCallEvent(
                        tool_name=tool_name,
                        params=dict(tool_args),
                        result=result,
                    )
                    await hook_dispatcher.emit(HookName.AFTER_TOOL_CALL, after_event, hook_ctx)
                    
                    preview = (result[:500] + "...") if len(result) > 500 else result
                    logger.debug(f"Tool {tool_name} result (preview): {preview}")
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_name, result
                    )
                follow_up = _default_after_tool_results_prompt
                if messages_config_loop and getattr(messages_config_loop, "after_tool_results_prompt", None):
                    follow_up = (messages_config_loop.after_tool_results_prompt or "").strip() or follow_up
                messages.append({"role": "user", "content": follow_up})
            else:
                final_content = response.content
                break

        if execution_stream_callback and final_content is not None:
            await execution_stream_callback("final", {"content": final_content})

        return final_content, tools_used, False, last_response

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except LLMError as e:
                    logger.error(f"LLM error processing message: {e.code} - {e.message}")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an LLM error: {e.message}"
                    ))
                except asyncio.TimeoutError:
                    logger.warning("Timeout processing message")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, the request timed out. Please try again."
                    ))
                except ConnectionError as e:
                    logger.error(f"Connection error: {sanitize_error_message(str(e))}")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, there was a connection error. Please try again later."
                    ))
                except Exception as e:
                    code, category, _ = classify_exception(e)
                    logger.exception(f"Unexpected error [{code}] processing message")
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an unexpected error. Please try again."
                    ))
            except asyncio.TimeoutError:
                continue
    
    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
        
        if self._knowledge_subprocess:
            from joyhousebot.services.knowledge_pipeline.service import stop_knowledge_pipeline_subprocess
            stop_knowledge_pipeline_subprocess(self._knowledge_subprocess)
            self._knowledge_subprocess = None
        
        if self._knowledge_watcher_thread and self._knowledge_queue:
            if self._knowledge_watcher_thread.is_alive():
                self._knowledge_watcher_thread.join(timeout=5.0)
            self._knowledge_watcher_thread = None
            self._knowledge_queue.stop()
            self._knowledge_queue = None
    
    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).
            stream_callback: If set, called with each content delta when provider supports streaming.
            execution_stream_callback: If set, called with (event_type, payload) for llm_delta, tool_start, tool_output, tool_end, final.
            check_abort_requested: If set, run can be aborted (e.g. chat.abort); when True for current run_id, returns None.

        Returns:
            The response message, or None if no response needed (e.g. run aborted).
        """
        # System messages route back via chat_id ("channel:chat_id")
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        hook_dispatcher = get_hook_dispatcher()
        hook_ctx = HookContext(
            session_key=session_key or msg.session_key,
            channel=msg.channel,
        )
        
        received_event = MessageReceivedEvent(
            from_id=msg.sender_id or "",
            content=msg.content,
            metadata=dict(msg.metadata) if msg.metadata else {},
        )
        await hook_dispatcher.emit(HookName.MESSAGE_RECEIVED, received_event, hook_ctx)
        
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        
        key = session_key or msg.session_key
        self._current_session_key = key
        session = self.sessions.get_or_create(key)
        scope_key = self._resolve_memory_scope_key(key, getattr(msg, "sender_id", "") or "", getattr(msg, "metadata", None) or {})
        if scope_key:
            retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None) if self.config else None
            if retrieval and getattr(retrieval, "memory_scope", "shared") == "user":
                session.metadata["last_memory_scope_key"] = scope_key
        self._set_tool_context(msg.channel, msg.chat_id)
        self._set_memory_scope(scope_key)
        
        # Handle slash commands (only when config.commands.native is not False)
        cmd = msg.content.strip().lower()
        commands_config = getattr(self.config, "commands", None) if self.config else None
        native_enabled = (
            commands_config is None
            or getattr(commands_config, "native", "auto") is True
            or getattr(commands_config, "native", "auto") == "auto"
        )
        if native_enabled and cmd == "/new":
            # Capture messages before clearing (avoid race condition with background task)
            messages_to_archive = session.messages.copy()
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            async def _consolidate_and_cleanup():
                temp_session = Session(key=session.key)
                temp_session.messages = messages_to_archive
                await self._consolidate_memory(temp_session, archive_all=True)

            asyncio.create_task(_consolidate_and_cleanup())
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started. Memory consolidation in progress.")
        if native_enabled and cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 joyhousebot commands:\n/new — Start a new conversation\n/help — Show available commands")
        if (cmd == "/new" or cmd == "/help") and not native_enabled:
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="Commands are disabled.")

        # /approve <id> allow-once|allow-always|deny (aliases: allow, once, deny, reject, always)
        approve_match = self._parse_approve_command(msg.content)
        if approve_match is not None and self._approval_resolve_fn is not None:
            request_id, decision = approve_match
            try:
                ok, text = await self._approval_resolve_fn(request_id, decision)
                return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=text)
            except Exception as e:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Approval resolve failed: {e!s}",
                )
        if approve_match is not None and self._approval_resolve_fn is None:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Approval resolve is not available in this context (e.g. CLI). Use: joyhousebot approvals resolve <id> allow-once|deny",
            )
        
        if len(session.messages) > self.memory_window:
            asyncio.create_task(self._consolidate_memory(session))

        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            max_context_tokens=self.max_context_tokens,
            scope_key=scope_key,
        )
        final_content, tools_used, aborted, last_response = await self._run_agent_loop(
            initial_messages,
            stream_callback=stream_callback,
            execution_stream_callback=execution_stream_callback,
            check_abort_requested=check_abort_requested,
        )

        if aborted:
            return None

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        # Prepend response_prefix from config (e.g. "[{model}] ")
        messages_config = getattr(self.config, "messages", None) if self.config else None
        if messages_config and getattr(messages_config, "response_prefix", None):
            prefix_template = (messages_config.response_prefix or "").strip()
            if prefix_template:
                provider_name = self._resolve_provider_name_for_model(self.model) or ""
                identity_name = (
                    getattr(getattr(self.config, "agent", None), "name", None)
                    if self.config else None
                ) or "joyhousebot"
                prefix = resolve_response_prefix(
                    prefix_template,
                    {
                        "model": self.model or "",
                        "provider": provider_name,
                        "identityName": identity_name,
                        "identity": identity_name,
                    },
                )
                if prefix:
                    final_content = prefix + "\n" + final_content

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")
        
        session.add_message("user", msg.content)
        usage_kw: dict[str, Any] = {"tools_used": tools_used if tools_used else None}
        if last_response and last_response.usage:
            usage_kw["usage"] = dict(last_response.usage)
        session.add_message("assistant", final_content, **usage_kw)
        self.sessions.save(session)
        
        reply_to: str | None = None
        if msg.metadata and "message_id" in msg.metadata:
            mid = msg.metadata["message_id"]
            reply_to = str(mid) if mid is not None else None
        
        sending_event = MessageSendingEvent(
            to_id=msg.channel,
            content=final_content,
            metadata=dict(msg.metadata) if msg.metadata else {},
        )
        sending_result = await hook_dispatcher.emit_first_result(
            HookName.MESSAGE_SENDING, sending_event, hook_ctx
        )
        
        if sending_result and isinstance(sending_result, MessageSendingResult):
            if sending_result.cancel:
                logger.info("Message sending cancelled by hook")
                return None
            if sending_result.content is not None:
                final_content = sending_result.content
        
        outbound = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            reply_to=reply_to,
            metadata=msg.metadata or {},
        )
        
        sent_event = MessageSentEvent(
            to_id=msg.channel,
            content=final_content,
        )
        await hook_dispatcher.emit(HookName.MESSAGE_SENT, sent_event, hook_ctx)
        
        return outbound

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        scope_key = self._resolve_memory_scope_key(
            session_key,
            getattr(msg, "sender_id", "") or "",
            getattr(msg, "metadata", None) or {},
        )
        self._set_tool_context(origin_channel, origin_chat_id)
        self._set_memory_scope(scope_key)
        initial_messages = self.context.build_messages(
            history=session.get_history(max_messages=self.memory_window),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
            max_context_tokens=self.max_context_tokens,
            scope_key=scope_key,
        )
        final_content, _, _, last_response = await self._run_agent_loop(initial_messages)

        if final_content is None:
            final_content = "Background task completed."

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        usage_kw: dict[str, Any] = {}
        if last_response and last_response.usage:
            usage_kw["usage"] = dict(last_response.usage)
        session.add_message("assistant", final_content, **usage_kw)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md.

        Args:
            archive_all: If True, clear all messages and reset session (for /new command).
                       If False, only write to files without modifying session.
        """
        scope_key = None
        if self.config:
            retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
            if retrieval:
                mode = getattr(retrieval, "memory_scope", "shared") or "shared"
                if mode == "session":
                    scope_key = session.key
                elif mode == "user":
                    scope_key = (session.metadata or {}).get("last_memory_scope_key") or session.key
        memory = MemoryStore(self.workspace, scope_key=scope_key)
        memory.ensure_memory_structure()

        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info(f"Memory consolidation (archive_all): {len(session.messages)} total messages archived")
        else:
            keep_count = self.memory_window // 2
            if len(session.messages) <= keep_count:
                logger.debug(f"Session {session.key}: No consolidation needed (messages={len(session.messages)}, keep={keep_count})")
                return

            messages_to_process = len(session.messages) - session.last_consolidated
            if messages_to_process <= 0:
                logger.debug(f"Session {session.key}: No new messages to consolidate (last_consolidated={session.last_consolidated}, total={len(session.messages)})")
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return
            logger.info(f"Memory consolidation started: {len(session.messages)} total, {len(old_messages)} new to consolidate, {keep_count} keep")

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")
        conversation = "\n".join(lines)
        raw_memory = memory.read_long_term()
        # Strip leading updated_at comment for prompt so LLM sees only body and does not echo it
        if raw_memory.startswith("<!-- updated_at=") and " -->" in raw_memory:
            current_memory = raw_memory.split(" -->", 1)[-1].lstrip("\n")
        else:
            current_memory = raw_memory

        # Optional OpenClaw-style memory flush: one LLM call to capture durable notes before consolidation
        flush_enabled = False
        flush_system = ""
        flush_prompt = ""
        try:
            from joyhousebot.config.access import get_config
            cfg = get_config()
            retrieval = getattr(getattr(cfg, "tools", None), "retrieval", None)
            if retrieval is not None:
                flush_enabled = getattr(retrieval, "memory_flush_before_consolidation", False)
                flush_system = getattr(retrieval, "memory_flush_system_prompt", "") or "Session nearing compaction. Output only valid JSON."
                flush_prompt = getattr(retrieval, "memory_flush_prompt", "") or "Write any lasting notes: return JSON with optional keys daily_log_entry and memory_additions. If nothing to store, return {}."
        except Exception:
            pass
        if flush_enabled and flush_prompt:
            try:
                flush_user = f"{flush_prompt}\n\n## Recent conversation\n{conversation[:4000]}"
                flush_response = await self.provider.chat(
                    messages=[
                        {"role": "system", "content": flush_system},
                        {"role": "user", "content": flush_user},
                    ],
                    model=self.model,
                )
                flush_text = (flush_response.content or "").strip()
                if flush_text.startswith("```"):
                    flush_text = flush_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                if flush_text:
                    flush_result = json_repair.loads(flush_text)
                    if isinstance(flush_result, dict):
                        if isinstance(flush_result.get("daily_log_entry"), str) and flush_result["daily_log_entry"].strip():
                            date_str = datetime.now(timezone.utc).date().isoformat()
                            memory.append_l2_daily(date_str, flush_result["daily_log_entry"].strip())
                        if isinstance(flush_result.get("memory_additions"), str) and flush_result["memory_additions"].strip():
                            raw_memory = memory.read_long_term()
                            body = raw_memory.split(" -->", 1)[-1].lstrip("\n") if (raw_memory.startswith("<!-- updated_at=") and " -->" in raw_memory) else raw_memory
                            memory.write_long_term(body.rstrip() + "\n\n" + flush_result["memory_additions"].strip(), updated_at=datetime.now(timezone.utc).isoformat())
                            raw_memory = memory.read_long_term()
                            current_memory = raw_memory.split(" -->", 1)[-1].lstrip("\n") if (raw_memory.startswith("<!-- updated_at=") and " -->" in raw_memory) else raw_memory
            except Exception as e:
                logger.debug(f"Memory flush before consolidation skipped: {e}")

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with these keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later. This will be appended to HISTORY.md and to memory/YYYY-MM-DD.md (daily log).

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged. If any existing long-term fact has been superseded or invalidated by this conversation, do not silently remove it; keep or briefly mention the old conclusion and add a clear new conclusion that explicitly supersedes it (e.g. "Previously: X. Now: Y." or "Supersedes: …"). You may tag items with [P0] (permanent), [P1] (e.g. 90-day), [P2] (e.g. 30-day) for lifecycle.

3. "l0_update" (optional): If you have a concise summary of active topics and retrieval hints (about 100–300 tokens), set this to the content for memory/.abstract. Omit the key or set to null if not needed.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            text = (response.content or "").strip()
            if not text:
                logger.warning("Memory consolidation: LLM returned empty response, skipping")
                return
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text)
            if not isinstance(result, dict):
                logger.warning(f"Memory consolidation: unexpected response type, skipping. Response: {text[:200]}")
                return

            history_max_entries = 0
            try:
                from joyhousebot.config.access import get_config
                cfg = get_config()
                retrieval = getattr(getattr(cfg, "tools", None), "retrieval", None)
                if retrieval is not None:
                    history_max_entries = getattr(retrieval, "history_max_entries", 0) or 0
            except Exception:
                pass

            if entry := result.get("history_entry"):
                memory.append_history(entry, max_entries=history_max_entries)
                date_str = datetime.now(timezone.utc).date().isoformat()
                memory.append_l2_daily(date_str, entry)
            if update := result.get("memory_update"):
                if update != current_memory:
                    memory.write_long_term(update, updated_at=datetime.now(timezone.utc).isoformat())
            if l0_update := result.get("l0_update"):
                if isinstance(l0_update, str) and l0_update.strip():
                    memory.update_l0_abstract(l0_update.strip())

            if archive_all:
                session.last_consolidated = 0
            else:
                session.last_consolidated = len(session.messages) - keep_count
            logger.info(f"Memory consolidation done: {len(session.messages)} messages, last_consolidated={session.last_consolidated}")
        except json.JSONDecodeError as e:
            logger.error(f"Memory consolidation JSON parse error: {e}")
        except LLMError as e:
            logger.error(f"Memory consolidation LLM error [{e.code}]: {e.message}")
        except ConnectionError as e:
            logger.error(f"Memory consolidation connection error: {sanitize_error_message(str(e))}")
        except Exception as e:
            code, category, _ = classify_exception(e)
            logger.error(f"Memory consolidation failed [{code}]: {sanitize_error_message(str(e))}")

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
    ) -> str | None:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
            stream_callback: If set, called with each content delta when provider supports streaming.
            execution_stream_callback: If set, called with (event_type, payload) for execution stream (e.g. /ws/agent-stream).
            check_abort_requested: If set, run can be aborted (e.g. chat.abort); when aborted returns None.

        Returns:
            The agent's response text, or None if run was aborted.
        """
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content
        )

        response = await self._process_message(
            msg,
            session_key=session_key,
            stream_callback=stream_callback,
            execution_stream_callback=execution_stream_callback,
            check_abort_requested=check_abort_requested,
        )
        return response.content if response else None
