"""Versioned capability registry exposed to the model provider."""

from __future__ import annotations

from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.capabilities.plugin_registry import CapabilityPluginRegistry
from joyhousebot.capabilities.tool_adapter import (
    ToolCapabilityAdapter,
    ToolInvocationError,
    ToolOutput,
)
from joyhousebot.contracts import CapabilityContext
from joyhousebot.domain.capabilities import CapabilityResult
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.runtime.permissions import permission_engine


class _PluginTool(Tool):
    """Expose a framework-independent plugin handler to the native tool path."""

    def __init__(self, definition: Any, handler: Any, runtime_settings: Any | None = None) -> None:
        self._definition = definition
        self._handler = handler
        self._runtime_settings = runtime_settings

    @property
    def name(self) -> str:
        return str(self._definition.ref.capability_id)

    @property
    def description(self) -> str:
        return str(self._definition.description)

    @property
    def parameters(self) -> dict[str, Any]:
        return dict(self._definition.input_schema)

    async def execute(self, **kwargs: Any) -> Any:
        tool_context = kwargs.pop("tool_context", None)
        if tool_context is None:
            raise ValueError("plugin capability requires tool context")
        settings = self._settings()
        if not settings["enabled"]:
            raise ToolInvocationError("CAPABILITY_DISABLED", f"Capability '{self.name}' is disabled by an operator")
        metadata = dict(getattr(tool_context, "metadata", {}) or {})
        # Capability handlers receive the grants that were frozen in the
        # Agent execution snapshot.  They are useful for independently
        # invokable plugins, while the dispatcher remains the authoritative
        # enforcement point for the native Tool path.
        metadata["permissions"] = sorted(tool_context.granted_permissions)
        # This is the only configuration contract plugins receive.  It is
        # run-scoped, non-secret, and has already been validated by the
        # control plane against the capability's declared JSON Schema.
        metadata["capability_configuration"] = settings["configuration"]
        context = CapabilityContext(
            user_id=tool_context.user_id,
            session_id=tool_context.session_id,
            run_id=tool_context.run_id,
            task_id=tool_context.task_id,
            agent_id=tool_context.agent_id,
            request_id=getattr(tool_context, "request_id", None),
            metadata=metadata,
        )
        result = await self._handler.execute(context, kwargs)
        if not result.success:
            message = (result.error or {}).get("message", "plugin capability failed")
            raise RuntimeError(message)
        if isinstance(result.output, ToolOutput):
            return result.output
        if isinstance(result.output, str):
            return ToolOutput(
                content=result.output,
                data={"content": result.output},
                artifacts=tuple(item.to_dict() for item in result.artifacts),
            )
        return ToolOutput(
            content=str(result.output),
            data={"output": result.output},
            artifacts=tuple(item.to_dict() for item in result.artifacts),
        )

    def _settings(self) -> dict[str, Any]:
        if self._runtime_settings is None:
            return {"enabled": True, "configuration": {}}
        value = self._runtime_settings(self.name)
        return {
            "enabled": bool(value.get("enabled", True)),
            "configuration": dict(value.get("configuration") or {}),
        }


class CapabilityRegistry:
    def __init__(
        self,
        *,
        store: Any | None = None,
        optional_allowlist: list[str] | None = None,
        plugin_modules: list[str] | None = None,
        discover_entry_points: bool = False,
    ) -> None:
        self._adapters: dict[str, ToolCapabilityAdapter] = {}
        # The model-facing catalog exposes one current adapter per name, but
        # durable Task/MCP execution must resolve the exact capability version
        # captured in CapabilityRef.  Never use the mutable current index for
        # a persisted invocation.
        self._versioned_adapters: dict[tuple[str, str], ToolCapabilityAdapter] = {}
        self._optional: set[str] = set()
        self._allowlist = {
            str(item).strip() for item in (optional_allowlist or []) if str(item).strip()
        }
        self._store = store
        self.dispatcher = CapabilityDispatcher(store)
        self.plugins = CapabilityPluginRegistry()
        if plugin_modules:
            self.plugins.load_modules(plugin_modules)
        if discover_entry_points:
            self.plugins.load_entry_points()
        self._sync_registered_capabilities()

    def register_plugin(self, plugin: Any) -> None:
        """Register a business plugin at the same capability boundary."""
        self.plugins.register_plugin(plugin)
        self._sync_registered_capabilities()

    def _sync_registered_capabilities(self) -> None:
        for definition in self.plugins.list_capabilities():
            ref = getattr(definition, "ref", None)
            resolved = self.plugins.get(
                str(getattr(ref, "capability_id", "")),
                str(getattr(ref, "version", "")),
            )
            if resolved is not None:
                self.register_capability(definition, resolved[1])
            if self._store is not None and hasattr(definition, "to_dict"):
                self._store.publish_capability(definition)
        self._sync_plugin_catalog()

    def _sync_plugin_catalog(self) -> None:
        """Persist ownership metadata without coupling core to any business plugin."""
        if self._store is None or not hasattr(self._store, "upsert_plugin_release"):
            return
        definitions = self.plugins.list_capabilities()
        for manifest in self.plugins.manifests():
            self._store.upsert_plugin_release(manifest.to_dict())
            components = []
            for definition in definitions:
                origin = getattr(definition, "origin", {}) or {}
                if origin.get("plugin_id") != manifest.plugin_id:
                    continue
                components.append(
                    {
                        "component_id": f"{definition.ref.kind.value}:{definition.ref.capability_id}",
                        "component_type": str(definition.ref.kind.value),
                        "name": str(definition.name),
                        "description": str(definition.description),
                        "reference_id": str(definition.ref.capability_id),
                        "reference_version": str(definition.ref.version),
                        "metadata": {"adapter": str(definition.adapter)},
                    }
                )
            self._store.sync_plugin_components(manifest.plugin_id, manifest.version, components)

    def register_capability(self, definition: Any, handler: Any) -> None:
        self.plugins.register_capability(definition, handler)
        capability_id = str(definition.ref.capability_id)
        adapter = ToolCapabilityAdapter(
            _PluginTool(definition, handler, self._runtime_settings),
            version=str(definition.ref.version),
            definition=definition,
        )
        self._adapters[capability_id] = adapter
        self._versioned_adapters[(capability_id, str(definition.ref.version))] = adapter

    async def invoke_capability(self, name: str, params: dict[str, Any], *, context: Any, version: str | None = None):
        return await self.plugins.invoke(name, params, context=context, version=version)

    def register_tool(self, tool: Tool, *, optional: bool = False) -> None:
        adapter = ToolCapabilityAdapter(tool)
        self._adapters[tool.name] = adapter
        self._versioned_adapters[(tool.name, str(adapter.definition.ref.version))] = adapter
        if optional:
            self._optional.add(tool.name)
        else:
            self._optional.discard(tool.name)
        if self._store is not None:
            self._store.publish_capability(adapter.definition)

    def get_tool(self, name: str, version: str | None = None) -> Tool | None:
        adapter = (
            self._versioned_adapters.get((name, version))
            if version is not None
            else self._adapters.get(name)
        )
        return adapter.tool if adapter and self._enabled(name) else None

    def has(self, name: str, version: str | None = None) -> bool:
        return self.get_tool(name, version) is not None

    @property
    def tool_names(self) -> list[str]:
        return [name for name in self._adapters if self._enabled(name)]

    def get_tool_definitions(
        self, context: ToolExecutionContext | None = None
    ) -> list[dict[str, Any]]:
        return [
            adapter.tool.to_schema()
            for name, adapter in self._adapters.items()
            if self._enabled(name)
            and (context is None or self._is_authorized(adapter, context))
        ]

    async def invoke_tool(
        self,
        name: str,
        params: dict[str, Any],
        *,
        context: ToolExecutionContext,
        tool_call_id: str | None = None,
        version: str | None = None,
        **kwargs: Any,
    ) -> CapabilityResult:
        adapter = (
            self._versioned_adapters.get((name, version))
            if version is not None
            else self._adapters.get(name)
        )
        if adapter is None:
            return CapabilityResult.failed(
                f"inv_{tool_call_id or 'unknown'}",
                code="CAPABILITY_NOT_FOUND",
                message=(
                    f"Capability '{name}@{version}' was not found"
                    if version is not None
                    else f"Capability '{name}' was not found"
                ),
            )
        if not self._enabled(name):
            return CapabilityResult.failed(
                f"inv_{tool_call_id or 'unknown'}",
                code="CAPABILITY_DISABLED",
                message=f"Capability '{name}' is disabled",
            )
        if not self._is_authorized(adapter, context):
            return CapabilityResult.failed(
                f"inv_{tool_call_id or 'unknown'}",
                code="PERMISSION_DENIED",
                message=self._authorization_error(adapter, context),
            )
        return await self.dispatcher.invoke_tool(
            adapter,
            params if isinstance(params, dict) else {},
            context=context,
            tool_call_id=tool_call_id,
            **kwargs,
        )

    def _enabled(self, name: str) -> bool:
        if name in self._optional and name not in self._allowlist:
            return False
        return bool(self._runtime_settings(name).get("enabled", True))

    @staticmethod
    def _missing_permissions(adapter: ToolCapabilityAdapter, context: ToolExecutionContext) -> list[str]:
        required = {
            str(item).strip()
            for item in (getattr(adapter.definition, "permissions", ()) or ())
            if str(item).strip()
        }
        granted = set(context.granted_permissions)
        wildcard = "*" in granted
        missing = [
            permission
            for permission in sorted(required)
            if not wildcard
            and permission not in granted
            and not any(
                grant.endswith(".*") and permission.startswith(grant[:-1])
                for grant in granted
            )
        ]
        return missing

    def _is_authorized(self, adapter: ToolCapabilityAdapter, context: ToolExecutionContext) -> bool:
        return permission_engine.evaluate(adapter.tool.name, context).allowed and not self._missing_permissions(adapter, context)

    def _authorization_error(self, adapter: ToolCapabilityAdapter, context: ToolExecutionContext) -> str:
        decision = permission_engine.evaluate(adapter.tool.name, context)
        if not decision.allowed:
            return decision.reason
        missing = self._missing_permissions(adapter, context)
        return f"Missing capability permissions: {', '.join(missing)}"

    def _runtime_settings(self, capability_id: str) -> dict[str, Any]:
        if self._store is None or not hasattr(self._store, "get_capability_runtime_settings"):
            return {"enabled": True, "configuration": {}}
        return self._store.get_capability_runtime_settings(capability_id)
