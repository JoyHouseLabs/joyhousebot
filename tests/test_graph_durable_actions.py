"""Graph Tasks share the durable Action, reconciliation, and verification planes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from porthouse.agent.executor import NativeAgentExecutor
from porthouse.capabilities import CapabilityRegistry
from porthouse.capabilities.tool_adapter import ToolOutput
from porthouse.contracts import OperationReconciliationResult
from porthouse.contracts.tools import Tool
from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    InvocationStatus,
)
from porthouse.providers.base import LLMProvider, LLMResponse
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec
from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.session.runtime_manager import RuntimeSessionManager
from tests.support.capabilities import register_tool_fixture
from tests.support.postgres_store import PostgresTestStore


class _CapabilityAgent:
    def __init__(
        self,
        store: PostgresTestStore,
        tool: Tool,
        *,
        definition: CapabilityDefinition | None = None,
    ) -> None:
        self.capabilities = CapabilityRegistry(store=store)
        register_tool_fixture(self.capabilities, tool, definition=definition)

    async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("direct Graph Capability Task must not call a model")


class _ApprovalWriteTool(Tool):
    name = "graph_approval_write"
    description = "Persist one approved value"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, value: str, **_kwargs: Any) -> str:
        self.calls += 1
        return f"saved:{value}"


class _AsyncGraphTool(Tool):
    name = "graph_async_operation"
    description = "Start and reconcile one asynchronous operation"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    side_effect = "internal"
    idempotent = True
    retryable = True

    def __init__(self) -> None:
        self.execute_calls = 0
        self.reconcile_calls = 0

    async def execute(self, value: str, **kwargs: Any) -> ToolOutput:
        self.execute_calls += 1
        return ToolOutput(
            content="accepted",
            summary="operation accepted",
            data={"submitted": value},
            operation={
                "provider_operation_id": "graph-provider-42",
                "idempotency_key": kwargs["tool_context"].idempotency_key,
            },
            status=InvocationStatus.ACCEPTED,
        )

    async def reconcile_operation(
        self, operation: dict[str, Any], **_kwargs: Any
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        return OperationReconciliationResult(
            status="succeeded",
            summary="provider confirmed completion",
            output={"value": "done"},
            operation={**operation, "status": "completed"},
        )


class _RepairProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/graph-verification"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        content = '{"value":"wrong"}' if self.calls == 1 else '{"value":42}'
        return LLMResponse(content=content, finish_reason="stop")


class _FrozenWorkReadTool(Tool):
    name = "graph_frozen_work_read"
    description = "Read a frozen Work version"
    parameters = {
        "type": "object",
        "properties": {"work_ref": {"type": "string"}},
        "required": ["work_ref"],
    }

    async def execute(self, work_ref: str, **_kwargs: Any) -> ToolOutput:
        return ToolOutput(
            content="frozen Work loaded",
            data={
                "output": {
                    "work_ref": work_ref,
                    "content": "# Versioned publication body",
                }
            },
        )


class _CapturePublicationTool(Tool):
    name = "graph_capture_publication"
    description = "Capture a rendered publication body"
    parameters = {
        "type": "object",
        "properties": {"body": {"type": "string"}},
        "required": ["body"],
    }

    def __init__(self) -> None:
        self.bodies: list[str] = []

    async def execute(self, body: str, **_kwargs: Any) -> str:
        self.bodies.append(body)
        return "published"


class _ChainedCapabilityAgent:
    def __init__(
        self,
        store: PostgresTestStore,
        read: _FrozenWorkReadTool,
        publish: _CapturePublicationTool,
    ) -> None:
        self.capabilities = CapabilityRegistry(store=store)
        self.read_definition = register_tool_fixture(self.capabilities, read)
        self.publish_definition = register_tool_fixture(self.capabilities, publish)

    async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("direct Graph Capability Task must not call a model")


def _approval_definition() -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(
            _ApprovalWriteTool.name,
            "1.0.0",
            CapabilityKind.TOOL,
            "test.graph-actions",
            "1.0.0",
            "sha256:graph-approval",
        ),
        name="Graph approval write",
        description=_ApprovalWriteTool.description,
        input_schema=_ApprovalWriteTool.parameters,
        output_schema={"type": "object"},
        adapter="test.graph_approval_write",
        side_effect="write",
    )


async def _wait_for_status(
    store: PostgresTestStore,
    run_id: str,
    statuses: set[str],
    *,
    timeout: float = 5,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = store.get_runtime_run(run_id)
        if record is not None and record.status in statuses:
            return record
        await asyncio.sleep(0.02)
    return store.get_runtime_run(run_id)


def test_graph_task_lease_takeover_recovers_same_durable_attempt(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-task-takeover.db")
    store.create_runtime_run(
        run_id="graph-task-takeover",
        user_id="user-a",
        session_id="graph-task-takeover",
        agent_id="default",
        kind="graph",
        prompt="recover",
        options={"max_concurrent": 1},
    )
    store.create_runtime_task(
        task_id="graph-task-takeover:one",
        run_id="graph-task-takeover",
        name="one",
        payload={"spec_id": "one"},
        max_attempts=1,
    )
    first = store.claim_runtime_task(worker_id="worker-one", lease_seconds=30)
    assert first is not None
    store.create_runtime_turn(
        turn_id="turn-graph-task-takeover",
        run_id=first.run_id,
        task_id=first.task_id,
        turn_index=first.attempt,
        model="test",
        request_hash="frozen-request",
        worker_id="worker-one",
    )
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE runtime_tasks
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE task_id=%s""",
            (first.task_id,),
        )
    store._lease_sweep_at = 0.0

    recovered = store.claim_runtime_task(worker_id="worker-two", lease_seconds=30)
    assert recovered is not None
    assert recovered.task_id == first.task_id
    assert recovered.attempt == first.attempt
    assert recovered.lease_version == first.lease_version + 1


@pytest.mark.asyncio
async def test_graph_capability_can_render_a_nested_dependency_result(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-nested-capability-output.db")
    read = _FrozenWorkReadTool()
    publish = _CapturePublicationTool()
    agent = _ChainedCapabilityAgent(store, read, publish)
    runtime = NativeAgentRuntime(agent=agent, store=store)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="read then publish without copying the body into Product",
                user_id="user-a",
                session_id="graph-nested-output",
                aggregate=False,
                tasks=[
                    GraphTaskSpec(
                        id="read-work",
                        prompt="read",
                        capability=agent.read_definition.ref,
                        capability_input={"work_ref": "work-001"},
                    ),
                    GraphTaskSpec(
                        id="publish",
                        prompt="publish",
                        dependencies=["read-work"],
                        capability=agent.publish_definition.ref,
                        capability_input={
                            "body": (
                                "${tasks.read-work.capability_result.data."
                                "output.content}"
                            )
                        },
                    ),
                ],
            )
        )
        completed = await runtime.wait(submitted.run_id, timeout=5)
    finally:
        await runtime.close()

    assert completed.status == "completed", completed.error
    assert publish.bodies == ["# Versioned publication body"]


@pytest.mark.asyncio
async def test_graph_approval_resumes_same_action_without_reexecution(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-approval.db")
    tool = _ApprovalWriteTool()
    definition = _approval_definition()
    agent = _CapabilityAgent(store, tool, definition=definition)
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="write once",
            user_id="user-a",
            session_id="graph-approval",
            aggregate=False,
            tasks=[
                GraphTaskSpec(
                    id="write",
                    prompt="write",
                    capability=definition.ref,
                    capability_input={"value": "one"},
                )
            ],
        )
    )

    waiting = await _wait_for_status(
        store, submitted.run_id, {"waiting_approval"}
    )
    assert waiting.status == "waiting_approval"
    task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert task.status == "waiting_approval"
    assert task.attempt == 1
    assert tool.calls == 0
    approval = store.list_run_approval_requests(
        submitted.run_id, expected_user_id="user-a"
    )[0]
    second = NativeAgentRuntime(agent=agent, store=store)
    await second.start()
    assert store.resolve_approval_request(
        approval_id=approval.approval_id,
        run_id=submitted.run_id,
        user_id="user-a",
        resolution="approve",
        note=None,
        actor_id="user-a",
    )
    store.notify_work(submitted.run_id)

    completed = await _wait_for_status(store, submitted.run_id, {"completed"})
    await asyncio.gather(runtime.close(), second.close())
    assert completed.status == "completed"
    task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert task.status == "completed", task.error
    assert task.attempt == 1
    assert tool.calls == 1
    actions = store.list_action_intents(submitted.run_id)
    assert len(actions) == 1
    assert actions[0].task_id == task.task_id
    assert actions[0].status == "observed"
    assert store.get_approval_request(approval.approval_id).status == "consumed"


@pytest.mark.asyncio
async def test_graph_external_operation_reconciles_without_resubmission(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-reconciliation.db")
    tool = _AsyncGraphTool()
    agent = _CapabilityAgent(store, tool)
    ref = agent.capabilities._adapters[tool.name].definition.ref
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="run asynchronous operation",
            user_id="user-a",
            session_id="graph-reconciliation",
            aggregate=False,
            tasks=[
                GraphTaskSpec(
                    id="operation",
                    prompt="operate",
                    capability=ref,
                    capability_input={"value": "one"},
                )
            ],
        )
    )

    completed = await _wait_for_status(store, submitted.run_id, {"completed"})
    await runtime.close()
    assert completed.status == "completed"
    task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert task.status == "completed"
    assert task.attempt == 1
    assert tool.execute_calls == 1
    assert tool.reconcile_calls == 1
    reconciliation = store.list_run_operation_reconciliations(
        submitted.run_id, expected_user_id="user-a"
    )[0]
    assert reconciliation.status == "succeeded"
    assert store.list_action_intents(submitted.run_id)[0].status == "observed"


@pytest.mark.asyncio
async def test_graph_agent_verification_is_task_fenced_and_repairs_once(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-verification.db")
    provider = _RepairProvider()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/graph-verification",
        max_iterations=3,
        session_manager=RuntimeSessionManager(store),
    )
    runtime = NativeAgentRuntime(agent=executor, store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="return verified JSON",
            user_id="user-a",
            session_id="graph-verification",
            aggregate=False,
            tasks=[
                GraphTaskSpec(
                    id="verified",
                    prompt="return the value",
                    output_schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                    },
                    max_repairs=1,
                )
            ],
        )
    )

    completed = await _wait_for_status(store, submitted.run_id, {"completed"})
    await runtime.close()
    await executor.close_tool_connectors()
    assert completed.status == "completed"
    task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert task.status == "completed", task.error
    assert provider.calls == 2
    records = store.list_verification_records(
        submitted.run_id, expected_user_id="user-a"
    )
    assert [(item.attempt, item.status) for item in records] == [
        (1, "failed"),
        (2, "passed"),
    ]
    assert all(item.task_id == task.task_id for item in records)
    assert all(item.run_lease_version is None for item in records)
    assert all(item.task_lease_version == task.lease_version for item in records)
