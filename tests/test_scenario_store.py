from dataclasses import replace
from pathlib import Path

import pytest

from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    ScenarioField,
    ScenarioVersion,
)
from joyhousebot.orchestration.planner import ScenarioPlanner
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
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("skill.voice-style", "1.0.0", CapabilityKind.SKILL, "test.plugin", "1.0.0", "sha256:test"),
            name="Voice style",
            description="Voice policy",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="prompt-skill:voice-style",
        )
    )
    scenario = replace(
        _scenario(),
        allowed_capabilities=(
            CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            CapabilityRef("skill.voice-style", "1.0.0", CapabilityKind.SKILL, "test.plugin", "1.0.0", "sha256:test"),
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
