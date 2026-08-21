import pytest
from joyhousebot_capability_research import ResearchCapabilityExtension

from joyhousebot.capabilities import CapabilityExtensionRegistry
from joyhousebot.contracts import CapabilityContext


def test_research_extension_registers_versioned_capabilities() -> None:
    registry = CapabilityExtensionRegistry()
    registry.register_extension(ResearchCapabilityExtension())
    search, _ = registry.get("web_search", "1.0.0")
    fetch, _ = registry.get("web_fetch", "1.0.0")
    assert search.permissions == ("network.search",)
    assert fetch.permissions == ("network.http.read",)
    assert search.ref.extension_id == "capability-research"


@pytest.mark.asyncio
async def test_research_capability_is_permission_gated() -> None:
    registry = CapabilityExtensionRegistry()
    registry.register_extension(ResearchCapabilityExtension())
    result = await registry.invoke(
        "web_fetch",
        {"url": "https://example.com"},
        context=CapabilityContext("user", "session", "run", metadata={"permissions": []}),
    )
    assert result.success is False
    assert result.error["code"] == "PERMISSION_DENIED"
