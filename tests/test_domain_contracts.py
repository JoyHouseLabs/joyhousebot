import pytest

from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    InvocationStatus,
)
from joyhousebot.domain.planning import PlanSpec, PlanTask
from joyhousebot.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    ScenarioField,
    ScenarioVersion,
)


def test_capability_definition_and_result_are_structured() -> None:
    definition = CapabilityDefinition(
        ref=CapabilityRef("web.search", "1.0.0", CapabilityKind.TOOL),
        name="Web search",
        description="Search public pages",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_schema={"type": "object"},
        adapter="builtin.web_search",
    )
    result = CapabilityResult.succeeded("inv_1", summary="done", data={"items": []})
    assert definition.to_dict()["ref"]["kind"] == "tool"
    assert result.to_dict()["status"] == "succeeded"
    assert result.ok is True


def test_failed_capability_result_requires_error() -> None:
    with pytest.raises(ValueError, match="requires an error"):
        CapabilityResult("inv_1", InvocationStatus.FAILED, "failed")


def test_plan_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        PlanSpec(
            tasks=(
                PlanTask("a", "agent.a", "1", {}, dependencies=("b",)),
                PlanTask("b", "agent.b", "1", {}, dependencies=("a",)),
            )
        )


def test_scenario_rejects_invalid_clarification_graph() -> None:
    with pytest.raises(ValueError, match="cycle"):
        ScenarioVersion(
            scenario_id="tts",
            version=1,
            name="TTS",
            description="",
            fields=(ScenarioField("voice", "string", required=True),),
            nodes=(
                ClarificationNode("voice", "question", "Which voice?", ("voice",)),
                ClarificationNode("confirm", "confirmation", "Confirm?"),
            ),
            edges=(
                ClarificationEdge("voice", "confirm"),
                ClarificationEdge("confirm", "voice"),
            ),
            allowed_capabilities=("speech.synthesize",),
        )
