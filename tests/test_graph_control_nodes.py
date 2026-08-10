"""Explicit Graph approval, verification, and compensation nodes."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.config.schema import Config
from joyhousebot.contracts.tools import Tool
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from joyhousebot.orchestration.control_nodes import (
    validate_compensation_declarations,
)
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore

_VALUE_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


class _ControlAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        self.calls.append(content)
        if content.startswith("SOURCE"):
            return '{"value":"ready"}'
        return f"done:{content}"


def test_control_node_configuration_fails_closed() -> None:
    source = GraphTaskSpec(id="source", prompt="SOURCE")
    approval = GraphTaskSpec(
        id="gate",
        prompt="Approve",
        node_type="approval",
        dependencies=["source"],
        approval={"required_role": "owner", "expires_in_seconds": 60},
    )
    verify = GraphTaskSpec(
        id="verify",
        prompt="",
        node_type="verify",
        dependencies=["source"],
        verify={"source": "tasks.source"},
        output_schema=_VALUE_SCHEMA,
    )
    assert [item.id for item in validate_and_order_graph([source, approval])] == [
        "source",
        "gate",
    ]
    assert [item.id for item in validate_and_order_graph([source, verify])] == [
        "source",
        "verify",
    ]

    approval.approval["required_role"] = "model"
    with pytest.raises(ValueError, match="required_role"):
        validate_and_order_graph([source, approval])
    verify.verify = {"source": "tasks.missing"}
    with pytest.raises(ValueError, match="dependency tasks"):
        validate_and_order_graph([source, verify])
    verify.verify = {"source": "tasks.source"}
    verify.output_schema = None
    with pytest.raises(ValueError, match="requires output_schema"):
        validate_and_order_graph([source, verify])


@pytest.mark.asyncio
async def test_explicit_approval_is_owner_scoped_and_completes_gate(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-explicit-approval.db")
    store.create_api_access_token(user_id="owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other", actor_id="test", token="other-token")
    agent = _ControlAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="approve verified work",
                user_id="owner",
                session_id="explicit-approval",
                aggregate=False,
                tasks=[
                    GraphTaskSpec(id="source", prompt="SOURCE"),
                    GraphTaskSpec(
                        id="gate",
                        prompt="Publish the result",
                        name="Publish approval",
                        node_type="approval",
                        dependencies=["source"],
                        approval={
                            "title": "Publish result",
                            "required_role": "owner",
                            "expires_in_seconds": 60,
                        },
                    ),
                    GraphTaskSpec(id="after", prompt="AFTER", dependencies=["gate"]),
                ],
            )
        )
        waiting = await first.wait(submitted.run_id, timeout=4)
        assert waiting.status == "waiting_approval"
        approvals = store.list_run_approval_requests(submitted.run_id, expected_user_id="owner")
        assert len(approvals) == 1
        request = approvals[0]
        assert request.subject_type == "graph_node"
        assert request.action_id is None
        assert request.task_id == f"{submitted.run_id}:gate"
        assert request.subject["dependencies"][0]["node_id"] == "source"
        assert store.list_action_intents(submitted.run_id) == []

        client = TestClient(create_app(build_api_container(config=Config(), store=store)))
        with client:
            foreign = client.get(
                f"/v1/runs/{submitted.run_id}/approvals",
                headers={"Authorization": "Bearer other-token"},
            )
            assert foreign.status_code == 404
            resolved = client.post(
                f"/v1/runs/{submitted.run_id}/approvals/{request.approval_id}/resolve",
                headers={"Authorization": "Bearer owner-token"},
                json={"resolution": "approve", "note": "ship it"},
            )
            assert resolved.status_code == 200, resolved.json()
            assert resolved.json()["approval"]["status"] == "approved"

        completed = await first.wait(submitted.run_id, timeout=5)
        assert completed.status == "completed", completed.error
        tasks = {
            item.payload["spec_id"]: item
            for item in store.list_runtime_tasks(run_id=submitted.run_id)
        }
        assert tasks["gate"].status == "completed"
        assert tasks["gate"].result["structured_output"]["approved"] is True
        assert tasks["after"].status == "completed"
        assert sum(call.startswith("AFTER") for call in agent.calls) == 1
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_explicit_approval_expiry_has_one_database_winner(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-explicit-approval-expiry.db")
    runtime = NativeAgentRuntime(agent=_ControlAgent(), store=store)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="expire approval gate",
                user_id="owner",
                session_id="explicit-approval-expiry",
                aggregate=False,
                fail_fast=True,
                tasks=[
                    GraphTaskSpec(id="source", prompt="SOURCE"),
                    GraphTaskSpec(
                        id="gate",
                        prompt="Approve",
                        node_type="approval",
                        dependencies=["source"],
                    ),
                ],
            )
        )
        waiting = await runtime.wait(submitted.run_id, timeout=4)
        assert waiting.status == "waiting_approval"
        with store._pool.connection() as connection:
            connection.execute(
                """UPDATE approval_requests
                   SET expires_at=clock_timestamp()-interval '1 second'
                   WHERE run_id=%s AND subject_type='graph_node'""",
                (submitted.run_id,),
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(lambda _index: store.expire_due_approval_requests(), range(2))
            )
        assert sum(len(items) for items in outcomes) == 1
        failed = await runtime.wait(submitted.run_id, timeout=5)
        assert failed.status == "failed"
        approvals = store.list_run_approval_requests(submitted.run_id, expected_user_id="owner")
        assert approvals[0].status == "expired"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_explicit_verify_records_source_evidence_and_fences_workers(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-explicit-verify.db")
    agent = _ControlAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="verify immutable source",
                user_id="owner",
                session_id="explicit-verify",
                aggregate=False,
                max_concurrent=2,
                tasks=[
                    GraphTaskSpec(id="source", prompt="SOURCE"),
                    GraphTaskSpec(
                        id="verify",
                        prompt="",
                        node_type="verify",
                        dependencies=["source"],
                        verify={"source": "tasks.source"},
                        output_schema=_VALUE_SCHEMA,
                        verification_policy={
                            "verifiers": [
                                {
                                    "id": "contains-ready",
                                    "type": "deterministic",
                                    "rule": "contains",
                                    "value": "ready",
                                    "required": True,
                                    "repairable": False,
                                }
                            ]
                        },
                    ),
                    GraphTaskSpec(id="after", prompt="AFTER", dependencies=["verify"]),
                ],
            )
        )
        completed = await first.wait(submitted.run_id, timeout=5)
        assert completed.status == "completed", completed.error
        tasks = {
            item.payload["spec_id"]: item
            for item in store.list_runtime_tasks(run_id=submitted.run_id)
        }
        assert tasks["verify"].result["structured_output"] == {"value": "ready"}
        records = store.list_verification_records(submitted.run_id)
        assert len(records) == 2
        assert {item.status for item in records} == {"passed"}
        assert {item.task_id for item in records} == {tasks["verify"].task_id}
        assert len({item.task_lease_version for item in records}) == 1
    finally:
        await asyncio.gather(first.close(), second.close())


class _ApplyTool(Tool):
    name = "graph_apply_change"
    description = "Apply a reversible internal change"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, value: str, **_kwargs: Any) -> str:
        self.calls += 1
        return f"applied:{value}"


class _UndoTool(Tool):
    name = "graph_undo_change"
    description = "Compensate a reversible internal change"
    parameters = {
        "type": "object",
        "properties": {"original": {"type": "string"}},
        "required": ["original"],
    }

    def __init__(self) -> None:
        self.calls = 0
        self.originals: list[str] = []

    async def execute(self, original: str, **_kwargs: Any) -> str:
        self.calls += 1
        self.originals.append(original)
        return f"compensated:{original}"


class _CapabilityAgent:
    def __init__(
        self,
        store: PostgresTestStore,
        apply: _ApplyTool,
        undo: _UndoTool,
        apply_definition: CapabilityDefinition,
        undo_definition: CapabilityDefinition,
    ) -> None:
        self.capabilities = CapabilityRegistry(store=store)
        self.capabilities.register_tool(undo, definition=undo_definition)
        self.capabilities.register_tool(apply, definition=apply_definition)
        # Non-core registration is discovery-only. Tests that exercise an
        # executable release must make the trusted activation explicit.
        store.publish_capability(
            undo_definition, actor_id="test:trusted-graph-fixture"
        )
        store.publish_capability(
            apply_definition, actor_id="test:trusted-graph-fixture"
        )

    async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("explicit Capability Graph must not call a model")


def _capability_refs() -> tuple[CapabilityRef, CapabilityRef]:
    undo = CapabilityRef(
        _UndoTool.name,
        "1.0.0",
        CapabilityKind.TOOL,
        "test.graph-control",
        "1.0.0",
        "sha256:undo",
    )
    apply = CapabilityRef(
        _ApplyTool.name,
        "1.0.0",
        CapabilityKind.TOOL,
        "test.graph-control",
        "1.0.0",
        "sha256:apply",
    )
    return apply, undo


def _compensation_definitions() -> tuple[CapabilityDefinition, CapabilityDefinition]:
    apply_ref, undo_ref = _capability_refs()
    undo = CapabilityDefinition(
        ref=undo_ref,
        name="Undo change",
        description=_UndoTool.description,
        input_schema=_UndoTool.parameters,
        output_schema={"type": "object"},
        adapter="test.graph_undo_change",
        side_effect="internal",
    )
    apply = CapabilityDefinition(
        ref=apply_ref,
        name="Apply change",
        description=_ApplyTool.description,
        input_schema=_ApplyTool.parameters,
        output_schema={"type": "object"},
        adapter="test.graph_apply_change",
        side_effect="internal",
        compensation=undo_ref,
    )
    return apply, undo


@pytest.mark.asyncio
async def test_explicit_compensation_uses_declared_version_and_action_ledger(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-explicit-compensation.db")
    apply_tool = _ApplyTool()
    undo_tool = _UndoTool()
    apply_definition, undo_definition = _compensation_definitions()
    agent = _CapabilityAgent(store, apply_tool, undo_tool, apply_definition, undo_definition)
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    apply_ref, undo_ref = _capability_refs()
    tasks = [
        GraphTaskSpec(
            id="apply",
            prompt="",
            node_type="capability",
            capability=apply_ref,
            capability_input={"value": "one"},
        ),
        GraphTaskSpec(
            id="undo",
            prompt="",
            node_type="compensation",
            dependencies=["apply"],
            capability=undo_ref,
            capability_input={"original": "${tasks.apply.content}"},
            compensation={"source": "tasks.apply"},
        ),
    ]
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="apply and explicitly compensate",
                user_id="owner",
                session_id="explicit-compensation",
                aggregate=False,
                tasks=tasks,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=5)
        assert completed.status == "completed", completed.error
        assert apply_tool.calls == 1
        assert undo_tool.calls == 1
        assert undo_tool.originals == ["applied:one"]
        actions = store.list_action_intents(submitted.run_id)
        assert len(actions) == 2
        graph_tasks = {
            item.payload["spec_id"]: item
            for item in store.list_runtime_tasks(run_id=submitted.run_id)
        }
        result = graph_tasks["undo"].result
        assert result["node_type"] == "compensation"
        assert result["source_action_id"] in {item.action_id for item in actions}
        assert result["compensation_action_id"] in {item.action_id for item in actions}
        event_types = {item.type for item in store.list_runtime_events(submitted.run_id)}
        assert {"compensation.started", "compensation.completed"} <= event_types

        frozen = apply_definition.to_dict()
        assert frozen["compensation"] == undo_ref.to_dict()
        bad = GraphTaskSpec(
            id="bad-undo",
            prompt="",
            node_type="compensation",
            dependencies=["apply"],
            capability=apply_ref,
            compensation={"source": "tasks.apply"},
        )
        with pytest.raises(ValueError, match="does not match"):
            validate_compensation_declarations([tasks[0], bad], store.list_capability_definitions())
    finally:
        await asyncio.gather(first.close(), second.close())
