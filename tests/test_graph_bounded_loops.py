"""Durable bounded-loop Graph nodes and recovery semantics."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore

_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "integer"},
        "done": {"type": "boolean"},
    },
    "required": ["value", "done"],
    "additionalProperties": False,
}


def _loop_tasks(*, max_iterations: int = 5) -> list[GraphTaskSpec]:
    return [
        GraphTaskSpec(id="source", prompt="SOURCE", output_schema=_STATE_SCHEMA),
        GraphTaskSpec(
            id="loop",
            prompt="",
            node_type="bounded_loop",
            dependencies=["source"],
            bounded_loop={
                "source": "tasks.source",
                "path": "structured_output",
                "state_path": "structured_output",
                "max_iterations": max_iterations,
                "exit": {
                    "path": "structured_output.done",
                    "when": {"op": "eq", "value": True},
                },
                "template": {
                    "node_type": "agent",
                    "prompt": "STEP:${state.value}:${iteration.number}",
                    "output_schema": _STATE_SCHEMA,
                    "max_attempts": 1,
                },
            },
        ),
        GraphTaskSpec(id="after", prompt="AFTER", dependencies=["loop"]),
    ]


def test_bounded_loop_configuration_rejects_unbounded_or_unverified_execution() -> None:
    assert [task.id for task in validate_and_order_graph(_loop_tasks())] == [
        "source",
        "loop",
        "after",
    ]
    oversized = _loop_tasks(max_iterations=33)
    with pytest.raises(ValueError, match="max_iterations"):
        validate_and_order_graph(oversized)
    unsafe = _loop_tasks()
    unsafe[1].bounded_loop["exit"]["when"]["op"] = "python_eval"
    with pytest.raises(ValueError, match="unsafe operator"):
        validate_and_order_graph(unsafe)
    injected = _loop_tasks()
    injected[1].bounded_loop["exit"]["when"]["expression"] = "state.done"
    with pytest.raises(ValueError, match="unsafe operator"):
        validate_and_order_graph(injected)
    unverified = _loop_tasks()
    unverified[0].output_schema = None
    with pytest.raises(ValueError, match="must declare output_schema"):
        validate_and_order_graph(unverified)
    no_schema = _loop_tasks()
    del no_schema[1].bounded_loop["template"]["output_schema"]
    with pytest.raises(ValueError, match="requires output_schema"):
        validate_and_order_graph(no_schema)
    retrying_parent = _loop_tasks()
    retrying_parent[1].max_attempts = 2
    with pytest.raises(ValueError, match="max_attempts must be 1"):
        validate_and_order_graph(retrying_parent)


def test_graph_api_freezes_bounded_loop_definition(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-bounded-loop-api.db")
    store.create_operator_access_token(user_id="loop-owner", actor_id="test", token="loop-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    tasks = _loop_tasks(max_iterations=7)
    with client:
        created = client.post(
            "/control/v1/runs/graphs",
            headers={"Authorization": "Bearer loop-token"},
            json={
                "goal": "freeze bounded loop",
                "session_id": "loop-api",
                "tasks": [
                    {
                        "id": task.id,
                        "prompt": task.prompt,
                        "node_type": task.node_type,
                        "dependencies": task.dependencies,
                        "output_schema": task.output_schema,
                        "bounded_loop": task.bounded_loop,
                    }
                    for task in tasks
                ],
            },
        )
        assert created.status_code == 202, created.json()
        revisions = client.get(
            f"/control/v1/runs/{created.json()['run_id']}/graph-revisions",
            headers={"Authorization": "Bearer loop-token"},
        )
    assert revisions.status_code == 200
    nodes = revisions.json()["items"][0]["nodes"]
    assert [node["node_type"] for node in nodes] == ["agent", "bounded_loop", "agent"]
    frozen = nodes[1]["definition"]["bounded_loop"]
    assert frozen["max_iterations"] == 7
    assert frozen["template"]["output_schema"] == _STATE_SCHEMA


def test_bounded_loop_advance_is_lease_fenced_under_worker_race(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-bounded-loop-fence.db")
    run_id = "bounded-loop-fence"
    parent_id = f"{run_id}:loop"
    store.create_runtime_graph(
        run_id=run_id,
        user_id="loop-owner",
        session_id="loop-fence",
        agent_id="default",
        prompt="fence loop",
        options={"aggregate": False},
        tasks=[
            {
                "task_id": parent_id,
                "agent_id": "default",
                "name": "loop",
                "payload": {
                    "spec_id": "loop",
                    "node_type": "bounded_loop",
                    "bounded_loop": {"max_iterations": 2},
                },
                "dependencies": [],
                "priority": 0,
                "max_attempts": 1,
            }
        ],
    )
    claimed = store.claim_runtime_task(worker_id="loop-worker", run_id=run_id)
    assert claimed is not None
    child = {
        "task_id": f"{parent_id}:loop:001:state",
        "agent_id": "default",
        "name": "iteration 1",
        "payload": {
            "spec_id": "loop[1]",
            "node_type": "agent",
            "bounded_loop_parent_task_id": parent_id,
            "bounded_loop_iteration": 1,
            "bounded_loop_id": "loop-id",
            "bounded_loop_input_state_hash": "state-hash",
        },
        "priority": 1,
        "max_attempts": 1,
    }

    def advance() -> dict[str, Any]:
        return store.advance_runtime_bounded_loop(
            run_id=run_id,
            task_id=parent_id,
            loop_id="loop-id",
            iteration=1,
            input_state_hash="state-hash",
            previous_child_id=None,
            child=child,
            worker_id="loop-worker",
            lease_version=claimed.lease_version,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: advance(), range(2)))
    assert sorted(outcome["saved"] for outcome in outcomes) == [False, True]
    tasks = store.list_runtime_tasks(run_id=run_id, limit=100)
    assert len(tasks) == 2
    assert len([task for task in tasks if task.parent_task_id == parent_id]) == 1
    assert store.get_runtime_run(run_id).total_task_count == 2


class _LoopAgent:
    def __init__(self, *, done_at: int) -> None:
        self.done_at = done_at
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        self.calls.append(content)
        line = content.split("\n", 1)[0]
        if line == "SOURCE":
            return '{"value":0,"done":false}'
        if line.startswith("STEP:"):
            value = int(line.split(":")[1]) + 1
            done = "true" if value >= self.done_at else "false"
            await asyncio.sleep(0.01)
            return f'{{"value":{value},"done":{done}}}'
        return "after"


class _RecoveringLoopAgent(_LoopAgent):
    def __init__(self) -> None:
        super().__init__(done_at=2)
        self.fail_second = True

    async def process_direct(self, content: str, **kwargs: Any) -> str:
        line = content.split("\n", 1)[0]
        if line.startswith("STEP:1:") and self.fail_second:
            self.calls.append(content)
            raise RuntimeError("transient second iteration failure")
        return await super().process_direct(content, **kwargs)


@pytest.mark.asyncio
async def test_bounded_loop_persists_each_iteration_and_exits_deterministically(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-bounded-loop.db")
    agent = _LoopAgent(done_at=3)
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="durable bounded loop",
                user_id="loop-owner",
                session_id="loop-success",
                tasks=_loop_tasks(),
                aggregate=False,
                fail_fast=True,
                max_concurrent=4,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=8)

        assert completed.status == "completed", completed.error
        tasks = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        by_spec = {str(task.payload.get("spec_id")): task for task in tasks}
        parent = by_spec["loop"]
        children = sorted(
            [task for task in tasks if task.parent_task_id == parent.task_id],
            key=lambda task: task.payload["bounded_loop_iteration"],
        )
        assert len(children) == 3
        assert all(child.status == "completed" for child in children)
        assert [child.payload["bounded_loop_iteration"] for child in children] == [1, 2, 3]
        assert len({child.task_id for child in children}) == 3
        assert parent.result["stop_reason"] == "bounded_loop_completed"
        assert parent.result["structured_output"] == {
            "state": {"value": 3, "done": True},
            "iterations": 3,
            "exited": True,
        }
        assert by_spec["after"].status == "completed"
        assert completed.total_task_count == 6
        events = store.list_runtime_events(submitted.run_id, limit=1000)
        types = [event.type for event in events]
        assert types.count("loop.iteration_started") == 3
        assert types.count("loop.iteration_completed") == 3
        assert "loop.exhausted" not in types
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_bounded_loop_exhaustion_fails_once_without_running_dependents(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-bounded-loop-exhausted.db")
    agent = _LoopAgent(done_at=99)
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="bounded exhaustion",
                user_id="loop-owner",
                session_id="loop-exhausted",
                tasks=_loop_tasks(max_iterations=2),
                aggregate=False,
                fail_fast=True,
            )
        )
        failed = await runtime.wait(submitted.run_id, timeout=8)

        assert failed.status == "failed"
        tasks = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        parent = next(task for task in tasks if task.payload.get("spec_id") == "loop")
        children = [task for task in tasks if task.parent_task_id == parent.task_id]
        after = next(task for task in tasks if task.payload.get("spec_id") == "after")
        assert len(children) == 2
        assert parent.status == "failed"
        assert parent.result["stop_reason"] == "bounded_loop_exhausted"
        assert parent.result["structured_output"]["state"] == {"value": 2, "done": False}
        assert after.status in {"cancelled", "skipped"}
        events = store.list_runtime_events(submitted.run_id, limit=1000)
        assert [event.type for event in events].count("loop.exhausted") == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_bounded_loop_resume_reuses_committed_iterations_without_duplicates(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-bounded-loop-resume.db")
    agent = _RecoveringLoopAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="resume a durable bounded loop",
                user_id="loop-owner",
                session_id="loop-resume",
                tasks=_loop_tasks(max_iterations=3),
                aggregate=False,
                fail_fast=True,
            )
        )
        failed = await runtime.wait(submitted.run_id, timeout=8)
        assert failed.status == "failed"
        before = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        parent = next(task for task in before if task.payload.get("spec_id") == "loop")
        before_children = [task for task in before if task.parent_task_id == parent.task_id]
        assert len(before_children) == 2
        assert parent.result["stop_reason"] == "bounded_loop_iteration_failed"

        agent.fail_second = False
        await runtime.resume(submitted.run_id)
        completed = await runtime.wait(submitted.run_id, timeout=8)
        assert completed.status == "completed", completed.error
        after = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        resumed_parent = next(task for task in after if task.payload.get("spec_id") == "loop")
        children = sorted(
            [task for task in after if task.parent_task_id == resumed_parent.task_id],
            key=lambda task: task.payload["bounded_loop_iteration"],
        )
        assert len(children) == 2
        assert [child.task_id for child in children] == [child.task_id for child in before_children]
        assert all(child.status == "completed" for child in children)
        assert resumed_parent.result["stop_reason"] == "bounded_loop_completed"
        assert resumed_parent.result["structured_output"]["state"] == {
            "value": 2,
            "done": True,
        }
        events = store.list_runtime_events(submitted.run_id, limit=1000)
        assert [event.type for event in events].count("loop.iteration_started") == 2
        assert [event.type for event in events].count("loop.iteration_completed") == 2
    finally:
        await runtime.close()
