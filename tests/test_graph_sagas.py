"""Automatic, declared and lease-safe Graph Saga compensation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.contracts.tools import Tool
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from joyhousebot.orchestration.failure_policy import (
    normalize_failure_policy,
    validate_saga_declarations,
)
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


class _ApplyTool(Tool):
    name = "saga_apply"
    description = "Apply a reversible test change"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self) -> None:
        self.values: list[str] = []

    async def execute(self, value: str, **_kwargs: Any) -> str:
        self.values.append(value)
        return f"applied:{value}"


class _UndoTool(Tool):
    name = "saga_undo"
    description = "Undo a reversible test change"
    parameters = {
        "type": "object",
        "properties": {"original": {"type": "string"}},
        "required": ["original"],
    }

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.originals: list[str] = []

    async def execute(self, original: str, **_kwargs: Any) -> str:
        self.originals.append(original)
        if original == self.fail_on:
            raise RuntimeError(f"cannot compensate {original}")
        return f"undone:{original}"


class _FailTool(Tool):
    name = "saga_fail"
    description = "Fail after prior reversible actions"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        raise RuntimeError("downstream failure")


class _ReadTool(Tool):
    name = "saga_read"
    description = "Complete a read-only final step"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        return "read:ok"


def _refs() -> tuple[CapabilityRef, CapabilityRef, CapabilityRef, CapabilityRef]:
    values = []
    for capability_id, digest in (
        (_ApplyTool.name, "apply"),
        (_UndoTool.name, "undo"),
        (_FailTool.name, "fail"),
        (_ReadTool.name, "read"),
    ):
        values.append(
            CapabilityRef(
                capability_id,
                "1.0.0",
                CapabilityKind.CAPABILITY,
                "test.graph-saga",
                "1.0.0",
                f"sha256:{digest}",
            )
        )
    return tuple(values)  # type: ignore[return-value]


def _definitions() -> tuple[CapabilityDefinition, ...]:
    apply_ref, undo_ref, fail_ref, read_ref = _refs()
    return (
        CapabilityDefinition(
            ref=undo_ref,
            name="Saga undo",
            description=_UndoTool.description,
            input_schema=_UndoTool.parameters,
            output_schema={"type": "object"},
            adapter=_UndoTool.name,
            side_effect="internal",
        ),
        CapabilityDefinition(
            ref=apply_ref,
            name="Saga apply",
            description=_ApplyTool.description,
            input_schema=_ApplyTool.parameters,
            output_schema={"type": "object"},
            adapter=_ApplyTool.name,
            side_effect="internal",
            compensation=undo_ref,
        ),
        CapabilityDefinition(
            ref=fail_ref,
            name="Saga fail",
            description=_FailTool.description,
            input_schema=_FailTool.parameters,
            output_schema={"type": "object"},
            adapter=_FailTool.name,
            side_effect="read",
        ),
        CapabilityDefinition(
            ref=read_ref,
            name="Saga read",
            description=_ReadTool.description,
            input_schema=_ReadTool.parameters,
            output_schema={"type": "object"},
            adapter=_ReadTool.name,
            side_effect="read",
        ),
    )


class _SagaAgent:
    def __init__(
        self,
        store: PostgresTestStore,
        apply: _ApplyTool,
        undo: _UndoTool,
    ) -> None:
        self.capabilities = CapabilityRegistry(store=store)
        definitions = _definitions()
        self.capabilities.register_connector_capability(undo, definition=definitions[0])
        self.capabilities.register_connector_capability(apply, definition=definitions[1])
        self.capabilities.register_connector_capability(_FailTool(), definition=definitions[2])
        self.capabilities.register_connector_capability(_ReadTool(), definition=definitions[3])
        for definition in definitions:
            store.publish_capability(
                definition, actor_id="test:trusted-saga-fixture"
            )

    async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("Saga Graph must execute pinned capabilities without a model")


def _saga_tasks(*, fail: bool = True) -> list[GraphTaskSpec]:
    apply_ref, undo_ref, fail_ref, read_ref = _refs()
    return [
        GraphTaskSpec(
            id="apply-a",
            prompt="",
            node_type="capability",
            capability=apply_ref,
            capability_input={"value": "a"},
        ),
        GraphTaskSpec(
            id="apply-b",
            prompt="",
            node_type="capability",
            dependencies=["apply-a"],
            capability=apply_ref,
            capability_input={"value": "b"},
        ),
        GraphTaskSpec(
            id="finish",
            prompt="",
            node_type="capability",
            dependencies=["apply-b"],
            capability=fail_ref if fail else read_ref,
        ),
        GraphTaskSpec(
            id="undo-a",
            prompt="",
            node_type="compensation",
            dependencies=["apply-a"],
            capability=undo_ref,
            capability_input={"original": "${tasks.apply-a.content}"},
            compensation={"source": "tasks.apply-a"},
        ),
        GraphTaskSpec(
            id="undo-b",
            prompt="",
            node_type="compensation",
            dependencies=["apply-b"],
            capability=undo_ref,
            capability_input={"original": "${tasks.apply-b.content}"},
            compensation={"source": "tasks.apply-b"},
        ),
    ]


def test_saga_policy_rejects_ambiguous_or_unbounded_graphs() -> None:
    catalog = [definition.to_dict() for definition in _definitions()]
    tasks = validate_and_order_graph(_saga_tasks())
    validate_saga_declarations(
        tasks,
        catalog,
        {"mode": "saga"},
        max_concurrent=1,
    )
    with pytest.raises(ValueError, match="max_concurrent=1"):
        validate_saga_declarations(tasks, catalog, {"mode": "saga"}, max_concurrent=2)
    missing = [task for task in tasks if task.id != "undo-b"]
    with pytest.raises(ValueError, match="apply-b.*requires compensation"):
        validate_saga_declarations(missing, catalog, {"mode": "saga"}, max_concurrent=1)
    with pytest.raises(ValueError, match="unsupported fields"):
        normalize_failure_policy({"mode": "saga", "expression": "rollback()"}, fail_fast=True)


@pytest.mark.asyncio
async def test_saga_compensates_completed_side_effects_in_reverse_order(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-saga-success.db")
    apply = _ApplyTool()
    undo = _UndoTool()
    agent = _SagaAgent(store, apply, undo)
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="compensate a failed serial workflow",
                user_id="saga-owner",
                session_id="saga-success",
                tasks=_saga_tasks(),
                max_concurrent=1,
                fail_fast=True,
                failure_policy={"mode": "saga"},
                aggregate=False,
            )
        )
        failed = await first.wait(submitted.run_id, timeout=8)

        assert failed.status == "failed"
        assert failed.result["stop_reason"] == "saga_compensated"
        assert apply.values == ["a", "b"]
        assert undo.originals == ["applied:b", "applied:a"]
        saga = store.get_runtime_saga(submitted.run_id)
        assert saga is not None
        assert saga["status"] == "completed"
        assert saga["compensation_total"] == 2
        assert saga["compensation_completed"] == 2
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        }
        assert tasks["undo-b"].payload["saga_order"] == 1
        assert tasks["undo-a"].payload["saga_order"] == 2
        assert tasks["undo-a"].status == tasks["undo-b"].status == "completed"
        types = [event.type for event in store.list_runtime_events(submitted.run_id)]
        assert types.count("saga.started") == 1
        assert types.count("saga.completed") == 1
        assert "saga.failed" not in types
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_saga_compensation_failure_is_explicit_and_not_resumable(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-saga-failed.db")
    apply = _ApplyTool()
    undo = _UndoTool(fail_on="applied:b")
    runtime = NativeAgentRuntime(agent=_SagaAgent(store, apply, undo), store=store)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="surface failed compensation",
                user_id="saga-owner",
                session_id="saga-failed",
                tasks=_saga_tasks(),
                max_concurrent=1,
                fail_fast=True,
                failure_policy={"mode": "saga"},
                aggregate=False,
            )
        )
        failed = await runtime.wait(submitted.run_id, timeout=8)

        assert failed.status == "failed"
        assert failed.result["stop_reason"] == "saga_compensation_failed"
        saga = store.get_runtime_saga(submitted.run_id)
        assert saga is not None and saga["status"] == "failed"
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        }
        assert tasks["undo-b"].status == "failed"
        assert tasks["undo-a"].status == "skipped"
        assert undo.originals == ["applied:b"]
        types = [event.type for event in store.list_runtime_events(submitted.run_id)]
        assert types.count("saga.failed") == 1
        with pytest.raises(ValueError, match="cannot be resumed"):
            await runtime.resume(submitted.run_id)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_saga_declarations_stay_dormant_on_success(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-saga-happy.db")
    apply = _ApplyTool()
    undo = _UndoTool()
    runtime = NativeAgentRuntime(agent=_SagaAgent(store, apply, undo), store=store)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="do not compensate successful work",
                user_id="saga-owner",
                session_id="saga-happy",
                tasks=_saga_tasks(fail=False),
                max_concurrent=1,
                failure_policy={"mode": "saga"},
                aggregate=False,
            )
        )
        completed = await runtime.wait(submitted.run_id, timeout=8)

        assert completed.status == "completed", completed.error
        assert apply.values == ["a", "b"]
        assert undo.originals == []
        assert store.get_runtime_saga(submitted.run_id) is None
        tasks = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        declarations = [task for task in tasks if task.payload.get("saga_managed")]
        assert len(declarations) == 2
        assert {task.status for task in declarations} == {"dormant"}
    finally:
        await runtime.close()
