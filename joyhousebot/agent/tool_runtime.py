"""ToolRuntime responsibilities for the shared Agent engine."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from joyhousebot.agent.tools.cron import CronTool
from joyhousebot.agent.tools.fetch_url_to_knowledgebase import FetchUrlToKnowledgebaseTool
from joyhousebot.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from joyhousebot.agent.tools.memory_get import MemoryGetTool
from joyhousebot.agent.tools.message import MessageTool
from joyhousebot.agent.tools.monitor_scratch import MonitorScratchTool
from joyhousebot.agent.tools.retrieve import RetrieveTool
from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.agent.tools.spawn import SpawnTool
from joyhousebot.agent.tools.web import WebFetchTool, WebSearchTool

if TYPE_CHECKING:
    pass


class ToolRuntimeMixin:
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools operate only inside the current root Run's scratch scope.
        allowed_dir = self.scratch_root if self.restrict_to_workspace else None
        self.capabilities.register_tool(
            ReadFileTool(
                allowed_dir=allowed_dir,
                workspace=self.scratch_root,
                runtime_store=self.runtime_store,
            ),
            optional=True,
        )
        self.capabilities.register_tool(
            WriteFileTool(
                allowed_dir=allowed_dir,
                workspace=self.scratch_root,
                runtime_store=self.runtime_store,
            ),
            optional=True,
        )
        self.capabilities.register_tool(
            EditFileTool(
                allowed_dir=allowed_dir,
                workspace=self.scratch_root,
                runtime_store=self.runtime_store,
            ),
            optional=True,
        )
        self.capabilities.register_tool(
            ListDirTool(
                allowed_dir=allowed_dir,
                workspace=self.scratch_root,
                runtime_store=self.runtime_store,
            ),
            optional=True,
        )

        # Shell tool (direct or Docker backend via exec_config.container_*)
        # Inject the configured environment only within that skill's workspace.
        self.capabilities.register_tool(
            ExecTool(
                working_dir=str(self.scratch_root),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                shell_mode=getattr(self.exec_config, "shell_mode", False),
                container_image=getattr(self.exec_config, "container_image", "alpine:3.18"),
                container_workspace_mount=getattr(self.exec_config, "container_workspace_mount", "")
                or "",
                container_user=getattr(self.exec_config, "container_user", "") or "",
                container_network=getattr(self.exec_config, "container_network", "none") or "none",
                container_memory=getattr(self.exec_config, "container_memory", "512m") or "512m",
                container_cpus=getattr(self.exec_config, "container_cpus", "1") or "1",
                container_pids_limit=getattr(self.exec_config, "container_pids_limit", 256) or 256,
            ),
            optional=True,
        )
        # Web tools
        self.capabilities.register_tool(WebSearchTool(api_key=self.brave_api_key), optional=True)
        self.capabilities.register_tool(WebFetchTool(), optional=True)

        # Knowledge base: retrieve (index from pipeline); optional fetch URL into knowledgebase; optional QMD for knowledge/memory
        self.capabilities.register_tool(RetrieveTool(runtime_store=self.runtime_store))
        self.capabilities.register_tool(
            FetchUrlToKnowledgebaseTool(runtime_store=self.runtime_store),
            optional=True,
        )
        self.capabilities.register_tool(MemoryGetTool(runtime_store=self.runtime_store))

        # Message tool
        message_tool = MessageTool(send_callback=self.outbound_sink)
        self.capabilities.register_tool(message_tool)

        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.capabilities.register_tool(spawn_tool)

        # Cron tool (for scheduling)
        if self.cron_service:
            self.capabilities.register_tool(
                CronTool(self.cron_service), optional=True, version="1.2.0"
            )
            # Private internal state is exposed on every Agent runtime, but the
            # Tool itself fails closed unless the immutable Run metadata proves
            # this is a scheduled Agent Monitor.
            self.capabilities.register_tool(MonitorScratchTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect configured platform MCP servers once per Agent worker."""
        if self._mcp_connected or not self._mcp_servers:
            return
        async with self._mcp_connect_lock:
            if self._mcp_connected:
                return
            from joyhousebot.agent.tools.mcp import connect_mcp_servers

            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                await connect_mcp_servers(self._mcp_servers, self.capabilities, stack)
                self._mcp_stack = stack
                self._mcp_connected = True
            except BaseException:
                await stack.aclose()
                raise
