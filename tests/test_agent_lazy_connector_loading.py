from types import SimpleNamespace

import pytest

from joyhousebot.bootstrap.agent_runtime_catalog import AgentRuntimeCatalog
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.storage.contracts import RuntimeStores


@pytest.mark.asyncio
async def test_lazy_resolved_agent_connects_tools_before_graph_execution() -> None:
    class Store:
        @staticmethod
        def get_run_execution_snapshot(_run_id: str):
            return None

    class Agent:
        connected = 0

        async def connect_capability_connectors(self) -> None:
            self.connected += 1

    agent = Agent()
    runtime = SimpleNamespace(
        stores=RuntimeStores.from_backend(Store()),
        default_agent_id="default",
        agent_resolver=lambda key: agent if key == "specialist" else None,
        agent=None,
    )
    resolved = await NativeAgentRuntime._resolve_execution_agent(
        runtime, "run-1", "specialist"
    )
    assert resolved is agent
    assert agent.connected == 1


@pytest.mark.asyncio
async def test_runtime_catalog_starts_agents_through_public_connector_lifecycle() -> None:
    class Agent:
        connected = 0

        async def connect_capability_connectors(self) -> None:
            self.connected += 1

    agent = Agent()
    catalog = AgentRuntimeCatalog(config=SimpleNamespace(), store=SimpleNamespace())
    catalog._agents["default"] = agent

    await catalog.start()

    assert agent.connected == 1
