"""Release gate for the extension-free Core distribution."""

from __future__ import annotations

import os
from importlib import metadata as importlib_metadata

import pytest

from joyhousebot.capabilities.extension_registry import CapabilityExtensionRegistry
from joyhousebot.channels.extensions.registry import ChannelExtensionRegistry
from joyhousebot.config.schema import Config
from joyhousebot.connectors.registry import CapabilityConnectorRegistry
from joyhousebot.extension_discovery import ENTRY_POINT_GROUPS, installed_extensions
from joyhousebot.providers.factory import create_model_provider
from joyhousebot.providers.registry import ModelProviderRegistry
from joyhousebot.providers.unconfigured import UnconfiguredModelProvider

pytestmark = pytest.mark.skipif(
    os.getenv("JOYHOUSEBOT_CORE_ONLY_TEST") != "1",
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
                "joyhousebot-capability-",
                "joyhousebot-channel-",
                "joyhousebot-connector-",
                "joyhousebot-provider-",
            )
        )
    }
    for group in ENTRY_POINT_GROUPS:
        assert not tuple(importlib_metadata.entry_points().select(group=group))


def test_empty_extension_configuration_builds_empty_registries() -> None:
    config = Config()
    assert config.extensions.allowed_ids == []
    assert ModelProviderRegistry(allowed_ids=()).specs() == ()
    assert ChannelExtensionRegistry().load_entry_points(allowed_ids=()) == []
    assert CapabilityConnectorRegistry().load_entry_points(allowed_ids=()) == []
    assert CapabilityExtensionRegistry().load_entry_points(allowed_ids=()) == []
    assert isinstance(
        create_model_provider(config=config, model="unconfigured/model"),
        UnconfiguredModelProvider,
    )
