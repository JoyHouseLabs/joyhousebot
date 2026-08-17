"""Versioned capability registry exposed to the model provider."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from porthouse.capabilities.dispatcher import CapabilityDispatcher
from porthouse.capabilities.plugin_registry import CapabilityPluginRegistry
from porthouse.capabilities.services import CapabilityServiceBroker
from porthouse.capabilities.tool_adapter import (
    ToolCapabilityAdapter,
    ToolInvocationError,
    ToolOutput,
)
from porthouse.contracts import CapabilityContext, OperationReconciliationResult
from porthouse.contracts.tools import Tool
from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityRef,
    CapabilityResult,
    InvocationStatus,
)
from porthouse.runtime.context import ToolExecutionContext
from porthouse.runtime.permissions import permission_engine
from porthouse.utils.permissions import missing_permissions


class _PluginTool(Tool):
    """Expose a framework-independent plugin handler to the native tool path."""

    def __init__(
        self,
        definition: Any,
        handler: Any,
        runtime_settings: Any | None = None,
        runtime_services: CapabilityServiceBroker | None = None,
    ) -> None:
        self._definition = definition
        self._handler = handler
        self._runtime_settings = runtime_settings
        self._runtime_services = runtime_services

    @property
    def name(self) -> str:
        return str(self._definition.ref.capability_id)

    @property
    def description(self) -> str:
        return str(self._definition.description)

    @property
    def parameters(self) -> dict[str, Any]:
        return dict(self._definition.input_schema)

    @property
    def supports_reconciliation(self) -> bool:
        return callable(getattr(self._handler, "reconcile_operation", None))

    async def execute(self, **kwargs: Any) -> Any:
        tool_context = kwargs.pop("tool_context", None)
        if tool_context is None:
            raise ValueError("plugin capability requires tool context")
        settings = self._settings()
        if not settings["enabled"]:
            raise ToolInvocationError("CAPABILITY_DISABLED", f"Capability '{self.name}' is disabled by an operator")
        context = self._context(tool_context, settings)
        result = await self._handler.execute(context, kwargs)
        if not result.success:
            error = result.error or {}
            raise ToolInvocationError(
                str(error.get("code") or "PLUGIN_CAPABILITY_FAILED"),
                str(error.get("message") or "plugin capability failed"),
                retryable=bool(error.get("retryable", False)),
            )
        side_effect = str(getattr(self._definition, "side_effect", "unknown") or "unknown")
        write_operation = dict(result.operation or {})
        if side_effect.strip().lower() not in {"none", "read"}:
            receipt = result.write_receipt
            if receipt is None:
                raise ToolInvocationError(
                    "WRITE_RECEIPT_REQUIRED",
                    "side-effecting plugin capability must return a WriteReceipt",
                )
            if (
                receipt.action_id != context.action_id
                or receipt.idempotency_key != context.idempotency_key
            ):
                raise ToolInvocationError(
                    "WRITE_IDENTITY_MISMATCH",
                    "business write receipt does not match the frozen Runtime Action",
                )
            write_operation.update(receipt.to_dict())
        status = InvocationStatus(result.status)
        if isinstance(result.output, ToolOutput):
            return replace(
                result.output,
                status=status,
                operation=write_operation or result.output.operation,
            )
        data = (
            {"content": result.output}
            if isinstance(result.output, str)
            else {"output": result.output}
        )
        return ToolOutput(
            content=str(result.output or result.metadata.get("summary") or "accepted"),
            data=data,
            artifacts=tuple(item.to_dict() for item in result.artifacts),
            operation=write_operation or None,
            status=status,
        )

    async def reconcile_operation(
        self, operation: dict[str, Any], **kwargs: Any
    ) -> OperationReconciliationResult:
        reconcile = getattr(self._handler, "reconcile_operation", None)
        if not callable(reconcile):
            return OperationReconciliationResult(
                status="unknown", summary="plugin does not expose operation reconciliation"
            )
        tool_context = kwargs.get("tool_context")
        if tool_context is None:
            raise ValueError("plugin reconciliation requires tool context")
        return await reconcile(self._context(tool_context, self._settings()), operation)

    def _context(self, tool_context: Any, settings: dict[str, Any]) -> CapabilityContext:
        metadata = dict(getattr(tool_context, "metadata", {}) or {})
        # Capability handlers receive the grants that were frozen in the
        # Agent execution snapshot.  They are useful for independently
        # invokable plugins, while the dispatcher remains the authoritative
        # enforcement point for the native Tool path.
        metadata["permissions"] = sorted(tool_context.granted_permissions)
        # Business write APIs must deduplicate with the exact durable Action
        # identity chosen by the framework.  Keeping these values in metadata
        # preserves the stable public contract while preventing plugins from
        # inventing a weaker, process-local idempotency key.
        if getattr(tool_context, "action_id", None):
            metadata["action_id"] = tool_context.action_id
        if getattr(tool_context, "idempotency_key", None):
            metadata["idempotency_key"] = tool_context.idempotency_key
        # This is the only configuration contract plugins receive.  It is
        # run-scoped, non-secret, and has already been validated by the
        # control plane against the capability's declared JSON Schema.
        metadata["capability_configuration"] = settings["configuration"]
        metadata["channel"] = getattr(tool_context, "channel", "")
        metadata["chat_id"] = getattr(tool_context, "chat_id", "")
        # Nested, Core-owned composition (for example retrieval followed by a
        # configured reranker) must preserve the exact Run tool policy.  This
        # is provenance, not an extension-controlled permission grant: every
        # value is overwritten from the original ToolExecutionContext here.
        metadata["_porthouse_tool_policy"] = {
            "permission_mode": tool_context.permission_mode,
            "allowed_tools": sorted(tool_context.allowed_tools),
            "disallowed_tools": sorted(tool_context.disallowed_tools),
            "worker_id": tool_context.worker_id,
            "turn_id": tool_context.turn_id,
            "turn_index": tool_context.turn_index,
            "action_index": tool_context.action_index,
            "request_id": tool_context.request_id,
            "tracker_id": tool_context.tracker_id,
        }
        app_installation_id = None
        app_identity = dict(metadata.get("app") or {})
        if isinstance(app_identity, dict) and app_identity.get("installation_id"):
            app_installation_id = str(app_identity["installation_id"])
        return CapabilityContext(
            user_id=tool_context.user_id,
            session_id=tool_context.session_id,
            run_id=tool_context.run_id,
            task_id=tool_context.task_id,
            agent_id=tool_context.agent_id,
            request_id=getattr(tool_context, "request_id", None),
            action_id=getattr(tool_context, "action_id", None),
            idempotency_key=getattr(tool_context, "idempotency_key", None),
            memory_scope=getattr(tool_context, "memory_scope", None),
            memory_policy=dict(getattr(tool_context, "memory_policy", {}) or {}),
            root_run_id=getattr(tool_context, "root_run_id", None),
            app_installation_id=app_installation_id,
            services=self._runtime_services,
            metadata=metadata,
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
        enabled_plugins: set[str] | None = None,
        scratch_root: Any | None = None,
        outbound_sink: Any = None,
        subagent_manager: Any = None,
        schedule_service: Any = None,
        embedding_provider_resolver: Any = None,
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
        self._runtime_services = (
            CapabilityServiceBroker(
                store,
                scratch_root=scratch_root,
                outbound_sink=outbound_sink,
                subagent_manager=subagent_manager,
                schedule_service=schedule_service,
                embedding_provider_resolver=embedding_provider_resolver,
            )
            if any(
                item is not None
                for item in (
                    store,
                    scratch_root,
                    outbound_sink,
                    subagent_manager,
                    schedule_service,
                    embedding_provider_resolver,
                )
            )
            else None
        )
        self.dispatcher = CapabilityDispatcher(store)
        self.plugins = CapabilityPluginRegistry()
        if self._runtime_services is not None:
            self._runtime_services.context.set_rerank_executor(self._invoke_rerank)
        self.plugins.load_entry_points(enabled=enabled_plugins)
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
                self._store.discover_capability_release(definition)
        self._sync_plugin_catalog()

    def _sync_plugin_catalog(self) -> None:
        """Persist ownership metadata without coupling core to any business plugin."""
        if self._store is None or not hasattr(self._store, "upsert_plugin_release"):
            return
        definitions = self.plugins.list_capabilities()
        for manifest in self.plugins.manifests():
            self._store.upsert_plugin_release(manifest.to_dict())
            components = [
                item.to_dict() for item in self.plugins.list_components(manifest.plugin_id)
            ]
            component_ids = {item["component_id"] for item in components}
            for definition in definitions:
                origin = getattr(definition, "origin", {}) or {}
                if origin.get("plugin_id") != manifest.plugin_id:
                    continue
                component_id = f"{definition.ref.kind.value}:{definition.ref.capability_id}"
                if component_id in component_ids:
                    raise ValueError(f"duplicate plugin component: {component_id}")
                components.append(
                    {
                        "component_id": component_id,
                        "component_type": str(definition.ref.kind.value),
                        "name": str(definition.name),
                        "description": str(definition.description),
                        "reference_id": str(definition.ref.capability_id),
                        "reference_version": str(definition.ref.version),
                        "metadata": {
                            "adapter": str(definition.adapter),
                            "input_schema": dict(definition.input_schema),
                            "output_schema": dict(definition.output_schema),
                            "permissions": list(definition.permissions),
                            "connection_ids": list(definition.connection_ids),
                            "side_effect": str(definition.side_effect),
                            "invocation_concurrency": str(definition.invocation_concurrency),
                            "max_concurrent_invocations": int(definition.max_concurrent_invocations),
                        },
                    }
                )
            self._store.sync_plugin_components(
                manifest.plugin_id, manifest.version, components, replace=True
            )

    def register_capability(self, definition: Any, handler: Any) -> None:
        self.plugins.register_capability(definition, handler)
        capability_id = str(definition.ref.capability_id)
        adapter = ToolCapabilityAdapter(
            _PluginTool(
                definition,
                handler,
                self._runtime_settings,
                self._runtime_services,
            ),
            definition=definition,
        )
        self._adapters[capability_id] = adapter
        self._versioned_adapters[(capability_id, str(definition.ref.version))] = adapter

    async def invoke_capability(self, name: str, params: dict[str, Any], *, context: Any, version: str | None = None):
        return await self.plugins.invoke(name, params, context=context, version=version)

    async def _invoke_rerank(
        self,
        capability_context: CapabilityContext,
        *,
        capability_id: str,
        version: str,
        input: dict[str, Any],
    ) -> CapabilityResult:
        """Dispatch a retrieval rerank through the same durable tool boundary.

        Context Assets never imports a reranking implementation. It asks this
        narrow callback to invoke an exact, published capability and this
        method reconstructs the original Run policy before dispatching it.
        """
        if capability_id != "retrieval.rerank":
            raise ValueError("only retrieval.rerank may be nested by Context Assets")
        adapter = self._resolve_adapter(capability_id, version)
        if adapter is None or not self._enabled(capability_id, version):
            raise ValueError("configured rerank capability is not loaded and enabled")
        policy = dict(capability_context.metadata.get("_porthouse_tool_policy") or {})
        nested_context = ToolExecutionContext(
            run_id=capability_context.run_id,
            session_key=capability_context.session_id or capability_context.run_id,
            channel=str(capability_context.metadata.get("channel") or "runtime"),
            chat_id=str(capability_context.metadata.get("chat_id") or "runtime"),
            user_id=capability_context.user_id,
            agent_id=capability_context.agent_id or "default",
            session_id=capability_context.session_id,
            memory_scope=capability_context.memory_scope,
            memory_policy=dict(capability_context.memory_policy or {}),
            task_id=capability_context.task_id,
            root_run_id=capability_context.root_run_id,
            request_id=str(policy.get("request_id") or "") or None,
            tracker_id=str(policy.get("tracker_id") or "") or None,
            permission_mode=str(policy.get("permission_mode") or "default"),
            allowed_tools=frozenset(str(item) for item in policy.get("allowed_tools") or ()),
            disallowed_tools=frozenset(str(item) for item in policy.get("disallowed_tools") or ()),
            granted_permissions=frozenset(
                str(item) for item in capability_context.metadata.get("permissions") or ()
            ),
            worker_id=str(policy.get("worker_id") or "") or None,
            turn_id=str(policy.get("turn_id") or "") or None,
            turn_index=policy.get("turn_index"),
            action_index=policy.get("action_index"),
            metadata={
                **{
                    key: value
                    for key, value in capability_context.metadata.items()
                    if not key.startswith("_porthouse_")
                },
                "nested_capability": "retrieval.rerank",
            },
        )
        return await self.dispatcher.invoke_tool(
            adapter,
            input,
            context=nested_context,
            tool_call_id=f"nested-rerank:{capability_context.task_id or capability_context.run_id}",
        )

    def register_tool(
        self,
        tool: Tool,
        *,
        definition: CapabilityDefinition,
        optional: bool = False,
    ) -> None:
        adapter = ToolCapabilityAdapter(tool, definition=definition)
        self._adapters[tool.name] = adapter
        self._versioned_adapters[(tool.name, str(adapter.definition.ref.version))] = adapter
        if optional:
            self._optional.add(tool.name)
        else:
            self._optional.discard(tool.name)
        if self._store is not None:
            self._store.discover_capability_release(adapter.definition)

    def registered_tools_for_plugin(
        self, plugin_id: str
    ) -> list[tuple[Tool, CapabilityDefinition, bool]]:
        """Expose connector-owned registrations for an atomic catalog swap."""
        output: list[tuple[Tool, CapabilityDefinition, bool]] = []
        for (name, _version), adapter in self._versioned_adapters.items():
            if adapter.definition.ref.plugin_id != plugin_id:
                continue
            output.append((adapter.tool, adapter.definition, name in self._optional))
        return output

    def replace_tools_for_plugin(
        self,
        plugin_id: str,
        entries: list[tuple[Tool, CapabilityDefinition, bool]],
    ) -> None:
        """Atomically replace one Tool connector generation after preflight succeeds."""
        old_keys = {
            key
            for key, adapter in self._versioned_adapters.items()
            if adapter.definition.ref.plugin_id == plugin_id
        }
        old_names = {name for name, _version in old_keys}
        new_names = {tool.name for tool, _definition, _optional in entries}
        for key in old_keys:
            self._versioned_adapters.pop(key, None)
        for name in old_names - new_names:
            adapter = self._adapters.get(name)
            if adapter is not None and adapter.definition.ref.plugin_id == plugin_id:
                self._adapters.pop(name, None)
            self._optional.discard(name)
        for tool, definition, optional in entries:
            self.register_tool(tool, definition=definition, optional=optional)

    def get_tool(self, name: str, version: str | None = None) -> Tool | None:
        adapter = self._resolve_adapter(name, version)
        return adapter.tool if adapter and self._enabled(name, version) else None

    def has(self, name: str, version: str | None = None) -> bool:
        return self.get_tool(name, version) is not None

    def get_definition(
        self, name: str, version: str | None = None
    ) -> CapabilityDefinition | None:
        """Return the exact locally loaded definition for rollout preflight."""
        adapter = self._resolve_adapter(name, version)
        return adapter.definition if adapter is not None else None

    def get_tool_invocation_policy(
        self, name: str, version: str | None = None
    ) -> dict[str, Any]:
        """Return immutable safety metadata used by one model tool-call turn.

        This never consults mutable operator settings: enabling a Tool must not
        accidentally turn a write-capability into a concurrent operation.
        Unknown tools deliberately resolve to a sequential policy.
        """
        adapter = self._resolve_adapter(name, version)
        if adapter is None:
            return {"mode": "sequential", "max_concurrent": 1, "idempotent": False, "side_effect": "unknown"}
        definition = adapter.definition
        return {
            "mode": str(getattr(definition, "invocation_concurrency", "sequential")),
            "max_concurrent": max(1, int(getattr(definition, "max_concurrent_invocations", 1) or 1)),
            "idempotent": bool(getattr(definition, "idempotent", False)),
            "side_effect": str(getattr(definition, "side_effect", "unknown")),
        }

    @property
    def tool_names(self) -> list[str]:
        names = {name for name, _version in self._versioned_adapters}
        return sorted(name for name in names if self._resolve_adapter(name) and self._enabled(name))

    def get_tool_definitions(
        self, context: ToolExecutionContext | None = None
    ) -> list[dict[str, Any]]:
        definitions = []
        for name in self.tool_names:
            adapter = self._resolve_adapter(name)
            if adapter is not None and (
                context is None or self._is_authorized(adapter, context)
            ):
                definitions.append(adapter.tool.to_schema())
        return definitions

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
        adapter = self._resolve_adapter(name, version)
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
        if not self._enabled(name, version):
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

    def _resolve_adapter(
        self, name: str, version: str | None = None
    ) -> ToolCapabilityAdapter | None:
        if version is not None:
            return self._versioned_adapters.get((name, version))
        if self._store is not None and hasattr(self._store, "get_capability_definition"):
            active = self._store.get_capability_definition(name)
            if active is None:
                return None
            ref = dict(active.get("ref") or {})
            return self._versioned_adapters.get((name, str(ref.get("version") or "")))
        return self._adapters.get(name)

    def _enabled(self, name: str, version: str | None = None) -> bool:
        if name in self._optional and name not in self._allowlist:
            return False
        if self._store is not None and hasattr(self._store, "get_capability_definition"):
            published = self._store.get_capability_definition(name, version)
            adapter = self._resolve_adapter(name, version)
            if published is None or adapter is None:
                return False
            try:
                if adapter.definition.ref.identity != CapabilityRef.from_dict(
                    dict(published["ref"])
                ).identity:
                    return False
            except (KeyError, TypeError, ValueError):
                return False
        return bool(self._runtime_settings(name).get("enabled", True))

    @staticmethod
    def _missing_permissions(adapter: ToolCapabilityAdapter, context: ToolExecutionContext) -> list[str]:
        required = {
            str(item).strip()
            for item in (getattr(adapter.definition, "permissions", ()) or ())
            if str(item).strip()
        }
        return missing_permissions(context.granted_permissions, required)

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
        settings = self._store.get_capability_runtime_settings(capability_id)
        plugin_enabled = getattr(self._store, "is_plugin_execution_enabled", None)
        if callable(plugin_enabled):
            definition = self._store.get_capability_definition(capability_id)
            reference = dict((definition or {}).get("ref") or {})
            plugin_id = str(reference.get("plugin_id") or "").strip()
            if plugin_id and not plugin_enabled(plugin_id):
                return {
                    **settings,
                    "enabled": False,
                    "disabled_reason": "extension is inactive",
                }
        return settings
