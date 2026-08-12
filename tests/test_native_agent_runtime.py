import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from joyhousebot.capabilities import CapabilityRegistry
from joyhousebot.contracts.tools import Tool
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.scenarios import ClarificationNode, ScenarioField, ScenarioVersion
from joyhousebot.orchestration.clarification import ClarificationEngine
from joyhousebot.orchestration.task_graph import validate_and_order_graph
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.runtime.schema_limits import MAX_STRUCTURED_CONTRACT_BYTES
from tests.support.capabilities import register_tool_fixture, tool_definition
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


@pytest.mark.asyncio
async def test_oversized_output_schema_fails_before_run_creation(
    store: PostgresTestStore,
) -> None:
    runtime = NativeAgentRuntime(agent=FakeAgent(), store=store)
    schema = {
        "type": "object",
        "description": "x" * MAX_STRUCTURED_CONTRACT_BYTES,
    }
    with pytest.raises(ValueError, match="output_schema.*maximum"):
        await runtime.submit_run(
            AgentOptions(
                prompt="never queued",
                user_id="schema-limit-user",
                session_id="schema-limit-session",
                output_schema=schema,
            )
        )
    assert store.list_runtime_runs(
        user_id="schema-limit-user",
        session_id="schema-limit-session",
    ) == []
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_preserves_monitor_metadata_and_reconciles_top_level_agent(
    store: PostgresTestStore,
) -> None:
    captured_contexts: list[Any] = []
    reconciled: list[dict[str, Any]] = []

    class CapturingAgent(FakeAgent):
        async def process_direct(self, content: str, *, run_context, **kwargs: Any) -> str:
            captured_contexts.append(run_context)
            return await super().process_direct(
                content, run_context=run_context, **kwargs
            )

    def reconcile(**kwargs: Any) -> None:
        reconciled.append(kwargs)

    runtime = NativeAgentRuntime(
        agent=CapturingAgent(),
        store=store,
        monitor_reconciler=reconcile,
    )
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="monitor",
            user_id="user-monitor",
            session_id="monitor-session",
            metadata={
                "schedule_id": "schedule-1",
                "schedule_payload_kind": "agent_monitor",
                "monitor_context_mode": "light",
                "_runtime_schedule_submission_ready": True,
            },
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=2)

    assert completed.status == "completed"
    assert captured_contexts[0].metadata["schedule_id"] == "schedule-1"
    assert captured_contexts[0].metadata["monitor_context_mode"] == "light"
    assert "_runtime_schedule_submission_ready" not in captured_contexts[0].metadata
    assert reconciled == []
    ordinary = await runtime.submit_run(
        AgentOptions(
            prompt="ordinary",
            user_id="user-monitor",
            session_id="main",
        )
    )
    assert (await runtime.wait(ordinary.run_id, timeout=2)).status == "completed"
    assert reconciled[0]["user_id"] == "user-monitor"
    assert reconciled[0]["profile"].definition.agent_id == ordinary.agent_id
    await runtime.close()


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
                        # The deterministic router already selected speech;
                        # the runtime must keep it even if the model omits it.
                        "scenario_id": None,
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
                metadata={
                    "coordinator_required": True,
                    "orchestration": {
                        "mode": "scenario",
                        "scenario_id": "speech",
                        "version": 1,
                    },
                    "routing_decision": {
                    "scenario_id": "speech",
                    "scenario_version": 1,
                    "reason_code": "RULE_CONTAINS",
                },
                "scenario_inputs": {},
            },
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
async def test_main_coordinator_can_pause_for_dynamic_structured_input(
    store: PostgresTestStore,
) -> None:
    class DynamicClarificationAgent(FakeAgent):
        async def process_direct(self, content: str, *, run_context, **kwargs: Any) -> str:
            if run_context.output_schema:
                if "Answers already supplied" not in content:
                    return json.dumps(
                        {
                            "intent": "people_search",
                            "summary": "Need search goal",
                            "scenario_id": None,
                            "scenario_inputs": {},
                            "execution_class": "interactive",
                            "estimated_duration_seconds": 30,
                            "selected_capabilities": [],
                            "selected_skills": [],
                            "planned_steps": [],
                            "clarification": {
                                "question": "What should the search produce?",
                                "fields": [{
                                    "name": "goal", "label": "Goal", "value_type": "string",
                                    "required": True, "input_mode": "single_choice",
                                    "options": [{"value": "recruit", "label": "Recruit candidates"}],
                                }],
                            },
                        }
                    )
                return json.dumps(
                    {
                        "intent": "people_search", "summary": "Search planned", "scenario_id": None,
                        "scenario_inputs": {}, "execution_class": "interactive",
                        "estimated_duration_seconds": 30, "selected_capabilities": [],
                        "selected_skills": [], "planned_steps": [], "clarification": None,
                    }
                )
            return "dynamic input accepted"

    runtime = NativeAgentRuntime(agent=DynamicClarificationAgent(), store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Find people", user_id="user-a", session_id="dynamic-session",
            metadata={"coordinator_required": True},
        )
    )
    waiting = await runtime.wait(submitted.run_id, timeout=2)
    assert waiting.status == "waiting_input"
    pending = store.list_pending_input_requests(waiting.run_id, expected_user_id="user-a")
    assert pending[0].source == "agent"
    assert pending[0].fields[0]["input_mode"] == "single_choice"
    assert store.resolve_dynamic_input_request(
        input_request_id=pending[0].input_request_id,
        run_id=waiting.run_id,
        user_id="user-a",
        answers={"goal": "recruit"},
    )
    store.notify_work(waiting.run_id)
    completed = await runtime.wait(waiting.run_id, timeout=3)
    assert completed.status == "completed", completed.error
    assert completed.result["content"] == "dynamic input accepted"
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
    definition = tool_definition(EchoCapability())

    class CapabilityAgent:
        def __init__(self) -> None:
            self.capabilities = CapabilityRegistry(store=store)
            tool = EchoCapability()
            register_tool_fixture(self.capabilities, tool, definition=definition)

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
                    capability=definition.ref,
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


@pytest.mark.asyncio
async def test_remote_cancel_aborts_running_execution(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    owner = NativeAgentRuntime(agent=agent, store=store, lease_seconds=5)
    remote = NativeAgentRuntime(agent=agent, store=store, worker_enabled=False, scheduler_enabled=False)
    submitted = await owner.submit_run(AgentOptions(prompt="BLOCK"))
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    assert await remote.cancel(submitted.run_id, "remote stop")
    cancelled = await owner.wait(submitted.run_id, timeout=5)

    assert cancelled.status == "cancelled"
    assert cancelled.error["message"] == "remote stop"
    assert cancelled.result["status"] == "cancelled"
    types = [event.type for event in store.list_runtime_events(submitted.run_id)]
    assert "run.cancelling" in types
    assert "run.completed" not in types
    await asyncio.gather(owner.close(), remote.close())


@pytest.mark.asyncio
async def test_execution_aborts_when_cancel_lands_between_claim_and_start(
    store: PostgresTestStore,
) -> None:
    agent = FakeAgent()
    options = AgentOptions(prompt="never runs")
    store.create_runtime_run(
        run_id="cancel-at-start",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    runtime = NativeAgentRuntime(agent=agent, store=store, worker_enabled=False, scheduler_enabled=False)
    claimed = store.claim_runtime_run(
        "cancel-at-start", worker_id=runtime.worker_id, lease_seconds=30
    )
    assert claimed is not None
    store.request_runtime_cancel("cancel-at-start", reason="cancelled before start")

    with pytest.raises(asyncio.CancelledError):
        await runtime._execute_agent_record(claimed, CancellationToken())

    assert "never runs" not in agent.calls
    record = store.get_runtime_run("cancel-at-start")
    assert record.status == "cancelled"
    assert record.error["message"] == "run was cancelled before execution started"


@pytest.mark.asyncio
async def test_non_executing_role_can_register_cluster_presence(
    store: PostgresTestStore,
) -> None:
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=False,
        presence_enabled=True,
        worker_name="channel-worker",
        capabilities={"channels": True},
    )

    await runtime.start()
    worker = next(
        item
        for item in store.list_runtime_workers()
        if item["worker_id"] == runtime.worker_id
    )
    assert worker["healthy"] is True
    assert worker["capabilities"] == {"channels": True}

    await runtime.close()
    worker = next(
        item
        for item in store.list_runtime_workers()
        if item["worker_id"] == runtime.worker_id
    )
    assert worker["status"] == "offline"


@pytest.mark.asyncio
async def test_dead_worker_cancel_completes_via_recovery(store: PostgresTestStore) -> None:
    agent = FakeAgent()
    options = AgentOptions(prompt="orphaned")
    store.create_runtime_run(
        run_id="dead-owner",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    claimed = store.claim_runtime_run("dead-owner", worker_id="dead-worker", lease_seconds=5)
    assert claimed is not None
    assert store.request_runtime_cancel("dead-owner", reason="owner died")["lease_alive"] is True
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            "UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 minute'"
            " WHERE run_id='dead-owner'"
        )

    runtime = NativeAgentRuntime(agent=agent, store=store)
    await runtime.start()
    record = await runtime.wait("dead-owner", timeout=2)

    assert record.status == "cancelled"
    assert record.error["message"] == "owner died"
    assert "orphaned" not in agent.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_same_session_next_run_waits_for_cancel_completion(
    store: PostgresTestStore,
) -> None:
    agent = FakeAgent()
    owner = NativeAgentRuntime(agent=agent, store=store, lease_seconds=5)
    remote = NativeAgentRuntime(agent=agent, store=store, worker_enabled=False, scheduler_enabled=False)
    first = await owner.submit_run(AgentOptions(prompt="BLOCK", session_id="s-cancel"))
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    assert await remote.cancel(first.run_id, "make way")
    second = await owner.submit_run(AgentOptions(prompt="second", session_id="s-cancel"))
    await asyncio.sleep(0.5)
    # The cancel-requested run still owns the conversation until it is terminal.
    assert "second" not in agent.calls
    assert store.get_runtime_run(second.run_id).status == "queued"

    cancelled = await owner.wait(first.run_id, timeout=5)
    assert cancelled.status == "cancelled"
    completed = await owner.wait(second.run_id, timeout=5)
    assert completed.status == "completed"
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
