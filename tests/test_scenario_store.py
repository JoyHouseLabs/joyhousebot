from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from porthouse.application.context import Principal, RequestContext
from porthouse.application.run_commands import AgentRunTarget, ScenarioRunTarget
from porthouse.application.runs import CreateRunCommand, RunService
from porthouse.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from porthouse.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    ScenarioField,
    ScenarioVersion,
)
from porthouse.domain.skills import SkillRef
from porthouse.orchestration.planner import ScenarioPlanner
from tests.support.postgres_store import PostgresTestStore


def _scenario(question: str = "Which voice?") -> ScenarioVersion:
    return ScenarioVersion(
        scenario_id="text_to_speech",
        version=1,
        name="Text to speech",
        description="Generate an audio artifact",
        fields=(
            ScenarioField("text", "string", required=True),
            ScenarioField("voice", "string", required=True, enum=("default", "professional")),
        ),
        nodes=(
            ClarificationNode("ask_text", "question", "What text?", ("text",)),
            ClarificationNode("ask_voice", "question", question, ("voice",)),
            ClarificationNode("ready", "terminal", ""),
        ),
        edges=(
            ClarificationEdge("ask_text", "ask_voice", "present(text)"),
            ClarificationEdge("ask_voice", "ready", "present(voice)"),
        ),
        allowed_capabilities=(
            CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            CapabilityRef("artifact.store", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
        ),
        planning_mode="fixed",
        execution_policy={"execution_class": "interactive", "wait_seconds": 20},
        routing_rules=({"contains_any": ["语音", "朗读", "tts"]},),
    )


def test_scenario_round_trip_publish_and_latest(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "scenarios.db")
    store.save_scenario_version(_scenario())
    assert store.get_scenario_version("text_to_speech") is None
    store.publish_scenario("text_to_speech", 1)
    restored = store.get_scenario_version("text_to_speech")
    assert restored is not None
    assert restored.definition_dict() == _scenario().definition_dict()
    assert restored.status == "published"
    assert store.list_scenario_versions() == [restored]


def test_published_scenario_is_immutable(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "immutable-scenario.db")
    store.save_scenario_version(_scenario(), status="published")
    with pytest.raises(ValueError, match="immutable"):
        store.save_scenario_version(_scenario("Choose a voice"))


def test_fixed_scenario_compiles_validated_capability_graph(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "scenario-plan.db")
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            name="Speech synthesis",
            description="Generate audio",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="builtin.speech",
        )
    )
    scenario = replace(
        _scenario(),
        allowed_capabilities=(
            CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
        ),
        required_skills=(
            SkillRef("skill.voice-style", "1.0.0", f"sha256:{'a' * 64}"),
        ),
        execution_policy={
            "max_concurrent": 2,
            "aggregate": False,
            "tasks": [
                {
                    "id": "synthesize",
                    "capability": CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test").to_dict(),
                    "input": {"text": "${text}", "voice": "${voice}"},
                }
            ],
        },
    )

    graph = ScenarioPlanner(store).build_graph(
        scenario,
        goal="Generate speech",
        inputs={"text": "hello", "voice": "professional"},
        user_id="user-a",
        session_id="session-a",
        agent_id="default",
        idempotency_key="request-a",
        request_id="trace-a",
    )

    assert graph is not None
    assert graph.aggregate is False
    assert graph.tasks[0].capability is not None
    assert graph.tasks[0].capability.capability_id == "speech.synthesize"
    assert graph.tasks[0].capability_input == {
        "text": "hello",
        "voice": "professional",
    }
    assert graph.tasks[0].skill_names == ["voice-style"]


def test_fixed_graph_omits_missing_optional_fields_from_capability_input(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "optional-fields.db")
    store.publish_capability(
        CapabilityDefinition(
            name="Echo",
            ref=CapabilityRef("echo", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            description="Echo input",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="echo",
        )
    )
    scenario = ScenarioVersion(
        scenario_id="optional-input", version=1, name="Optional", description="Optional input",
        fields=(ScenarioField("query", "string", default=""), ScenarioField("platform", "string")),
        nodes=(), edges=(), allowed_capabilities=(CapabilityRef("echo", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),), planning_mode="fixed",
        execution_policy={"tasks": [{"id": "echo", "capability": CapabilityRef("echo", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test").to_dict(), "input": {"query": "${query}", "platform": "${platform}"}}]},
    )
    store.save_scenario_version(scenario, status="published")
    graph = ScenarioPlanner(store).build_graph(
        scenario, goal="test", inputs={"query": "python"}, user_id="user", session_id="session",
        agent_id="agent", idempotency_key=None, request_id="request",
    )
    assert graph is not None
    assert graph.tasks[0].capability_input == {"query": "python"}


@pytest.mark.asyncio
async def test_explicit_fixed_scenario_bypasses_coordinator_and_submits_graph(tmp_path: Path) -> None:
    backing = PostgresTestStore(tmp_path / "explicit-fixed-scenario.db")
    tool = CapabilityDefinition(
        name="Echo",
        ref=CapabilityRef("echo", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
        description="Echo input",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        adapter="echo",
    )
    backing.publish_capability(tool)
    scenario = ScenarioVersion(
        scenario_id="explicit.echo", version=1, name="Explicit echo", description="A fixed quickstart",
        fields=(ScenarioField("query", "string", required=True),), nodes=(), edges=(),
        allowed_capabilities=(tool.ref,), planning_mode="fixed",
        execution_policy={"tasks": [{"id": "echo", "capability": tool.ref.to_dict(), "input": {"query": "${query}"}}]},
        routing_rules=({"contains_any": ["echo"], "priority": 100},),
    )
    backing.save_scenario_version(scenario, status="published")

    class StoreProxy:
        saved_states: list[dict] = []

        def __getattr__(self, name):
            return getattr(backing, name)

        def save_run_scenario_state(self, **kwargs):
            self.saved_states.append(kwargs)

    class Runtime:
        def __init__(self) -> None:
            self.graph = None
            self.events = SimpleNamespace(publish=self._publish)

        async def _publish(self, _event) -> None:
            return None

        async def submit_graph(self, graph):
            self.graph = graph
            return SimpleNamespace(run_id="run-explicit")

        async def submit_run(self, *_args, **_kwargs):
            raise AssertionError("explicit fixed scenario must not ask the coordinator to create a text run")

    store = StoreProxy()
    runtime = Runtime()
    result = await RunService(runtime, store).create(
        RequestContext(Principal("user-a", "user-a"), "request-a"),
        CreateRunCommand(
            execution=ScenarioRunTarget(
                mode="scenario",
                scenario_id="explicit.echo",
                version=1,
                agent_id="main-coordinator",
                inputs={"query": "ready"},
            ),
            session_id="session-a",
            input="Run the echo quickstart",
        ),
    )

    assert result.run_id == "run-explicit"
    assert runtime.graph is not None
    assert runtime.graph.tasks[0].capability_input == {"query": "ready"}
    assert store.saved_states[0]["scenario_id"] == "explicit.echo"

    class AgentRuntime:
        def __init__(self) -> None:
            self.options = None
            self.events = SimpleNamespace(publish=self._publish)

        async def _publish(self, _event) -> None:
            return None

        async def submit_run(self, options, **_kwargs):
            self.options = options
            return SimpleNamespace(run_id="run-agent", status="queued")

        async def submit_graph(self, _graph):
            raise AssertionError("agent mode must not auto-select a Scenario")

    agent_runtime = AgentRuntime()
    await RunService(agent_runtime, store).create(
        RequestContext(Principal("user-a", "user-a"), "request-b"),
        CreateRunCommand(
            execution=AgentRunTarget(mode="agent", agent_id="main-coordinator"),
            session_id="session-b",
            input="echo should remain an Agent request",
        ),
    )
    assert agent_runtime.options.metadata["orchestration"]["mode"] == "agent"
    assert agent_runtime.options.metadata["routing_decision"]["scenario_id"] is None
