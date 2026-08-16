"""ToolRuntime responsibilities for the shared Agent engine."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class ToolRuntimeMixin:
    async def _connect_tool_connectors(self) -> None:
        """Attach configured external Tool catalogs once per Agent worker."""
        if self._tool_connectors_connected or not self._tool_connector_settings:
            return
        async with self._tool_connector_lock:
            if self._tool_connectors_connected:
                return
            await self._reload_tool_connectors_locked()

    async def reload_tool_connectors(self) -> None:
        """Preflight a new connector generation, then swap it without a traffic gap."""
        async with self._tool_connector_lock:
            await self._reload_tool_connectors_locked()

    async def _reload_tool_connectors_locked(self) -> None:
        from porthouse.capabilities import CapabilityRegistry

        settings = self._effective_tool_connector_settings()
        stack = AsyncExitStack()
        await stack.__aenter__()
        staged = CapabilityRegistry()
        try:
            await self.tool_connectors.connect_configured(
                settings,
                capability_registry=staged,
                lifecycle=stack,
            )
            for manifest in self.tool_connectors.manifests():
                plugin_id = str(manifest.extension_id)
                self.capabilities.replace_tools_for_plugin(
                    plugin_id,
                    staged.registered_tools_for_plugin(plugin_id),
                )
        except BaseException:
            await stack.aclose()
            raise
        previous = self._tool_connector_stack
        if previous is not None:
            # In-flight invocations may still own clients from the previous
            # generation. Keep that lifecycle alive until orderly shutdown.
            self._retired_tool_connector_stacks.append(previous)
        self._tool_connector_stack = stack
        self._tool_connector_settings = settings
        self._tool_connectors_connected = True
