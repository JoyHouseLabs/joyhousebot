"""Subagent manager for background task execution."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.bus.events import InboundMessage
from joyhousebot.bus.queue import MessageBus
from joyhousebot.providers.base import LLMProvider
from joyhousebot.agent.tools.registry import ToolRegistry
from joyhousebot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.agent.tools.web import WebSearchTool, WebFetchTool
from joyhousebot.agent.auth_profiles import (
    classify_failover_reason,
    is_profile_available,
    load_profile_usage,
    mark_profile_failure,
    mark_profile_success,
    resolve_profile_order,
    save_profile_usage,
)


class SubagentManager:
    """
    Manages background subagent execution.
    
    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt.
    """
    
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        model_fallbacks: list[str] | None = None,
        config: Any | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        brave_api_key: str | None = None,
        exec_config: Any | None = None,
        restrict_to_workspace: bool = False,
    ):
        from joyhousebot.config.schema import ExecToolConfig
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        seen = {self.model}
        self.model_fallbacks: list[str] = []
        for raw in model_fallbacks or []:
            m = str(raw or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            self.model_fallbacks.append(m)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.config = config
        self._auth_profile_usage = load_profile_usage()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    def _resolve_provider_name_for_model(self, model: str) -> str:
        if "/" in model:
            return str(model.split("/", 1)[0]).strip()
        if self.config and hasattr(self.config, "get_provider_name"):
            resolved = self.config.get_provider_name(model)
            if resolved:
                return str(resolved).strip()
        return ""

    def _resolve_profile_candidates(self, provider_name: str) -> list[str | None]:
        if self.config is None or not provider_name:
            return [None]
        profile_ids = resolve_profile_order(self.config, provider_name)
        if not profile_ids:
            return [None]
        now_ms = time.time() * 1000
        available = [pid for pid in profile_ids if is_profile_available(self._auth_profile_usage, pid, now_ms)]
        in_cooldown = [pid for pid in profile_ids if pid not in available]
        return [None] + (available if available else in_cooldown)

    def _build_runtime_provider(self, *, model: str, profile_id: str | None):
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
    
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """
        Spawn a subagent to execute a task in the background.
        
        Args:
            task: The task description for the subagent.
            label: Optional human-readable label for the task.
            origin_channel: The channel to announce results to.
            origin_chat_id: The chat ID to announce results to.
        
        Returns:
            Status message indicating the subagent was started.
        """
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        
        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }
        
        # Create background task
        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task
        
        # Cleanup when done
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))
        
        logger.info(f"Spawned subagent [{task_id}]: {display_label}")
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."
    
    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label}")
        
        try:
            # Build subagent tools (no message tool, no spawn tool)
            optional_allowlist = []
            if self.config is not None:
                optional_allowlist = list(getattr(getattr(self.config, "tools", None), "optional_allowlist", []) or [])
            tools = ToolRegistry(optional_allowlist=optional_allowlist)
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(allowed_dir=allowed_dir))
            tools.register(WriteFileTool(allowed_dir=allowed_dir))
            tools.register(EditFileTool(allowed_dir=allowed_dir))
            tools.register(ListDirTool(allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                shell_mode=getattr(self.exec_config, "shell_mode", False),
                container_enabled=getattr(self.exec_config, "container_enabled", False),
                container_image=getattr(self.exec_config, "container_image", "alpine:3.18"),
                container_workspace_mount=getattr(self.exec_config, "container_workspace_mount", "") or "",
                container_user=getattr(self.exec_config, "container_user", "") or "",
                container_network=getattr(self.exec_config, "container_network", "none") or "none",
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key), optional=True)
            tools.register(WebFetchTool(), optional=True)
            
            # Build messages with subagent-specific prompt
            system_prompt = self._build_subagent_prompt(task)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
            
            # Run agent loop (limited iterations)
            max_iterations = 15
            iteration = 0
            final_result: str | None = None
            active_model = self.model
            
            while iteration < max_iterations:
                iteration += 1
                response = None
                for idx, candidate in enumerate([active_model] + [m for m in self.model_fallbacks if m != active_model]):
                    provider_name = self._resolve_provider_name_for_model(candidate)
                    profile_candidates = self._resolve_profile_candidates(provider_name)
                    for pidx, profile_id in enumerate(profile_candidates):
                        runtime_provider = self._build_runtime_provider(model=candidate, profile_id=profile_id)
                        response = await runtime_provider.chat(
                            messages=messages,
                            tools=tools.get_definitions(),
                            model=candidate,
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                        )
                        if response.finish_reason != "error":
                            if profile_id:
                                mark_profile_success(self._auth_profile_usage, profile_id)
                                save_profile_usage(self._auth_profile_usage)
                            if idx > 0:
                                logger.warning(f"Subagent [{task_id}] model fallback: {active_model} -> {candidate}")
                            active_model = candidate
                            break
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
                        if pidx < len(profile_candidates) - 1:
                            logger.warning(
                                f"Subagent [{task_id}] profile failed for {candidate}: {profile_id}, trying next profile"
                            )
                    if response and response.finish_reason != "error":
                        break
                if response is None:
                    response = await self.provider.chat(
                        messages=messages,
                        tools=tools.get_definitions(),
                        model=active_model,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                
                if response.has_tool_calls:
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                    })
                    
                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments)
                        logger.debug(f"Subagent [{task_id}] executing: {tool_call.name} with arguments: {args_str}")
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                        })
                else:
                    final_result = response.content
                    break
            
            if final_result is None:
                final_result = "Task completed but no final response was generated."
            
            logger.info(f"Subagent [{task_id}] completed successfully")
            await self._announce_result(task_id, label, task, final_result, origin, "ok")
            
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
            await self._announce_result(task_id, label, task, error_msg, origin, "error")
    
    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"
        
        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
        
        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        
        await self.bus.publish_inbound(msg)
        logger.debug(f"Subagent [{task_id}] announced result to {origin['channel']}:{origin['chat_id']}")
    
    def _build_subagent_prompt(self, task: str) -> str:
        """Build a focused system prompt for the subagent."""
        from datetime import datetime
        import time as _time
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"

        return f"""# Subagent

## Current Time
{now} ({tz})

You are a subagent spawned by the main agent to complete a specific task.

## Rules
1. Stay focused - complete only the assigned task, nothing else
2. Your final response will be reported back to the main agent
3. Do not initiate conversations or take on side tasks
4. Be concise but informative in your findings

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents
- Access the main agent's conversation history

## Workspace
Your workspace is at: {self.workspace}
Skills are available at: {self.workspace}/skills/ (read SKILL.md files as needed)

When you have completed the task, provide a clear summary of your findings or actions."""
    
    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
