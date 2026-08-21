from dataclasses import dataclass

import pytest

from joyhousebot.capabilities import CapabilityExtensionRegistry, CapabilityRegistry
from joyhousebot.contracts import (
    CapabilityContext,
    CapabilityResult,
    WriteReceipt,
)
from joyhousebot.contracts.capability_extensions import CapabilityExtensionManifest
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.postgres_store import PostgresTestStore

TEST_BUILD_DIGEST = f"sha256:{'0' * 64}"


@dataclass(frozen=True)
class Definition:
    name: str
    ref: object = None
    permissions: tuple[str, ...] = ()
    side_effect: str = "none"


@dataclass(frozen=True)
class Ref:
    capability_id: str
    version: str


class Handler:
    async def execute(self, context, input):
        return CapabilityResult(success=True, output={"user": context.user_id, **input})


def _extension_manifest(extension_id: str, version: str) -> CapabilityExtensionManifest:
    return CapabilityExtensionManifest(
        extension_id=extension_id,
        version=version,
        name=extension_id,
        build_digest=TEST_BUILD_DIGEST,
        runtime_contract_version=2,
    )


class Plugin:
    extension_id = "demo"
    version = "1.0.0"

    def register(self, registry):
        registry.register_capability(
            Definition("demo.echo", Ref("demo.echo", "1.0.0")), Handler()
        )

    def manifest(self):
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="Demo",
            build_digest=TEST_BUILD_DIGEST,
        )


@pytest.mark.asyncio
async def test_extension_registry_registers_and_invokes_versioned_capability():
    registry = CapabilityExtensionRegistry()
    registry.register_extension(Plugin())
    result = await registry.invoke(
        "demo.echo", {"value": "ok"}, context=CapabilityContext("u", "s", "r")
    )
    assert result.success is True
    assert result.output == {"user": "u", "value": "ok"}
    assert registry.manifests()[0].name == "Demo"


def test_extension_registry_rejects_conflicting_capability():
    registry = CapabilityExtensionRegistry()
    registry.register_extension(Plugin())
    with pytest.raises(ValueError, match="already registered"):
        registry.register_capability(
            Definition("demo.echo", Ref("demo.echo", "1.0.0")), Handler()
        )


@pytest.mark.asyncio
async def test_extension_registry_enforces_declared_permissions():
    registry = CapabilityExtensionRegistry()
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


def test_runtime_adapter_preserves_plugin_definition_metadata():
    class MetadataPlugin:
        extension_id = "metadata"
        version = "1.0.0"

        def manifest(self):
            return _extension_manifest(self.extension_id, self.version)

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="metadata.echo",
                    ref=CapabilityRef("metadata.echo", "2.0.0", CapabilityKind.CAPABILITY),
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
    registry.register_extension(MetadataPlugin())
    adapter = registry._adapters["metadata.echo"]
    assert adapter.definition.timeout_seconds == 17
    assert adapter.definition.retryable is False
    assert adapter.definition.permissions == ("metadata.invoke",)
    assert adapter.definition.configuration == {"cache_ttl_seconds": 60}
    assert adapter.definition.origin["extension_id"] == "metadata"
    assert adapter.definition.origin["extension_version"] == "1.0.0"
    assert adapter.definition.origin["extension_build_digest"].startswith("sha256:")


@pytest.mark.asyncio
async def test_plugin_runtime_settings_disable_tools_and_pass_validated_configuration(tmp_path):
    observed = {}

    class SettingsHandler:
        async def execute(self, context, input):
            observed.update(context.metadata.get("capability_configuration") or {})
            observed["scenario_inputs"] = context.metadata.get("scenario_inputs")
            return CapabilityResult(success=True, output={"ok": True, **input})

    class SettingsPlugin:
        extension_id = "settings"
        version = "1.0.0"

        def manifest(self):
            return _extension_manifest(self.extension_id, self.version)

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="settings.echo", ref=CapabilityRef("settings.echo", "1.0.0", CapabilityKind.CAPABILITY),
                    description="", input_schema={"type": "object"}, output_schema={"type": "object"}, adapter="settings.echo",
                    configuration_schema={"type": "object", "additionalProperties": False, "properties": {"prefix": {"type": "string"}}},
                ), SettingsHandler(),
            )

    store = PostgresTestStore(tmp_path / "plugin-settings.db")
    registry = CapabilityRegistry(store=store)
    registry.register_extension(SettingsPlugin())
    definition = registry.get_definition("settings.echo", "1.0.0")
    assert definition is not None
    store.publish_capability(definition, actor_id="test:trusted-fixture")
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
async def test_plugin_receives_durable_action_and_idempotency_identity(tmp_path):
    observed = {}

    class IdentityHandler:
        async def execute(self, context, input):
            observed.update(context.metadata)
            observed["first_class_action_id"] = context.action_id
            observed["first_class_idempotency_key"] = context.idempotency_key
            return CapabilityResult(
                success=True,
                output={"ok": True},
                write_receipt=WriteReceipt(
                    action_id=context.action_id,
                    idempotency_key=context.idempotency_key,
                    provider_operation_id="business-write-1",
                ),
            )

    class IdentityPlugin:
        extension_id = "identity"
        version = "1.0.0"

        def manifest(self):
            return CapabilityExtensionManifest(
                extension_id=self.extension_id,
                version=self.version,
                name="Identity",
                build_digest=TEST_BUILD_DIGEST,
                runtime_contract_version=2,
            )

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="identity.write",
                    ref=CapabilityRef("identity.write", "1.0.0", CapabilityKind.CAPABILITY),
                    description="durable business write",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="identity.write",
                    side_effect="internal",
                ),
                IdentityHandler(),
            )

    store = PostgresTestStore(tmp_path / "plugin-action-identity.db")
    run = store.create_runtime_run(
        run_id="run",
        user_id="system",
        session_id="session",
        agent_id="default",
        kind="agent",
        prompt="write",
        options={},
    )[0]
    claimed = store.claim_runtime_run(run.run_id, worker_id="worker")
    assert claimed is not None
    store.create_runtime_turn(
        turn_id="turn_durable",
        run_id=run.run_id,
        task_id=None,
        turn_index=0,
        model="test",
        request_hash="request",
        worker_id="worker",
    )
    registry = CapabilityRegistry(store=store)
    registry.register_extension(IdentityPlugin())
    definition = registry.get_definition("identity.write", "1.0.0")
    assert definition is not None
    store.publish_capability(definition, actor_id="test:trusted-fixture")
    result = await registry.invoke_tool(
        "identity.write",
        {},
        context=ToolExecutionContext(
            run_id="run",
            session_key="session",
            channel="api",
            chat_id="chat",
            worker_id="worker",
            turn_id="turn_durable",
            turn_index=0,
            action_index=0,
        ),
    )
    assert result.ok is True
    assert observed["action_id"].startswith("act_")
    assert observed["idempotency_key"] == f"action:{observed['action_id']}"
    assert observed["first_class_action_id"] == observed["action_id"]
    assert observed["first_class_idempotency_key"] == observed["idempotency_key"]
    assert result.operation["idempotency_key"] == observed["idempotency_key"]


@pytest.mark.asyncio
async def test_plugin_write_receipt_must_echo_frozen_identity():
    class BadReceiptHandler:
        async def execute(self, context, input):
            return CapabilityResult(
                success=True,
                output={"ok": True},
                write_receipt=WriteReceipt(
                    action_id="different-action",
                    idempotency_key="different-key",
                ),
            )

    class WritePlugin:
        extension_id = "bad-receipt"
        version = "1.0.0"

        def manifest(self):
            return CapabilityExtensionManifest(
                extension_id=self.extension_id,
                version=self.version,
                name="Bad receipt",
                build_digest=TEST_BUILD_DIGEST,
                runtime_contract_version=2,
            )

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="bad-receipt.write",
                    ref=CapabilityRef(
                        "bad-receipt.write", "1.0.0", CapabilityKind.CONNECTOR
                    ),
                    description="business write",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="bad-receipt.write",
                    side_effect="write",
                ),
                BadReceiptHandler(),
            )

    extensions = CapabilityExtensionRegistry()
    extensions.register_extension(WritePlugin())
    result = await extensions.invoke(
        "bad-receipt.write",
        {},
        context=CapabilityContext(
            user_id="u",
            session_id="s",
            run_id="r",
            action_id="act_expected",
            idempotency_key="action:act_expected",
        ),
    )
    assert result.success is False
    assert result.error["code"] == "WRITE_IDENTITY_MISMATCH"


@pytest.mark.asyncio
async def test_plugin_structured_error_survives_the_native_dispatcher():
    class ErrorHandler:
        async def execute(self, context, input):
            return CapabilityResult(
                success=False,
                error={
                    "code": "REMOTE_UNAVAILABLE",
                    "message": "business API unavailable",
                    "retryable": True,
                },
            )

    class ErrorPlugin:
        extension_id = "error"
        version = "1.0.0"

        def manifest(self):
            return _extension_manifest(self.extension_id, self.version)

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="error.remote",
                    ref=CapabilityRef("error.remote", "1.0.0", CapabilityKind.CONNECTOR),
                    description="remote connector",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="error.remote",
                    side_effect="read",
                ),
                ErrorHandler(),
            )

    registry = CapabilityRegistry()
    registry.register_extension(ErrorPlugin())
    result = await registry.invoke_tool(
        "error.remote",
        {},
        context=ToolExecutionContext(
            run_id="run",
            session_key="session",
            channel="api",
            chat_id="chat",
        ),
    )
    assert result.ok is False
    assert result.error and result.error.code == "REMOTE_UNAVAILABLE"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_native_runtime_enforces_plugin_capability_permissions():
    class ProtectedPlugin:
        extension_id = "protected"
        version = "1.0.0"

        def manifest(self):
            return _extension_manifest(self.extension_id, self.version)

        def register(self, registry):
            registry.register_capability(
                CapabilityDefinition(
                    name="protected.read",
                    ref=CapabilityRef("protected.read", "1.0.0", CapabilityKind.CAPABILITY),
                    description="protected read",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    adapter="protected.read",
                    permissions=("protected.read",),
                ),
                Handler(),
            )

    registry = CapabilityRegistry()
    registry.register_extension(ProtectedPlugin())
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
