"""Immutable Graph revisions and safe deterministic branch execution."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.orchestration.branching import evaluate_branch
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.graph_revision import freeze_graph_revision, graph_task_rows
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore

_ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"route": {"type": "string", "enum": ["publish", "review"]}},
    "required": ["route"],
    "additionalProperties": False,
}


def _branch_tasks(route: str = "publish") -> list[GraphTaskSpec]:
    return [
        GraphTaskSpec(
            id="classify",
            prompt=f"CLASSIFY:{route}",
            output_schema=_ROUTE_SCHEMA,
        ),
        GraphTaskSpec(
            id="route",
            prompt="",
            node_type="branch",
            dependencies=["classify"],
            branch={
                "source": "tasks.classify",
                "path": "structured_output.route",
                "cases": [
                    {
                        "when": {"op": "eq", "value": "publish"},
                        "targets": ["publish"],
                    }
                ],
                "default_targets": ["review"],
            },
        ),
        GraphTaskSpec(id="publish", prompt="PUBLISH", dependencies=["route"]),
        GraphTaskSpec(id="review", prompt="REVIEW", dependencies=["route"]),
    ]


def test_branch_validation_rejects_unverified_or_undeclared_routing() -> None:
    tasks = _branch_tasks()
    assert [item.id for item in validate_and_order_graph(tasks)] == [
        "classify",
        "route",
        "publish",
        "review",
    ]
    decision = evaluate_branch(
        tasks[1].branch,
        {"classify": {"structured_output": {"route": "publish"}}},
    )
    assert decision.selected_targets == ("publish",)

    unsafe = _branch_tasks()
    unsafe[1].branch["cases"][0]["when"]["op"] = "python_eval"
    with pytest.raises(ValueError, match="unsafe operator"):
        validate_and_order_graph(unsafe)

    unverified = _branch_tasks()
    unverified[0].output_schema = None
    with pytest.raises(ValueError, match="must declare output_schema"):
        validate_and_order_graph(unverified)

    undeclared = _branch_tasks()
    undeclared.append(GraphTaskSpec(id="hidden", prompt="HIDDEN", dependencies=["route"]))
    with pytest.raises(ValueError, match="undeclared outgoing targets"):
        validate_and_order_graph(undeclared)


def test_graph_budgets_are_frozen_into_revision_and_task_payloads() -> None:
    task = GraphTaskSpec(
        id="budgeted",
        prompt="bounded execution",
        max_input_tokens=1000,
        max_output_tokens=250,
        max_cost_usd=0.05,
    )
    spec = TaskGraphSpec(
        goal="bounded graph",
        tasks=[task],
        max_input_tokens=2000,
        max_output_tokens=500,
        max_cost_usd=0.10,
    )
    revision = freeze_graph_revision(
        "budgeted-graph", spec, [task], source="contract-test"
    )
    assert revision["settings"]["max_cost_usd"] == 0.10
    assert revision["nodes"][0]["max_output_tokens"] == 250
    payload = graph_task_rows("budgeted-graph", revision)[0]["payload"]
    assert payload["max_input_tokens"] == 1000
    assert payload["max_cost_usd"] == 0.05


class _BranchAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        self.calls.append(content)
        if content.startswith("CLASSIFY:"):
            return '{"route":"' + content.split(":", 1)[1] + '"}'
        return f"done:{content}"


@pytest.mark.asyncio
async def test_branch_executes_selected_path_and_freezes_revision(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-branch.db")
    agent = _BranchAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="route a verified result",
                user_id="graph-owner",
                session_id="branch-session",
                tasks=_branch_tasks("publish"),
                aggregate=False,
                max_concurrent=2,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=5)

        assert completed.status == "completed", completed.error
        assert any(call.startswith("PUBLISH") for call in agent.calls)
        assert not any(call.startswith("REVIEW") for call in agent.calls)
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id)
        }
        assert tasks["classify"].result["structured_output"] == {"route": "publish"}
        assert tasks["route"].result["selected_targets"] == ["publish"]
        assert tasks["publish"].status == "completed"
        assert tasks["review"].status == "skipped"
        assert tasks["review"].result["stop_reason"] == "branch_not_selected"
        events = store.list_runtime_events(submitted.run_id)
        assert "branch.evaluated" in [event.type for event in events]
        assert any(
            event.type == "task.skipped" and event.task_id == tasks["review"].task_id
            for event in events
        )

        run = store.get_runtime_run(submitted.run_id)
        assert run is not None and run.graph_revision_id
        assert run.options["graph_revision_id"] == run.graph_revision_id
        revisions = store.list_graph_revisions(submitted.run_id, expected_user_id="graph-owner")
        assert len(revisions) == 1
        revision = revisions[0]
        assert revision.revision_id == run.graph_revision_id
        assert len(revision.spec_hash) == 64
        assert [node.node_type for node in revision.nodes] == [
            "agent",
            "branch",
            "agent",
            "agent",
        ]
        assert {edge.edge_id for edge in revision.edges} == {
            "classify->route",
            "route->publish",
            "route->review",
        }
        with store._pool.connection() as connection:
            with pytest.raises(Exception, match="Graph revisions are immutable"):
                with connection.transaction():
                    connection.execute(
                        "UPDATE graph_revisions SET settings='{}'::jsonb WHERE revision_id=%s",
                        (revision.revision_id,),
                    )
    finally:
        await asyncio.gather(first.close(), second.close())


def test_branch_completion_is_lease_fenced_and_atomic(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-branch-fence.db")
    run_id = "branch-fence-run"
    rows = [
        {
            "task_id": f"{run_id}:route",
            "agent_id": "default",
            "name": "route",
            "payload": {"spec_id": "route", "node_type": "branch"},
            "dependencies": [],
            "priority": 0,
            "max_attempts": 1,
        },
        {
            "task_id": f"{run_id}:yes",
            "agent_id": "default",
            "name": "yes",
            "payload": {"spec_id": "yes"},
            "dependencies": [f"{run_id}:route"],
            "priority": 1,
            "max_attempts": 1,
        },
        {
            "task_id": f"{run_id}:no",
            "agent_id": "default",
            "name": "no",
            "payload": {"spec_id": "no"},
            "dependencies": [f"{run_id}:route"],
            "priority": 2,
            "max_attempts": 1,
        },
    ]
    store.create_runtime_graph(
        run_id=run_id,
        user_id="graph-owner",
        session_id="fence",
        agent_id="default",
        prompt="fence",
        options={"aggregate": False},
        tasks=rows,
    )
    claimed = store.claim_runtime_task(worker_id="branch-worker", run_id=run_id)
    assert claimed is not None and claimed.task_id == f"{run_id}:route"

    with pytest.raises(RuntimeError, match="frozen branch target Task set changed"):
        store.complete_runtime_branch(
            run_id=run_id,
            task_id=claimed.task_id,
            selected_target_ids=[f"{run_id}:yes"],
            all_target_ids=[f"{run_id}:yes"],
            result={"selected_targets": ["yes"]},
            worker_id="branch-worker",
            lease_version=claimed.lease_version,
        )

    def complete() -> tuple[bool, list[str]]:
        return store.complete_runtime_branch(
            run_id=run_id,
            task_id=claimed.task_id,
            selected_target_ids=[f"{run_id}:yes"],
            all_target_ids=[f"{run_id}:yes", f"{run_id}:no"],
            result={"selected_targets": ["yes"]},
            worker_id="branch-worker",
            lease_version=claimed.lease_version,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: complete(), range(2)))
    assert sorted(saved for saved, _skipped in outcomes) == [False, True]
    tasks = {task.task_id: task for task in store.list_runtime_tasks(run_id=run_id)}
    assert tasks[f"{run_id}:route"].status == "completed"
    assert tasks[f"{run_id}:no"].status == "skipped"
    assert tasks[f"{run_id}:yes"].status == "blocked"


def test_graph_revision_hash_is_verified_before_atomic_insert(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-revision-hash.db")
    run_id = "tampered-graph-revision"
    spec = TaskGraphSpec(
        goal="original",
        user_id="graph-owner",
        session_id="revision-hash",
        tasks=[GraphTaskSpec(id="only", prompt="ONLY")],
        aggregate=False,
    )
    revision = freeze_graph_revision(
        run_id,
        spec,
        validate_and_order_graph(spec.tasks),
        source="test",
    )
    revision["settings"]["goal"] = "tampered"

    with pytest.raises(RuntimeError, match="spec hash mismatch"):
        store.create_runtime_graph(
            run_id=run_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            agent_id=spec.agent_id,
            prompt=spec.goal,
            options={"aggregate": False},
            tasks=graph_task_rows(run_id, revision),
            revision=revision,
        )
    assert store.get_runtime_run(run_id) is None


@pytest.mark.asyncio
async def test_graph_revision_api_is_owner_scoped(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-revision-api.db")
    store.create_api_access_token(user_id="graph-owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other-owner", actor_id="test", token="other-token")
    runtime = NativeAgentRuntime(agent=_BranchAgent(), store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="revision API",
            user_id="graph-owner",
            session_id="revision-api",
            tasks=[GraphTaskSpec(id="only", prompt="ONLY")],
            aggregate=False,
        )
    )
    await runtime.wait(submitted.run_id, timeout=3)
    await runtime.close()
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        own = client.get(
            f"/v1/runs/{submitted.run_id}/graph-revisions",
            headers={"Authorization": "Bearer owner-token"},
        )
        foreign = client.get(
            f"/v1/runs/{submitted.run_id}/graph-revisions",
            headers={"Authorization": "Bearer other-token"},
        )
        invalid_branch = client.post(
            "/v1/runs/graphs",
            headers={"Authorization": "Bearer owner-token"},
            json={
                "goal": "unsafe branch",
                "session_id": "invalid-branch",
                "tasks": [
                    {"id": "source", "prompt": "source"},
                    {
                        "id": "route",
                        "node_type": "branch",
                        "dependencies": ["source"],
                        "branch": {
                            "source": "tasks.source",
                            "path": "structured_output.route",
                            "cases": [
                                {
                                    "when": {"op": "eq", "value": "yes"},
                                    "targets": ["yes"],
                                }
                            ],
                            "default_targets": ["no"],
                        },
                    },
                    {"id": "yes", "prompt": "yes", "dependencies": ["route"]},
                    {"id": "no", "prompt": "no", "dependencies": ["route"]},
                ],
            },
        )

    assert own.status_code == 200
    assert own.json()["items"][0]["nodes"][0]["definition"]["prompt"] == "ONLY"
    assert foreign.status_code == 404
    assert invalid_branch.status_code == 422
    assert "must declare output_schema" in invalid_branch.json()["error"]["message"]
