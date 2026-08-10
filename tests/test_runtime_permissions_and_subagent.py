from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from joyhousebot.agent.subagent import SubagentManager
from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.capabilities.dispatcher import capability_result_prompt
from joyhousebot.contracts.tools import Tool
from joyhousebot.providers.base import LLMProvider, LLMResponse
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.capabilities import register_tool_fixture


class EchoTool(Tool):
    name = "exec"
    description = "test"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        return "executed"


@pytest.mark.asyncio
async def test_runtime_permission_policy_blocks_high_risk_and_allowlists() -> None:
    registry = CapabilityRegistry()
    tool = EchoTool()
    register_tool_fixture(registry, tool)

    denied_result = await registry.invoke_tool(
        "exec",
        {},
        context=ToolExecutionContext(
            run_id="r",
            session_key="s",
            channel="api",
            chat_id="c",
            permission_mode="dontask",
        ),
    )
    not_listed_result = await registry.invoke_tool(
        "exec",
        {},
        context=ToolExecutionContext(
            run_id="r",
            session_key="s",
            channel="api",
            chat_id="c",
            allowed_tools=frozenset({"read_file"}),
        ),
    )

    denied = capability_result_prompt(denied_result)
    not_listed = capability_result_prompt(not_listed_result)
    assert "non-interactive mode" in denied
    assert "not in the run allowlist" in not_listed
    hidden = registry.get_tool_definitions(
        ToolExecutionContext(
            run_id="r",
            session_key="s",
            channel="api",
            chat_id="c",
            disallowed_tools=frozenset({"exec"}),
        )
    )
    assert hidden == []


class DummyProvider(LLMProvider):
    def get_default_model(self) -> str:
        return "fake"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        return LLMResponse(content="unused")


class FakeRuntime:
    def __init__(self) -> None:
        self.options = None

    async def submit_run(self, options, **_kwargs):
        self.options = options
        return SimpleNamespace(run_id="native-subagent-run")

    async def wait(self, _run_id):
        return SimpleNamespace(
            status="completed",
            result={"content": "native result"},
            error=None,
        )

    async def cancel(self, _run_id, _reason):
        return True


@pytest.mark.asyncio
async def test_subagent_uses_native_runtime_when_attached(tmp_path: Path) -> None:
    del tmp_path
    manager = SubagentManager(model="fake")
    runtime = FakeRuntime()
    manager.set_runtime(runtime)

    result = await manager.spawn("research this", "research")

    assert runtime.options.metadata["source"] == "spawn"
    assert set(runtime.options.disallowed_tools) == {"message", "spawn"}
    assert result.operation["run_id"] == "native-subagent-run"


class FakeRuntimeWithEvents(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.events = SimpleNamespace(publish=self._publish)
        self.submitted = 0

    async def _publish(self, _event):
        return None

    async def submit_run(self, options, **kwargs):
        self.submitted += 1
        return await super().submit_run(options, **kwargs)


@pytest.mark.asyncio
async def test_subagent_passes_distributed_fanout_limit() -> None:
    from joyhousebot.runtime.context import RunContext, bind_run_context

    manager = SubagentManager(model="fake", max_spawns_per_run=2)
    runtime = FakeRuntimeWithEvents()
    manager.set_runtime(runtime)

    context = RunContext(
        run_id="run-1",
        root_run_id="root-1",
        session_key="s",
        channel="api",
        chat_id="c",
    )
    with bind_run_context(context):
        first = await manager.spawn("task one")

    assert first.operation["run_id"] == "native-subagent-run"
    assert runtime.options.max_children_per_root == 2
    assert runtime.submitted == 1


def test_child_fanout_limit_is_atomic_in_store(tmp_path: Path) -> None:
    from tests.support.postgres_store import PostgresTestStore

    store = PostgresTestStore(tmp_path / "fanout.db")
    common = {
        "user_id": "user-a",
        "agent_id": "agent-a",
        "kind": "agent",
        "prompt": "work",
        "options": {},
    }
    store.create_runtime_run(run_id="root", session_id="root", **common)
    for index in range(2):
        store.create_runtime_run(
            run_id=f"child-{index}",
            session_id=f"child-{index}",
            root_run_id="root",
            parent_run_id="root",
            max_children_per_root=2,
            **common,
        )
    with pytest.raises(RuntimeError, match="fan-out limit"):
        store.create_runtime_run(
            run_id="child-3",
            session_id="child-3",
            root_run_id="root",
            parent_run_id="root",
            max_children_per_root=2,
            **common,
        )
