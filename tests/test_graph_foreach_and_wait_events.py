"""Bounded foreach expansion and token-authenticated Graph event waits."""

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
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore

_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "string"}, "maxItems": 8}},
    "required": ["items"],
    "additionalProperties": False,
}
_ITEM_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _foreach_tasks(*, max_items: int = 4, max_concurrent: int = 2) -> list[GraphTaskSpec]:
    return [
        GraphTaskSpec(id="source", prompt="SOURCE", output_schema=_ITEMS_SCHEMA),
        GraphTaskSpec(
            id="each",
            prompt="",
            node_type="foreach",
            dependencies=["source"],
            foreach={
                "source": "tasks.source",
                "path": "structured_output.items",
                "max_items": max_items,
                "max_concurrent": max_concurrent,
                "template": {
                    "node_type": "agent",
                    "prompt": "ITEM:${item.value}",
                    "output_schema": _ITEM_SCHEMA,
                    "max_attempts": 1,
                },
            },
        ),
        GraphTaskSpec(id="after", prompt="AFTER", dependencies=["each"]),
    ]


def test_foreach_and_wait_event_configuration_is_bounded() -> None:
    assert [task.id for task in validate_and_order_graph(_foreach_tasks())] == [
        "source",
        "each",
        "after",
    ]
    oversized = _foreach_tasks(max_items=65)
    with pytest.raises(ValueError, match="max_items"):
        validate_and_order_graph(oversized)
    over_parallel = _foreach_tasks(max_items=2, max_concurrent=3)
    with pytest.raises(ValueError, match="max_concurrent"):
        validate_and_order_graph(over_parallel)
    unverified = _foreach_tasks()
    unverified[0].output_schema = None
    with pytest.raises(ValueError, match="must declare output_schema"):
        validate_and_order_graph(unverified)

    wait = GraphTaskSpec(
        id="wait",
        prompt="",
        node_type="wait_event",
        wait_event={
            "event_type": "order.ready",
            "deadline_seconds": 60,
            "payload_schema": _ITEM_SCHEMA,
        },
    )
    assert validate_and_order_graph([wait]) == [wait]
    wait.wait_event["deadline_seconds"] = 0
    with pytest.raises(ValueError, match="deadline_seconds"):
        validate_and_order_graph([wait])


def test_graph_api_freezes_foreach_and_wait_event_definitions(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-v2-api.db")
    store.create_api_access_token(user_id="graph-owner", actor_id="test", token="owner-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    with client:
        created = client.post(
            "/v1/runs/graphs",
            headers={"Authorization": "Bearer owner-token"},
            json={
                "goal": "freeze dynamic definitions",
                "session_id": "graph-v2-api",
                "tasks": [
                    {"id": "source", "prompt": "SOURCE", "output_schema": _ITEMS_SCHEMA},
                    {
                        "id": "each",
                        "node_type": "foreach",
                        "dependencies": ["source"],
                        "foreach": {
                            "source": "tasks.source",
                            "path": "structured_output.items",
                            "max_items": 4,
                            "max_concurrent": 2,
                            "template": {
                                "node_type": "agent",
                                "prompt": "ITEM:${item.value}",
                                "output_schema": _ITEM_SCHEMA,
                            },
                        },
                    },
                    {
                        "id": "wait",
                        "node_type": "wait_event",
                        "dependencies": ["each"],
                        "wait_event": {
                            "event_type": "order.ready",
                            "deadline_seconds": 60,
                            "payload_schema": _ITEM_SCHEMA,
                        },
                    },
                ],
            },
        )
        assert created.status_code == 202, created.json()
        run_id = created.json()["run_id"]
        revisions = client.get(
            f"/v1/runs/{run_id}/graph-revisions",
            headers={"Authorization": "Bearer owner-token"},
        )
    assert revisions.status_code == 200
    nodes = revisions.json()["items"][0]["nodes"]
    assert [node["node_type"] for node in nodes] == ["agent", "foreach", "wait_event"]
    assert nodes[1]["definition"]["foreach"]["max_items"] == 4
    assert nodes[2]["definition"]["wait_event"]["event_type"] == "order.ready"


class _ForeachAgent:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        self.calls.append(content)
        if content.startswith("SOURCE"):
            return '{"items":["one","two","three","four"]}'
        if content.startswith("ITEM:"):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.05)
                value = content.split(":", 1)[1].split("\n", 1)[0]
                return '{"value":"' + value + '"}'
            finally:
                self.active -= 1
        return "after"


class _RecoveringForeachAgent(_ForeachAgent):
    def __init__(self) -> None:
        super().__init__()
        self.fail_two = True

    async def process_direct(self, content: str, **kwargs: Any) -> str:
        if content.startswith("ITEM:two") and self.fail_two:
            self.calls.append(content)
            raise RuntimeError("transient item failure")
        return await super().process_direct(content, **kwargs)


@pytest.mark.asyncio
async def test_foreach_expands_durable_tasks_with_local_concurrency_limit(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-foreach.db")
    agent = _ForeachAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="bounded foreach",
                user_id="foreach-owner",
                session_id="foreach-session",
                tasks=_foreach_tasks(),
                aggregate=False,
                max_concurrent=8,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=8)

        assert completed.status == "completed", completed.error
        assert agent.max_active == 2
        tasks = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        by_spec = {str(task.payload.get("spec_id")): task for task in tasks}
        children = [task for task in tasks if task.parent_task_id == by_spec["each"].task_id]
        assert len(children) == 4
        assert all(task.status == "completed" for task in children)
        assert all(
            task.payload["graph_revision_id"] == completed.graph_revision_id for task in children
        )
        assert by_spec["each"].result["item_count"] == 4
        assert by_spec["each"].result["structured_output"]["count"] == 4
        assert by_spec["after"].status == "completed"
        assert completed.total_task_count == 7
        events = store.list_runtime_events(submitted.run_id)
        assert "foreach.expanded" in [event.type for event in events]
        assert "foreach.completed" in [event.type for event in events]
    finally:
        await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_foreach_resume_reuses_frozen_expansion_without_duplicate_children(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-foreach-resume.db")
    agent = _RecoveringForeachAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=4)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="resume foreach",
                user_id="foreach-owner",
                session_id="foreach-resume",
                tasks=_foreach_tasks(),
                aggregate=False,
                fail_fast=True,
                max_concurrent=4,
            )
        )
        failed = await runtime.wait(submitted.run_id, timeout=8)
        assert failed.status == "failed"
        before = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        assert len([task for task in before if task.parent_task_id]) == 4

        agent.fail_two = False
        await runtime.resume(submitted.run_id)
        completed = await runtime.wait(submitted.run_id, timeout=8)
        assert completed.status == "completed", completed.error
        after = store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        assert len([task for task in after if task.parent_task_id]) == 4
        foreach = next(task for task in after if task.payload.get("spec_id") == "each")
        assert foreach.result["stop_reason"] == "foreach_completed"
    finally:
        await runtime.close()


def _wait_task(deadline_seconds: int = 60) -> GraphTaskSpec:
    return GraphTaskSpec(
        id="wait",
        prompt="",
        node_type="wait_event",
        wait_event={
            "event_type": "order.ready",
            "deadline_seconds": deadline_seconds,
            "payload_schema": _ITEM_SCHEMA,
        },
    )


async def _wait_for_status(
    store: PostgresTestStore, run_id: str, status: str, timeout: float = 5
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = store.get_runtime_run(run_id)
        if record is not None and record.status == status:
            return record
        await asyncio.sleep(0.05)
    return store.get_runtime_run(run_id)


@pytest.mark.asyncio
async def test_wait_event_token_schema_owner_and_duplicate_delivery(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-wait-event.db")
    store.create_api_access_token(user_id="event-owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other-owner", actor_id="test", token="other-token")
    agent = _ForeachAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    await runtime.start()
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="wait for callback",
            user_id="event-owner",
            session_id="event-session",
            tasks=[_wait_task(), GraphTaskSpec(id="after", prompt="AFTER", dependencies=["wait"])],
            aggregate=False,
        )
    )
    waiting = await runtime.wait(submitted.run_id, timeout=4)
    assert waiting.status == "waiting_external"
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    try:
        with client:
            own = client.get(
                f"/v1/runs/{submitted.run_id}/event-waits",
                headers={"Authorization": "Bearer owner-token"},
            )
            foreign = client.get(
                f"/v1/runs/{submitted.run_id}/event-waits",
                headers={"Authorization": "Bearer other-token"},
            )
            assert own.status_code == 200
            assert foreign.status_code == 404
            wait_id = own.json()["items"][0]["wait_id"]
            issued = client.post(
                f"/v1/runs/{submitted.run_id}/event-waits/{wait_id}/token",
                headers={"Authorization": "Bearer owner-token"},
            )
            assert issued.status_code == 201
            old_token = issued.json()["token"]
            assert "token" not in own.json()["items"][0]

            rotated = client.post(
                f"/v1/runs/{submitted.run_id}/event-waits/{wait_id}/token",
                headers={"Authorization": "Bearer owner-token"},
            )
            assert rotated.status_code == 201
            token = rotated.json()["token"]
            assert token != old_token

            invalid_token = client.post(
                f"/v1/run-events/{wait_id}",
                headers={"X-Porthouse-Event-Token": old_token},
                json={"event_type": "order.ready", "payload": {"value": "ok"}},
            )
            assert invalid_token.status_code == 404
            invalid_payload = client.post(
                f"/v1/run-events/{wait_id}",
                headers={"X-Porthouse-Event-Token": token},
                json={"event_type": "order.ready", "payload": {"wrong": True}},
            )
            assert invalid_payload.status_code == 422
            delivered = client.post(
                f"/v1/run-events/{wait_id}",
                headers={"X-Porthouse-Event-Token": token},
                json={"event_type": "order.ready", "payload": {"value": "ok"}},
            )
            assert delivered.status_code == 200
            assert delivered.json()["duplicate"] is False
            duplicate = client.post(
                f"/v1/run-events/{wait_id}",
                headers={"X-Porthouse-Event-Token": token},
                json={"event_type": "order.ready", "payload": {"value": "ok"}},
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["duplicate"] is True
            conflicting_duplicate = client.post(
                f"/v1/run-events/{wait_id}",
                headers={"X-Porthouse-Event-Token": token},
                json={"event_type": "order.ready", "payload": {"value": "changed"}},
            )
            assert conflicting_duplicate.status_code == 409

        completed = await _wait_for_status(store, submitted.run_id, "completed")
        assert completed.status == "completed", completed.error
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id)
        }
        assert tasks["wait"].result["structured_output"] == {"value": "ok"}
        assert tasks["after"].status == "completed"
        assert "event.received" in [
            event.type for event in store.list_runtime_events(submitted.run_id)
        ]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wait_event_deadline_expiry_is_single_winner(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-wait-expiry.db")
    runtime = NativeAgentRuntime(agent=_ForeachAgent(), store=store)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="expire callback",
                user_id="event-owner",
                session_id="event-expiry",
                tasks=[_wait_task()],
                aggregate=False,
                fail_fast=True,
            )
        )
        waiting = await runtime.wait(submitted.run_id, timeout=4)
        assert waiting.status == "waiting_external"
        with store._pool.connection() as connection:
            connection.execute(
                """UPDATE graph_event_waits SET deadline_at=clock_timestamp()-interval '1 second'
                   WHERE run_id=%s""",
                (submitted.run_id,),
            )

        def expire() -> list[Any]:
            return store.expire_due_graph_event_waits(run_id=submitted.run_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: expire(), range(2)))
        assert sum(len(rows) for rows in outcomes) == 1
        waits = store.list_graph_event_waits(submitted.run_id, expected_user_id="event-owner")
        assert waits[0].status == "expired"
        task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
        assert task.status == "failed"
        failed = await _wait_for_status(store, submitted.run_id, "failed")
        assert failed.status == "failed"
    finally:
        await runtime.close()
