"""Framework-owned registry for business capability plugins."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterable
from dataclasses import is_dataclass, replace
from hashlib import sha256
from importlib import metadata as importlib_metadata
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext, CapabilityResult
from joyhousebot.contracts.plugins import PluginManifest
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.utils.permissions import missing_permissions


class CapabilityPluginRegistry:
    """Register versioned plugin capabilities without framework dependencies."""

    def __init__(self) -> None:
        self._plugins: dict[str, Any] = {}
        self._capabilities: dict[str, tuple[Any, Any, str | None]] = {}
        self._projections: dict[str, tuple[Any, str | None]] = {}
        self._active_plugin: str | None = None

    def register_plugin(self, plugin: Any) -> None:
        plugin_id = str(getattr(plugin, "plugin_id", "")).strip()
        if not plugin_id:
            raise ValueError("plugin_id is required")
        version = str(getattr(plugin, "version", "")).strip()
        if not version:
            raise ValueError(f"plugin {plugin_id} version is required")
        existing = self._plugins.get(plugin_id)
        if existing is not None and str(getattr(existing, "version", "")) != version:
            raise ValueError(f"plugin {plugin_id} is already registered at another version")
        self._plugins[plugin_id] = plugin
        self._active_plugin = plugin_id
        try:
            plugin.register(self)
        finally:
            self._active_plugin = None

    def load_modules(self, modules: Iterable[str]) -> list[str]:
        """Load plugins from configured Python modules."""
        loaded: list[str] = []
        for module_name in modules:
            name = str(module_name).strip()
            if not name:
                continue
            module = importlib.import_module(name)
            factory = getattr(module, "create_plugin", None)
            plugin = factory() if callable(factory) else getattr(module, "plugin", None)
            if plugin is not None:
                self.register_plugin(plugin)
            else:
                register = getattr(module, "register", None)
                if not callable(register):
                    raise TypeError(
                        f"plugin module {name} must expose create_plugin, plugin, or register"
                    )
                self._active_plugin = name
                try:
                    register(self)
                finally:
                    self._active_plugin = None
            loaded.append(name)
        return loaded

    def load_entry_points(self, group: str = "joyhousebot.capabilities") -> list[str]:
        """Discover and register installed capability plugins."""
        entries = importlib_metadata.entry_points()
        selected = entries.select(group=group) if hasattr(entries, "select") else entries.get(group, ())
        loaded: list[str] = []
        for entry in selected:
            plugin = entry.load()
            if callable(plugin) and not hasattr(plugin, "register"):
                plugin = plugin()
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

    def register_projection(self, provider: Any) -> None:
        """Register one named business read model owned by the active plugin."""
        view_id = str(getattr(provider, "view_id", "")).strip()
        schema_version = int(getattr(provider, "schema_version", 0) or 0)
        if not view_id or schema_version < 1 or not callable(getattr(provider, "build", None)):
            raise ValueError("projection provider requires view_id, schema_version, and build")
        existing = self._projections.get(view_id)
        if existing is not None and existing[0] is not provider:
            raise ValueError(f"projection view {view_id} is already registered")
        self._projections[view_id] = (provider, self._active_plugin)

    def get_projection(self, view_id: str) -> Any | None:
        value = self._projections.get(str(view_id).strip())
        return value[0] if value else None

    def list_projections(self) -> tuple[Any, ...]:
        return tuple(value[0] for value in self._projections.values())

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
        """Return safe manifests, including a compatibility fallback."""
        values: list[PluginManifest] = []
        for plugin in self.plugins:
            values.append(self._manifest_for(plugin))
        return tuple(values)

    @staticmethod
    def _manifest_for(plugin: Any) -> PluginManifest:
        declared = getattr(plugin, "manifest", None)
        manifest = declared() if callable(declared) else None
        if not isinstance(manifest, PluginManifest):
            manifest = PluginManifest(
                plugin_id=str(plugin.plugin_id),
                version=str(plugin.version),
                name=str(plugin.plugin_id),
                description="External capability plugin",
            )
        if manifest.build_digest:
            return manifest
        # Package manifests must provide a source/build digest in production.
        # During local development derive one from the loaded plugin class so
        # every registered capability is still pinned to a concrete artifact.
        try:
            source = inspect.getsource(plugin.__class__).encode()
        except (OSError, TypeError):
            source = f"{plugin.__class__.__module__}:{plugin.__class__.__qualname__}".encode()
        return replace(manifest, build_digest=f"sha256:{sha256(source).hexdigest()}")

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
        return result


__all__ = ["CapabilityPluginRegistry"]
