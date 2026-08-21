"""Explicit Graph aggregate nodes are bounded, replayable, and observable."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


class _AggregateAgent:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        self.calls.append(content)
        if "Synthesize a final answer" in content:
            return "synthesized answer from left and right"
        if "LEFT" in content:
            return '{"profile":{"name":"left"},"tags":["a"],"score":1}'
        if "RIGHT" in content:
            return '{"profile":{"name":"right"},"tags":["b"],"score":2}'
        raise AssertionError(f"unexpected prompt: {content}")


def _source_tasks() -> list[GraphTaskSpec]:
    schema = {"type": "object"}
    return [
        GraphTaskSpec(id="left", prompt="LEFT", output_schema=schema),
        GraphTaskSpec(id="right", prompt="RIGHT", output_schema=schema),
    ]


def test_aggregate_node_rejects_missing_sources_and_open_configuration() -> None:
    with pytest.raises(ValueError, match="requires at least one dependency"):
        validate_and_order_graph(
            [GraphTaskSpec(id="merge", prompt="", node_type="aggregate")]
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_and_order_graph(
            [
                GraphTaskSpec(id="source", prompt="SOURCE"),
                GraphTaskSpec(
                    id="merge",
                    prompt="",
                    node_type="aggregate",
                    dependencies=["source"],
                    aggregate={"mode": "raw", "expression": "arbitrary_code()"},
                ),
            ]
        )


@pytest.mark.asyncio
async def test_deterministic_aggregate_node_merges_dependency_outputs(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-aggregate-deterministic.db")
    agent = _AggregateAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    try:
        submitted = await runtime.submit_graph(
            TaskGraphSpec(
                goal="merge two structured sources",
                user_id="aggregate-owner",
                session_id="aggregate-deterministic",
                tasks=[
                    *_source_tasks(),
                    GraphTaskSpec(
                        id="merge",
                        prompt="",
                        node_type="aggregate",
                        dependencies=["left", "right"],
                        aggregate={
                            "mode": "structured_merge",
                            "conflict_resolution": "prefer_last",
                        },
                        output_schema={"type": "object"},
                    ),
                ],
                aggregate=False,
            )
        )
        completed = await runtime.wait(submitted.run_id, timeout=8)

        assert completed.status == "completed", completed.error
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        }
        merged = tasks["merge"].result
        assert merged["structured_output"] == {
            "profile": {"name": "right"},
            "score": 2,
            "tags": ["a", "b"],
        }
        assert merged["aggregation"]["execution"] == "deterministic"
        assert merged["aggregation"]["conflicts"][0]["path"] == "profile.name"
        event_types = [
            event.type
            for event in store.list_runtime_events(submitted.run_id)
            if event.task_id == tasks["merge"].task_id
        ]
        assert event_types.count("aggregation.started") == 1
        assert event_types.count("aggregation.completed") == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_llm_aggregate_node_is_an_explicit_task(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "graph-aggregate-llm.db")
    agent = _AggregateAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    try:
        await asyncio.gather(first.start(), second.start())
        submitted = await first.submit_graph(
            TaskGraphSpec(
                goal="synthesize two sources",
                user_id="aggregate-owner",
                session_id="aggregate-llm",
                tasks=[
                    *_source_tasks(),
                    GraphTaskSpec(
                        id="synthesis",
                        prompt="write the final answer",
                        node_type="aggregate",
                        dependencies=["left", "right"],
                        aggregate={"mode": "llm_synthesis", "max_items": 2},
                    ),
                ],
                aggregate=False,
            )
        )
        completed = await first.wait(submitted.run_id, timeout=8)

        assert completed.status == "completed", completed.error
        tasks = {
            task.payload["spec_id"]: task
            for task in store.list_runtime_tasks(run_id=submitted.run_id, limit=100)
        }
        assert tasks["synthesis"].result["content"] == (
            "synthesized answer from left and right"
        )
        assert tasks["synthesis"].result["aggregation"]["execution"] == "llm_synthesis"
        assert sum("Synthesize a final answer" in call for call in agent.calls) == 1
    finally:
        await asyncio.gather(first.close(), second.close())
