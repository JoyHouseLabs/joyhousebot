import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.scenarios import ClarificationNode, ScenarioField, ScenarioVersion
from joyhousebot.orchestration.clarification import ClarificationEngine
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


class FakeAgent:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: Counter[str] = Counter()
        self.started = asyncio.Event()

    async def process_direct(
        self,
        content: str,
        *,
        execution_stream_callback=None,
        run_context=None,
        **_kwargs: Any,
    ) -> str:
        self.calls[content] += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if content == "BLOCK":
                await asyncio.Event().wait()
            if content == "SLOW":
                await asyncio.sleep(0.2)
            if content == "FAIL":
                raise RuntimeError("intentional failure")
            if content == "FLAKY" and self.calls[content] == 1:
                raise RuntimeError("first attempt failed")
            await asyncio.sleep(0.02)
            answer = '{"answer": 42}' if content == "JSON" else f"answer:{content}"
            if execution_stream_callback:
                await execution_stream_callback("llm_delta", {"content": "delta"})
                await execution_stream_callback(
                    "usage",
                    {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "cost_usd": 0.01,
                        "model": run_context.model if run_context else "fake-model",
                    },
                )
                await execution_stream_callback("final", {"content": answer})
            return answer
        finally:
            self.active -= 1


class EchoCapability(Tool):
    name = "echo"
    description = "Echo structured text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def execute(self, text: str, **_kwargs: Any) -> str:
        return f"echo:{text}"


@pytest.fixture
def store(tmp_path: Path) -> PostgresTestStore:
    return PostgresTestStore(tmp_path / "runtime.db")


@pytest.mark.asyncio
async def test_agent_run_lifecycle_usage_events_and_idempotency(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    options = AgentOptions(
        prompt="hello",
        user_id="user-a",
        session_id="s1",
        agent_id="default",
        model="fake-model",
        idempotency_key="request-1",
    )

    submitted = await runtime.submit_run(options)
    repeated = await runtime.submit_run(options)
    completed = await runtime.wait(submitted.run_id, timeout=2)

    assert repeated.run_id == submitted.run_id
    assert completed.status == "completed"
    assert completed.result["content"] == "answer:hello"
    assert completed.result["usage"]["total_tokens"] == 15
    events = store.list_runtime_events(submitted.run_id)
    assert [event.type for event in events][:2] == ["run.accepted", "run.queued"]
    assert "usage.updated" in [event.type for event in events]
    assert [event.type for event in events][-1] == "run.completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_main_coordinator_can_select_scenario_pause_and_resume_same_run(
    store: PostgresTestStore,
) -> None:
    store.save_scenario_version(
        ScenarioVersion(
            scenario_id="speech",
            version=1,
            name="Speech",
            description="Generate speech",
            fields=(ScenarioField("voice", "string", required=True),),
            nodes=(ClarificationNode("ask_voice", "question", "Which voice?", ("voice",)),),
            edges=(),
            allowed_capabilities=(),
        ),
        status="published",
    )

    class CoordinatingAgent:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def process_direct(self, content: str, *, run_context, **_kwargs: Any) -> str:
            self.calls.append(content)
            if run_context.output_schema:
                return json.dumps(
                    {
                        "intent": "speech_generation",
                        "summary": "生成语音前需要选择声音",
                        "scenario_id": "speech",
                        "scenario_inputs": {},
                        "execution_class": "interactive",
                        "estimated_duration_seconds": 30,
                        "selected_capabilities": [],
                        "selected_skills": [],
                        "planned_steps": [{"name": "speak", "objective": "generate speech"}],
                    }
                )
            return "speech ready"

    agent = CoordinatingAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Please make audio",
            user_id="user-a",
            session_id="session-a",
            metadata={"coordinator_required": True},
        )
    )
    waiting = await runtime.wait(submitted.run_id, timeout=2)
    assert waiting.status == "waiting_input"
    pending = store.list_pending_input_requests(submitted.run_id, expected_user_id="user-a")
    assert pending[0].question == "Which voice?"

    ClarificationEngine(store).resolve(
        run_id=submitted.run_id,
        user_id="user-a",
        input_request_id=pending[0].input_request_id,
        answers={"voice": "calm"},
    )
    completed = await runtime.wait(submitted.run_id, timeout=3)
    assert completed.status == "completed"
    assert completed.result["content"] == "speech ready"
    assert len(agent.calls) == 2
    events = [item.type for item in store.list_runtime_events(submitted.run_id)]
    assert "user_input.requested" in events
    assert "plan.created" in events
    await runtime.close()


@pytest.mark.asyncio
async def test_main_coordinator_materializes_multi_agent_plan_as_graph(
    store: PostgresTestStore,
) -> None:
    class PlanningAgent(FakeAgent):
        async def process_direct(self, content: str, *, run_context, **kwargs: Any) -> str:
            if run_context.output_schema:
                return json.dumps(
                    {
                        "intent": "research_and_compare",
                        "summary": "并行调研后汇总",
                        "scenario_id": None,
                        "scenario_inputs": {},
                        "execution_class": "background",
                        "estimated_duration_seconds": 300,
                        "selected_capabilities": [],
                        "selected_skills": [],
                        "planned_steps": [
                            {
                                "name": "research-a",
                                "objective": "research source A",
                                "can_run_in_parallel": True,
                            },
                            {
                                "name": "research-b",
                                "objective": "research source B",
                                "can_run_in_parallel": True,
                            },
                        ],
                    }
                )
            return await super().process_direct(content, run_context=run_context, **kwargs)

    agent = PlanningAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Research and compare two sources",
            user_id="user-a",
            session_id="session-plan",
            metadata={"coordinator_required": True},
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=5)

    assert completed.status == "completed"
    assert completed.kind == "graph"
    tasks = store.list_runtime_tasks(run_id=submitted.run_id)
    assert len(tasks) == 2
    assert {task.status for task in tasks} == {"completed"}
    assert agent.max_active == 2
    events = [item.type for item in store.list_runtime_events(submitted.run_id)]
    assert "plan.updated" in events
    assert events[-1] == "run.completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_routes_shared_agents_and_persists_user_session_identity(
    store: PostgresTestStore,
) -> None:
    default_agent = FakeAgent()
    researcher = FakeAgent()
    store.save_agent_revision(
        AgentDefinition(
            agent_id="researcher",
            name="Researcher",
            role="specialist",
        ),
        AgentRevision(
            revision_id="researcher:test-v1",
            agent_id="researcher",
            version=1,
            model_policy={"primary": "test/model"},
            status="published",
        ),
    )
    agents = {
        "default": default_agent,
        "researcher": researcher,
        "researcher:test-v1": researcher,
    }
    runtime = NativeAgentRuntime(
        agent=default_agent,
        agent_resolver=agents.get,
        store=store,
    )

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="research",
            user_id="user-42",
            session_id="session-7",
            agent_id="researcher",
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=2)

    assert completed.status == "completed"
    assert completed.user_id == "user-42"
    assert completed.session_id == "session-7"
    assert completed.agent_id == "researcher"
    assert researcher.calls["research"] == 1
    assert default_agent.calls["research"] == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_child_terminal_state_is_appended_to_parent_timeline(
    store: PostgresTestStore,
) -> None:
    runtime = NativeAgentRuntime(agent=FakeAgent(), store=store)
    parent = await runtime.submit_run(
        AgentOptions(prompt="parent", user_id="user-a", session_id="main")
    )
    await runtime.wait(parent.run_id, timeout=2)
    child = await runtime.submit_run(
        AgentOptions(
            prompt="child",
            user_id="user-a",
            session_id="main:child",
            parent_run_id=parent.run_id,
            root_run_id=parent.run_id,
        )
    )
    await runtime.wait(child.run_id, timeout=2)

    parent_events = store.list_runtime_events(parent.run_id)
    terminal = [event for event in parent_events if event.type == "subagent.completed"]
    assert terminal[-1].data["child_run_id"] == child.run_id
    assert terminal[-1].data["content_preview"] == "answer:child"
    await runtime.close()


@pytest.mark.asyncio
async def test_same_session_id_is_isolated_by_user_id(store: PostgresTestStore) -> None:
    class ContextAgent:
        def __init__(self) -> None:
            self.contexts: dict[str, tuple[str, str]] = {}

        async def process_direct(self, content: str, *, session_key: str, run_context, **_kwargs):
            self.contexts[content] = (session_key, run_context.user_id)
            return content

    agent = ContextAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    first = await runtime.submit_run(
        AgentOptions(prompt="first", user_id="user-a", session_id="main")
    )
    second = await runtime.submit_run(
        AgentOptions(prompt="second", user_id="user-b", session_id="main")
    )
    await asyncio.gather(
        runtime.wait(first.run_id, timeout=2),
        runtime.wait(second.run_id, timeout=2),
    )

    assert agent.contexts["first"][1] == "user-a"
    assert agent.contexts["second"][1] == "user-b"
    assert agent.contexts["first"][0] != agent.contexts["second"][0]
    await runtime.close()


@pytest.mark.asyncio
async def test_agent_run_timeout_and_resume(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    slow = await runtime.submit_run(AgentOptions(prompt="SLOW", timeout_seconds=0.01))
    timed_out = await runtime.wait(slow.run_id, timeout=2)
    assert timed_out.status == "timed_out"

    flaky = await runtime.submit_run(AgentOptions(prompt="FLAKY"))
    failed = await runtime.wait(flaky.run_id, timeout=2)
    assert failed.status == "failed"
    await runtime.resume(flaky.run_id)
    resumed = await runtime.wait(flaky.run_id, timeout=2)
    assert resumed.status == "completed"
    assert agent.calls["FLAKY"] == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_agent_run_validates_structured_output(store: PostgresTestStore) -> None:
    runtime = NativeAgentRuntime(agent=FakeAgent(), store=store)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    valid = await runtime.submit_run(
        AgentOptions(prompt="JSON", system_prompt="Be exact", output_schema=schema)
    )
    valid = await runtime.wait(valid.run_id, timeout=2)
    assert valid.result["structured_output"] == {"answer": 42}

    invalid = await runtime.submit_run(AgentOptions(prompt="hello", output_schema=schema))
    invalid = await runtime.wait(invalid.run_id, timeout=2)
    assert invalid.status == "failed"
    assert invalid.result["stop_reason"] == "structured_output_error"
    await runtime.close()


@pytest.mark.asyncio
async def test_agent_run_can_be_cancelled(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_run(AgentOptions(prompt="BLOCK"))
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    assert await runtime.cancel(submitted.run_id, "test cancellation")
    cancelled = await runtime.wait(submitted.run_id, timeout=2)

    assert cancelled.status == "cancelled"
    assert cancelled.error["message"] == "test cancellation"
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_executes_parallel_waves_and_dependencies(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    graph = TaskGraphSpec(
        goal="combine",
        tasks=[
            GraphTaskSpec(id="a", prompt="A"),
            GraphTaskSpec(id="b", prompt="B"),
            GraphTaskSpec(id="c", prompt="C", dependencies=["a", "b"]),
        ],
        max_concurrent=2,
        aggregate=False,
    )

    submitted = await runtime.submit_graph(graph)
    completed = await runtime.wait(submitted.run_id, timeout=3)

    assert completed.status == "completed"
    assert agent.max_active == 2
    tasks = store.list_runtime_tasks(run_id=submitted.run_id)
    assert {task.status for task in tasks} == {"completed"}
    result = completed.result["structured_output"]["tasks"]
    assert result["c"]["content"].startswith("answer:C")
    assert '"a": "answer:A"' in result["c"]["content"]
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_task_invokes_capability_through_unified_dispatcher(
    store: PostgresTestStore,
) -> None:
    class CapabilityAgent:
        def __init__(self) -> None:
            self.capabilities = CapabilityRegistry(store=store)
            self.capabilities.register_tool(EchoCapability())

        async def process_direct(self, *_args: Any, **_kwargs: Any) -> str:
            raise AssertionError("direct capability task must not call the model")

    runtime = NativeAgentRuntime(agent=CapabilityAgent(), store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="echo",
            tasks=[
                GraphTaskSpec(
                    id="echo",
                    prompt="",
                    capability_id="echo",
                    capability_input={"text": "hello"},
                )
            ],
            aggregate=False,
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=3)

    assert completed.status == "completed"
    task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert task.result["content"] == "echo:hello"
    invocations = store.list_capability_invocations(submitted.run_id)
    assert invocations[0].capability_id == "echo"
    assert invocations[0].status == "succeeded"
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_structured_aggregation_records_audit_artifact(store: PostgresTestStore) -> None:
    class StructuredAgent:
        async def process_direct(self, content: str, **_kwargs: Any) -> str:
            return '{"items":["' + content + '"],"source":"' + content + '"}'

    runtime = NativeAgentRuntime(agent=StructuredAgent(), store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="merge structured research",
            tasks=[GraphTaskSpec(id="a", prompt="a"), GraphTaskSpec(id="b", prompt="b")],
            aggregation_policy={"mode": "structured_merge", "version": "v1"},
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=3)

    assert completed.status == "completed"
    aggregation = completed.result["structured_output"]["aggregation"]
    assert aggregation["result"] == {"items": ["a", "b"], "source": "a"}
    assert aggregation["conflicts"][0]["path"] == "source"
    assert any(item["name"] == "aggregation-audit" for item in store.list_runtime_artifacts(submitted.run_id))
    assert "aggregation.started" in [item.type for item in store.list_runtime_events(submitted.run_id)]
    assert "aggregation.completed" in [item.type for item in store.list_runtime_events(submitted.run_id)]
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_fail_fast_skips_unstarted_dependents(store: PostgresTestStore) -> None:
    runtime = NativeAgentRuntime(agent=FakeAgent(), store=store)
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="fail",
            tasks=[
                GraphTaskSpec(id="a", prompt="FAIL"),
                GraphTaskSpec(id="b", prompt="B", dependencies=["a"]),
            ],
            fail_fast=True,
            aggregate=False,
        )
    )
    failed = await runtime.wait(submitted.run_id, timeout=3)

    assert failed.status == "failed"
    tasks = {
        task.payload["spec_id"]: task for task in store.list_runtime_tasks(run_id=submitted.run_id)
    }
    assert tasks["a"].status == "failed"
    assert tasks["b"].status == "skipped"
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_recovers_a_queued_run(store: PostgresTestStore) -> None:
    options = AgentOptions(prompt="recovered")
    record, _ = store.create_runtime_run(
        run_id="recover-me",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    assert record.status == "queued"

    runtime = NativeAgentRuntime(agent=FakeAgent(), store=store)
    await runtime.start()
    recovered = await runtime.wait("recover-me", timeout=2)

    assert recovered.status == "completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_two_runtime_workers_claim_a_run_only_once(store: PostgresTestStore) -> None:
    options = AgentOptions(prompt="single execution")
    store.create_runtime_run(
        run_id="shared-run",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    agent = FakeAgent()
    first = NativeAgentRuntime(agent=agent, store=store)
    second = NativeAgentRuntime(agent=agent, store=store)

    await asyncio.gather(first.start(), second.start())
    completed = await first.wait("shared-run", timeout=2)

    assert completed.status == "completed"
    assert agent.calls["single execution"] == 1
    await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_two_runtime_workers_share_graph_tasks(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    first = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    second = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=1)
    await asyncio.gather(first.start(), second.start())

    run = await first.submit_graph(
        TaskGraphSpec(
            goal="distributed",
            tasks=[GraphTaskSpec(id=f"t{i}", prompt="SLOW") for i in range(8)],
            aggregate=False,
        )
    )
    completed = await first.wait(run.run_id, timeout=5)

    assert completed.status == "completed"
    claims = [item for item in store.list_runtime_logs(run.run_id) if item.stage == "task.claimed"]
    assert len(claims) == 8
    assert len({item.worker_id for item in claims}) == 2
    await asyncio.gather(first.close(), second.close())


@pytest.mark.asyncio
async def test_graph_recovery_reuses_completed_task_results(store: PostgresTestStore) -> None:
    tasks = [
        GraphTaskSpec(id="a", prompt="A"),
        GraphTaskSpec(id="b", prompt="B", dependencies=["a"]),
    ]
    store.create_runtime_run(
        run_id="partial-graph",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="graph",
        prompt="recover graph",
        options={
            "goal": "recover graph",
            "tasks": [asdict(task) for task in tasks],
            "max_concurrent": 2,
            "fail_fast": False,
            "aggregate": False,
        },
    )
    store.create_runtime_task(
        task_id="partial-graph:a",
        run_id="partial-graph",
        name="a",
        payload={"spec_id": "a", "prompt": "A"},
    )
    store.create_runtime_task(
        task_id="partial-graph:b",
        run_id="partial-graph",
        name="b",
        payload={"spec_id": "b", "prompt": "B"},
        dependencies=["partial-graph:a"],
    )
    store.update_runtime_task("partial-graph:a", status="running")
    store.update_runtime_task(
        "partial-graph:a",
        status="completed",
        result={"status": "completed", "content": "saved:A", "tools_used": []},
    )

    agent = FakeAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    await runtime.start()
    completed = await runtime.wait("partial-graph", timeout=2)

    assert completed.status == "completed"
    assert agent.calls["A"] == 0
    assert sum(count for prompt, count in agent.calls.items() if prompt.startswith("B")) == 1
    assert completed.result["structured_output"]["tasks"]["a"]["content"] == "saved:A"
    await runtime.close()


@pytest.mark.asyncio
async def test_remote_worker_cancel_cannot_be_overwritten_by_completion(
    store: PostgresTestStore,
) -> None:
    class ControllableAgent:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def process_direct(self, content: str, **_kwargs: Any) -> str:
            self.started.set()
            await self.release.wait()
            return "finished after cancellation"

    agent = ControllableAgent()
    owner = NativeAgentRuntime(agent=agent, store=store)
    remote = NativeAgentRuntime(agent=agent, store=store)
    run = await owner.submit_run(AgentOptions(prompt="remote cancel"))
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    assert await remote.cancel(run.run_id, "cancelled remotely")
    agent.release.set()
    final = await owner.wait(run.run_id, timeout=2)

    assert final.status == "cancelled"
    assert final.result["status"] == "cancelled"
    await asyncio.gather(owner.close(), remote.close())


def test_graph_validation_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValueError, match="cycle"):
        validate_and_order_graph(
            [
                GraphTaskSpec(id="a", prompt="A", dependencies=["b"]),
                GraphTaskSpec(id="b", prompt="B", dependencies=["a"]),
            ]
        )
    with pytest.raises(ValueError, match="unknown dependencies"):
        validate_and_order_graph([GraphTaskSpec(id="a", prompt="A", dependencies=["missing"])])
