from types import SimpleNamespace

import pytest

from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.storage.contracts import RuntimeStores


@pytest.mark.asyncio
async def test_lazy_resolved_agent_connects_tools_before_graph_execution() -> None:
    class Store:
        @staticmethod
        def get_run_execution_snapshot(_run_id: str):
            return None

    class Agent:
        connected = 0

        async def _connect_tool_connectors(self) -> None:
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
