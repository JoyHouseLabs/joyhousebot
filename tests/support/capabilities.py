"""Capability definitions used only by unit and integration fixtures."""

from typing import Any

from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)

TEST_PLUGIN_DIGEST = f"sha256:{'0' * 64}"


def tool_definition(
    tool: Any,
    *,
    version: str = "1.0.0",
    extension_id: str = "test.plugin",
    extension_version: str = "1.0.0",
) -> CapabilityDefinition:
    """Bind an ad-hoc test Tool to an explicit immutable plugin release."""
    return CapabilityDefinition(
        ref=CapabilityRef(
            str(tool.name),
            version,
            CapabilityKind.CAPABILITY,
            extension_id,
            extension_version,
            TEST_PLUGIN_DIGEST,
        ),
        name=str(tool.name),
        description=str(tool.description),
        input_schema=dict(tool.parameters),
        output_schema={"type": "object"},
        adapter=f"test:{tool.__class__.__module__}.{tool.__class__.__name__}",
        timeout_seconds=max(1, int(getattr(tool, "timeout", 60) or 60)),
        idempotent=bool(getattr(tool, "idempotent", True)),
        retryable=bool(getattr(tool, "retryable", True)),
        side_effect=str(getattr(tool, "side_effect", "none") or "unknown"),
        data_classification=str(
            getattr(tool, "data_classification", "internal") or "internal"
        ),
    )


def register_capability_fixture(
    registry: Any,
    tool: Any,
    *,
    optional: bool = False,
    definition: CapabilityDefinition | None = None,
) -> CapabilityDefinition:
    """Register a test Tool and activate it when the Registry has a test store."""
    resolved = definition or tool_definition(tool)
    store = getattr(registry, "_store", None)
    publish = getattr(store, "publish_capability", None)
    if callable(publish):
        publish(resolved)
    registry.register_connector_capability(tool, definition=resolved, optional=optional)
    return resolved


__all__ = ["TEST_PLUGIN_DIGEST", "register_capability_fixture", "tool_definition"]
