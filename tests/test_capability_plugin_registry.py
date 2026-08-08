import sys
import types
from dataclasses import dataclass

import pytest

from joyhousebot.capabilities import CapabilityPluginRegistry, CapabilityRegistry
from joyhousebot.contracts import CapabilityContext, CapabilityResult, ProjectionContext
from joyhousebot.contracts.plugins import PluginManifest
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.postgres_store import PostgresTestStore


@dataclass(frozen=True)
class Definition:
    name: str
    ref: object = None
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ref:
    capability_id: str
    version: str


class Handler:
    async def execute(self, context, input):
        return CapabilityResult(success=True, output={"user": context.user_id, **input})


class Plugin:
    plugin_id = "demo"
    version = "1.0.0"

    def register(self, registry):
        registry.register_capability(
            Definition("demo.echo", Ref("demo.echo", "1.0.0")), Handler()
        )

    def manifest(self):
        return PluginManifest(plugin_id=self.plugin_id, version=self.version, name="Demo")


@pytest.mark.asyncio
async def test_plugin_registry_registers_and_invokes_versioned_capability():
    registry = CapabilityPluginRegistry()
    registry.register_plugin(Plugin())
    result = await registry.invoke(
        "demo.echo", {"value": "ok"}, context=CapabilityContext("u", "s", "r")
    )
    assert result.success is True
    assert result.output == {"user": "u", "value": "ok"}
    assert registry.manifests()[0].name == "Demo"


def test_plugin_registry_rejects_conflicting_capability():
    registry = CapabilityPluginRegistry()
    registry.register_plugin(Plugin())
    with pytest.raises(ValueError, match="already registered"):
        registry.register_capability(
            Definition("demo.echo", Ref("demo.echo", "1.0.0")), Handler()
        )


def test_plugin_registry_owns_named_projection_providers():
    class Provider:
        view_id = "demo.search"
        schema_version = 1

        def build(self, context):
            return {"view": self.view_id, "run": context.run}

    class ProjectionPlugin:
        plugin_id = "projection-demo"
        version = "1.0.0"

        def register(self, registry):
            registry.register_projection(Provider())

    registry = CapabilityPluginRegistry()
    registry.register_plugin(ProjectionPlugin())
    provider = registry.get_projection("demo.search")
    assert provider is not None
    assert provider.build(ProjectionContext(run={"run_id": "run-1"}))["run"]["run_id"] == "run-1"
    assert registry.get_projection("dinq.search") is None


@pytest.mark.asyncio
async def test_plugin_registry_enforces_declared_permissions():
    registry = CapabilityPluginRegistry()
    registry.register_capability(
        Definition("secure.echo", Ref("secure.echo", "1.0.0"), ("secure.invoke",)),
        Handler(),
    )
    denied = await registry.invoke(
        "secure.echo", {}, context=CapabilityContext("u", "s", "r")
    )
    assert denied.success is False
    assert denied.error["code"] == "PERMISSION_DENIED"
    allowed = await registry.invoke(
        "secure.echo",
        {},
        context=CapabilityContext("u", "s", "r", metadata={"permissions": ["secure.invoke"]}),
    )
    assert allowed.success is True


def test_plugin_registry_loads_configured_module(monkeypatch):
    module = types.ModuleType("test_capability_plugin_module")

    def register(registry):
        registry.register_capability(
            Definition("demo.module", Ref("demo.module", "1.0.0")), Handler()
        )

    module.register = register
    monkeypatch.setitem(sys.modules, module.__name__, module)
    registry = CapabilityPluginRegistry()
    with pytest.raises(ValueError, match="active plugin"):
        registry.load_modules([module.__name__])


def test_runtime_adapter_preserves_plugin_definition_metadata():
    class MetadataPlugin:
        plugin_id = "metadata"
        version = "1.0.0"

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="metadata.echo",
                    ref=CapabilityRef("metadata.echo", "2.0.0", CapabilityKind.TOOL),
                    description="echo with explicit runtime policy",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="test.metadata.echo",
                    timeout_seconds=17,
                    retryable=False,
                    permissions=("metadata.invoke",),
                    configuration={"cache_ttl_seconds": 60},
                ),
                Handler(),
            )

    registry = CapabilityRegistry()
    registry.register_plugin(MetadataPlugin())
    adapter = registry._adapters["metadata.echo"]
    assert adapter.definition.timeout_seconds == 17
    assert adapter.definition.retryable is False
    assert adapter.definition.permissions == ("metadata.invoke",)
    assert adapter.definition.configuration == {"cache_ttl_seconds": 60}
    assert adapter.definition.origin["plugin_id"] == "metadata"
    assert adapter.definition.origin["plugin_version"] == "1.0.0"
    assert adapter.definition.origin["plugin_build_digest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_plugin_runtime_settings_disable_tools_and_pass_validated_configuration(tmp_path):
    observed = {}

    class SettingsHandler:
        async def execute(self, context, input):
            observed.update(context.metadata.get("capability_configuration") or {})
            observed["scenario_inputs"] = context.metadata.get("scenario_inputs")
            return CapabilityResult(success=True, output={"ok": True, **input})

    class SettingsPlugin:
        plugin_id = "settings"
        version = "1.0.0"

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="settings.echo", ref=CapabilityRef("settings.echo", "1.0.0", CapabilityKind.TOOL),
                    description="", input_schema={"type": "object"}, output_schema={"type": "object"}, adapter="settings.echo",
                    configuration_schema={"type": "object", "additionalProperties": False, "properties": {"prefix": {"type": "string"}}},
                ), SettingsHandler(),
            )

    store = PostgresTestStore(tmp_path / "plugin-settings.db")
    registry = CapabilityRegistry(store=store)
    registry.register_plugin(SettingsPlugin())
    store.save_capability_runtime_settings("settings.echo", enabled=True, configuration={"prefix": "configured"}, actor_id="admin")
    context = ToolExecutionContext(
        run_id="run", session_key="session", channel="api", chat_id="chat",
        metadata={"scenario_inputs": {"must_have": ["verified"]}},
    )
    result = await registry.invoke_tool("settings.echo", {}, context=context)
    assert result.ok and observed == {
        "prefix": "configured",
        "scenario_inputs": {"must_have": ["verified"]},
    }
    store.save_capability_runtime_settings("settings.echo", enabled=False, configuration={}, actor_id="admin")
    assert registry.get_tool("settings.echo") is None


@pytest.mark.asyncio
async def test_native_runtime_enforces_plugin_capability_permissions():
    class ProtectedPlugin:
        plugin_id = "protected"
        version = "1.0.0"

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="protected.read",
                    ref=CapabilityRef("protected.read", "1.0.0", CapabilityKind.TOOL),
                    description="protected read",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="protected.read",
                    permissions=("protected.read",),
                ),
                Handler(),
            )

    registry = CapabilityRegistry()
    registry.register_plugin(ProtectedPlugin())
    base = ToolExecutionContext(run_id="run", session_key="session", channel="api", chat_id="chat")
    denied = await registry.invoke_tool("protected.read", {}, context=base)
    assert denied.ok is False
    assert denied.error and denied.error.code == "PERMISSION_DENIED"
    assert registry.get_tool_definitions(base) == []

    allowed = ToolExecutionContext(
        run_id="run",
        session_key="session",
        channel="api",
        chat_id="chat",
        granted_permissions=frozenset({"protected.*"}),
    )
    result = await registry.invoke_tool("protected.read", {}, context=allowed)
    assert result.ok is True
    assert [item["function"]["name"] for item in registry.get_tool_definitions(allowed)] == [
        "protected.read"
    ]
