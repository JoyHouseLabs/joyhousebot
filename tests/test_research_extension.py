import pytest
from porthouse_capability_research import ResearchCapabilityPlugin

from porthouse.capabilities import CapabilityPluginRegistry
from porthouse.contracts import CapabilityContext


def test_research_extension_registers_versioned_capabilities() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(ResearchCapabilityPlugin())
    search, _ = registry.get("web_search", "1.0.0")
    fetch, _ = registry.get("web_fetch", "1.0.0")
    assert search.permissions == ("network.search",)
    assert fetch.permissions == ("network.http.read",)
    assert search.ref.plugin_id == "capability-research"


@pytest.mark.asyncio
async def test_research_capability_is_permission_gated() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(ResearchCapabilityPlugin())
    result = await registry.invoke(
        "web_fetch",
        {"url": "https://example.com"},
        context=CapabilityContext("user", "session", "run", metadata={"permissions": []}),
    )
    assert result.success is False
    assert result.error["code"] == "PERMISSION_DENIED"
