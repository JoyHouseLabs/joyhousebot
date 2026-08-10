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
            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                await self.tool_connectors.connect_configured(
                    self._tool_connector_settings,
                    capability_registry=self.capabilities,
                    lifecycle=stack,
                )
                self._tool_connector_stack = stack
                self._tool_connectors_connected = True
            except BaseException:
                await stack.aclose()
                raise
