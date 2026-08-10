"""Framework-owned registry for business capability plugins."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import is_dataclass, replace
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext, CapabilityResult
from joyhousebot.contracts.plugins import (
    PluginComponent,
    PluginManifest,
)
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.extension_discovery import enabled_entry_points, validate_manifest
from joyhousebot.utils.permissions import missing_permissions


class CapabilityPluginRegistry:
    """Register versioned plugin capabilities without framework dependencies."""

    def __init__(self) -> None:
        self._plugins: dict[str, Any] = {}
        self._capabilities: dict[str, tuple[Any, Any, str | None]] = {}
        self._components: dict[str, tuple[PluginComponent, Any | None, str]] = {}
        self._active_plugin: str | None = None

    def register_plugin(self, plugin: Any) -> None:
        plugin_id = str(getattr(plugin, "plugin_id", "")).strip()
        if not plugin_id:
            raise ValueError("plugin_id is required")
        version = str(getattr(plugin, "version", "")).strip()
        if not version:
            raise ValueError(f"plugin {plugin_id} version is required")
        manifest = self._manifest_for(plugin)
        if manifest.plugin_id != plugin_id or manifest.version != version:
            raise ValueError("plugin identity does not match its manifest")
        existing = self._plugins.get(plugin_id)
        if existing is not None and str(getattr(existing, "version", "")) != version:
            raise ValueError(f"plugin {plugin_id} is already registered at another version")
        self._plugins[plugin_id] = plugin
        self._active_plugin = plugin_id
        try:
            plugin.register(self)
        finally:
            self._active_plugin = None

    def load_entry_points(
        self,
        group: str = "joyhousebot.capabilities",
        *,
        enabled: Iterable[str] | None = None,
    ) -> list[str]:
        """Discover installed capability plugins and register enabled releases."""
        allowed = {str(item).strip() for item in enabled or () if str(item).strip()}
        loaded: list[str] = []
        for entry in enabled_entry_points(group, allowed):
            plugin = entry.load()
            if callable(plugin) and not hasattr(plugin, "register"):
                plugin = plugin()
            if str(getattr(plugin, "plugin_id", "")) != str(entry.name):
                raise ValueError(
                    f"entry point {entry.name!r} does not match plugin id "
                    f"{getattr(plugin, 'plugin_id', None)!r}"
                )
            manifest = self._manifest_for(plugin)
            validate_manifest(
                manifest.to_extension_manifest(),
                entry_name=str(entry.name),
                expected_type="capability",
            )
            self.register_plugin(plugin)
            loaded.append(entry.name)
        return loaded

    def register_capability(self, definition: Any, handler: Any) -> None:
        ref = getattr(definition, "ref", None)
        capability_id = str(getattr(ref, "capability_id", "") or getattr(definition, "name", ""))
        version = str(getattr(ref, "version", "1.0.0"))
        if not capability_id or handler is None or not callable(getattr(handler, "execute", None)):
            raise ValueError("capability definition and async handler are required")
        if (
            self._active_plugin
            and is_dataclass(definition)
        ):
            plugin = self._plugins.get(self._active_plugin)
            if plugin is None:
                raise ValueError("capability registration has no active plugin")
            manifest = self._manifest_for(plugin)
            side_effect = str(getattr(definition, "side_effect", "unknown") or "unknown")
            if (
                side_effect.strip().lower() not in {"none", "read"}
                and manifest.runtime_contract_version < 2
            ):
                raise ValueError(
                    f"side-effecting capability {capability_id}@{version} requires "
                    "plugin runtime_contract_version >= 2"
                )
            if isinstance(ref, CapabilityRef) and not ref.is_bound:
                definition = replace(
                    definition,
                    ref=CapabilityRef(
                        capability_id=capability_id,
                        version=version,
                        kind=ref.kind,
                        plugin_id=manifest.plugin_id,
                        plugin_version=manifest.version,
                        plugin_build_digest=manifest.build_digest,
                    ),
                )
                ref = definition.ref
            if isinstance(ref, CapabilityRef) and (
                ref.plugin_id != manifest.plugin_id
                or ref.plugin_version != manifest.version
                or ref.plugin_build_digest != manifest.build_digest
            ):
                raise ValueError(
                    f"capability {capability_id}@{version} is not bound to its active plugin release"
                )
            if "origin" in getattr(definition, "__dataclass_fields__", {}) and not getattr(definition, "origin", None):
                definition = replace(
                    definition,
                    origin={
                        "plugin_id": manifest.plugin_id,
                        "plugin_version": manifest.version,
                        "plugin_build_digest": manifest.build_digest,
                    },
                )
        key = f"{capability_id}@{version}"
        existing = self._capabilities.get(key)
        if existing is not None and existing[:2] != (definition, handler):
            raise ValueError(f"capability {key} is already registered")
        self._capabilities[key] = (definition, handler, self._active_plugin)

    def register_component(
        self, component: PluginComponent, provider: Any | None = None
    ) -> None:
        """Register a business-owned component without teaching core its internals."""
        if not self._active_plugin or self._active_plugin not in self._plugins:
            raise ValueError("component registration requires an active plugin")
        key = f"{self._active_plugin}:{component.component_id}"
        existing = self._components.get(key)
        value = (component, provider, self._active_plugin)
        if existing is not None and existing[:2] != value[:2]:
            raise ValueError(f"plugin component {component.component_id} is already registered")
        self._components[key] = value

    def list_components(self, plugin_id: str | None = None) -> tuple[PluginComponent, ...]:
        return tuple(
            value[0]
            for value in self._components.values()
            if plugin_id is None or value[2] == plugin_id
        )

    def get_component_provider(self, plugin_id: str, component_id: str) -> Any | None:
        value = self._components.get(f"{plugin_id}:{component_id}")
        return value[1] if value else None

    def get(self, capability_id: str, version: str | None = None) -> tuple[Any, Any] | None:
        prefix = f"{capability_id}@"
        if version is not None:
            value = self._capabilities.get(f"{capability_id}@{version}")
            return value[:2] if value else None
        matches = [value for key, value in self._capabilities.items() if key.startswith(prefix)]
        if not matches:
            return None
        return matches[-1][:2]

    def list_capabilities(self) -> list[Any]:
        return [value[0] for value in self._capabilities.values()]

    @property
    def plugins(self) -> tuple[Any, ...]:
        return tuple(self._plugins.values())

    def manifests(self) -> tuple[PluginManifest, ...]:
        """Return safe manifests for registered plugin releases."""
        values: list[PluginManifest] = []
        for plugin in self.plugins:
            values.append(self._manifest_for(plugin))
        return tuple(values)

    @staticmethod
    def _manifest_for(plugin: Any) -> PluginManifest:
        declared = getattr(plugin, "manifest", None)
        manifest = declared() if callable(declared) else None
        if not isinstance(manifest, PluginManifest):
            raise TypeError(f"plugin {plugin.plugin_id!r} must declare a PluginManifest")
        return manifest

    async def invoke(
        self,
        capability_id: str,
        input: dict[str, Any],
        *,
        context: CapabilityContext,
        version: str | None = None,
    ) -> CapabilityResult:
        resolved = self.get(capability_id, version)
        if resolved is None:
            return CapabilityResult(success=False, error={"code": "CAPABILITY_NOT_FOUND", "message": capability_id})
        definition, handler = resolved
        requires_action = (
            str(getattr(definition, "side_effect", "unknown") or "unknown")
            .strip()
            .lower()
            not in {"none", "read"}
        )
        if requires_action and (not context.action_id or not context.idempotency_key):
            return CapabilityResult(
                success=False,
                error={
                    "code": "DURABLE_ACTION_REQUIRED",
                    "message": "side-effecting capability requires a frozen Action identity",
                },
            )
        required = set(getattr(definition, "permissions", ()) or ())
        granted = set((context.metadata or {}).get("permissions", ()) or ())
        missing = missing_permissions(granted, required)
        if missing:
            return CapabilityResult(
                success=False,
                error={"code": "PERMISSION_DENIED", "message": f"missing permissions: {', '.join(missing)}"},
            )
        result = handler.execute(context, input)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, CapabilityResult):
            raise TypeError(f"capability handler {capability_id} returned an invalid result")
        if requires_action and result.success:
            receipt = result.write_receipt
            if (
                receipt is None
                or receipt.action_id != context.action_id
                or receipt.idempotency_key != context.idempotency_key
            ):
                return CapabilityResult(
                    success=False,
                    error={
                        "code": "WRITE_IDENTITY_MISMATCH",
                        "message": "business write did not acknowledge the frozen Action identity",
                    },
                )
        return result


__all__ = ["CapabilityPluginRegistry"]
