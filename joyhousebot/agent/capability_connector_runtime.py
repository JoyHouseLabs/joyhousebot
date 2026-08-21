"""Capability Connector lifecycle for the shared Agent engine."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class CapabilityConnectorRuntimeMixin:
    async def connect_capability_connectors(self) -> None:
        """Attach configured external Capability catalogs once per Agent worker."""
        if self._capability_connectors_connected or not self._capability_connector_settings:
            return
        async with self._capability_connector_lock:
            if self._capability_connectors_connected:
                return
            await self._reload_capability_connectors_locked()

    async def reload_capability_connectors(self) -> None:
        """Preflight a new connector generation, then swap it without a traffic gap."""
        async with self._capability_connector_lock:
            await self._reload_capability_connectors_locked()

    async def _reload_capability_connectors_locked(self) -> None:
        from joyhousebot.capabilities import CapabilityRegistry

        settings = self._effective_capability_connector_settings()
        stack = AsyncExitStack()
        await stack.__aenter__()
        staged = CapabilityRegistry()
        try:
            await self.capability_connectors.connect_configured(
                settings,
                capability_registry=staged,
                lifecycle=stack,
            )
            for manifest in self.capability_connectors.manifests():
                extension_id = str(manifest.extension_id)
                self.capabilities.replace_capabilities_for_extension(
                    extension_id,
                    staged.registered_capabilities_for_extension(extension_id),
                )
        except BaseException:
            await stack.aclose()
            raise
        previous = self._capability_connector_stack
        if previous is not None:
            # In-flight invocations may still own clients from the previous
            # generation. Keep that lifecycle alive until orderly shutdown.
            self._retired_capability_connector_stacks.append(previous)
        self._capability_connector_stack = stack
        self._capability_connector_settings = settings
        self._capability_connectors_connected = True
