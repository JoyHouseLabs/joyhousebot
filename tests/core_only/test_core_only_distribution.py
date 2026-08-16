"""Release gate for the extension-free Core distribution."""

from __future__ import annotations

import os
from importlib import metadata as importlib_metadata

import pytest

from porthouse.capabilities.plugin_registry import CapabilityPluginRegistry
from porthouse.channels.plugins.registry import ChannelRegistry
from porthouse.config.schema import Config
from porthouse.connectors.registry import ToolConnectorRegistry
from porthouse.extension_discovery import ENTRY_POINT_GROUPS, installed_extensions
from porthouse.providers.factory import create_model_provider
from porthouse.providers.registry import ModelProviderRegistry
from porthouse.providers.unconfigured import UnconfiguredModelProvider

pytestmark = pytest.mark.skipif(
    os.getenv("PORTHOUSE_CORE_ONLY_TEST") != "1",
    reason="runs in the isolated Core-only quality-gate environment",
)


def test_core_wheel_has_no_extension_distributions_or_entry_points() -> None:
    assert installed_extensions() == []
    distributions = {
        distribution.metadata.get("Name", "").lower()
        for distribution in importlib_metadata.distributions()
    }
    assert not {
        name
        for name in distributions
        if name.startswith(
            (
                "porthouse-capability-",
                "porthouse-channel-",
                "porthouse-connector-",
                "porthouse-provider-",
            )
        )
    }
    for group in ENTRY_POINT_GROUPS:
        assert not tuple(importlib_metadata.entry_points().select(group=group))


def test_empty_extension_configuration_builds_empty_registries() -> None:
    config = Config()
    assert config.extensions.enabled == []
    assert ModelProviderRegistry(enabled=()).specs() == ()
    assert ChannelRegistry().load_entry_points(enabled=()) == []
    assert ToolConnectorRegistry().load_entry_points(enabled=()) == []
    assert CapabilityPluginRegistry().load_entry_points(enabled=()) == []
    assert isinstance(
        create_model_provider(config=config, model="unconfigured/model"),
        UnconfiguredModelProvider,
    )
