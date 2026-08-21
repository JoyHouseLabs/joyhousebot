from pathlib import Path

import pytest

from joyhousebot.domain.capabilities import CapabilityKind, CapabilityRef
from joyhousebot.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    RoutingDecision,
    ScenarioField,
    ScenarioVersion,
)
from joyhousebot.orchestration import ClarificationEngine, ScenarioRouter
from joyhousebot.runtime.coordination_preparation import _enforce_routed_scenario
from tests.support.postgres_store import PostgresTestStore


def _scenario() -> ScenarioVersion:
    return ScenarioVersion(
        scenario_id="tts",
        version=1,
        name="TTS",
        description="Speech generation",
        fields=(
            ScenarioField("text", "string", required=True),
            ScenarioField("voice", "string", required=True, enum=("default", "pro")),
            ScenarioField("format", "string", required=True, default="mp3"),
        ),
        nodes=(
            ClarificationNode("text", "question", "需要合成哪段文字？", ("text",)),
            ClarificationNode("voice", "question", "选择哪种声音？", ("voice",)),
            ClarificationNode("ready", "terminal", ""),
        ),
        edges=(
            ClarificationEdge("text", "voice", "present(text)"),
            ClarificationEdge("voice", "ready", "present(voice)"),
        ),
        allowed_capabilities=(CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.CAPABILITY, "test.plugin", "1.0.0", "sha256:test"),),
        planning_mode="fixed",
        execution_policy={"execution_class": "interactive"},
        routing_rules=({"contains_any": ["语音", "朗读"]},),
    )


def _waiting_run(store: PostgresTestStore) -> None:
    store.create_runtime_run(
        run_id="run-tts", user_id="user-a", session_id="session-a",
        agent_id="coordinator", kind="scenario", prompt="生成语音",
        options={}, initial_status="waiting_input",
    )


def test_router_matches_published_scenario_and_lists_missing_inputs(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "router.db")
    store.save_scenario_version(_scenario(), status="published")
    decision, scenario = ScenarioRouter(store).route("请帮我生成语音", supplied_inputs={})
    assert scenario is not None
    assert decision.scenario_id == "tts"
    assert decision.next_action == "clarify"
    assert decision.missing_inputs == ("text", "voice")


def test_router_supports_combined_rule_terms(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "router-combined.db")
    scenario = _scenario()
    scenario = ScenarioVersion.from_dict(
        {
            **scenario.to_dict(),
            "routing_rules": (
                {"contains_all": ["dinq", "人才"], "excludes_any": ["删除"]},
            ),
        }
    )
    store.save_scenario_version(scenario, status="published")
    decision, selected = ScenarioRouter(store).route("请在 Dinq 人才库中搜索 Python 工程师")
    assert selected is not None and decision.reason_code == "RULE_CONTAINS"
    _, selected = ScenarioRouter(store).route("请删除 Dinq 人才")
    assert selected is None


def test_router_prefers_highest_priority_matching_rule(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "router-priority.db")
    broad = ScenarioVersion.from_dict({**_scenario().to_dict(), "scenario_id": "broad", "routing_rules": ({"contains_any": ["搜索"], "priority": 10},)})
    preferred = ScenarioVersion.from_dict({**_scenario().to_dict(), "scenario_id": "preferred", "routing_rules": ({"contains_all": ["dinq", "搜索"], "priority": 100},)})
    store.save_scenario_version(broad, status="published")
    store.save_scenario_version(preferred, status="published")
    decision, selected = ScenarioRouter(store).route("在 Dinq 中搜索研究人员")
    assert selected is not None and selected.scenario_id == "preferred"
    assert decision.scenario_id == "preferred"


def test_router_explains_match_and_exclusion_deterministically(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "router-explain.db")
    scenario = ScenarioVersion.from_dict(
        {**_scenario().to_dict(), "routing_rules": ({"contains_all": ["dinq", "搜索"], "excludes_any": ["删除"], "priority": 50},)}
    )
    router = ScenarioRouter(store)
    matched = router.explain_match(scenario, "在 Dinq 中搜索研究人员")[0]
    assert matched["matched"] is True and matched["matched_any"] == []
    excluded = router.explain_match(scenario, "删除 Dinq 搜索结果")[0]
    assert excluded["matched"] is False and excluded["matched_excluded"] == ["删除"]


def test_coordinator_cannot_replace_deterministic_route_with_open_agent_plan() -> None:
    scenario = _scenario()
    plan = {
        "scenario_id": None,
        "scenario_inputs": {"voice": "model-extracted"},
        "clarification": None,
    }
    enforced = _enforce_routed_scenario(
        plan,
        scenarios=[scenario],
        routing_decision={"scenario_id": "tts"},
        supplied_inputs={"text": "来自 API 的文本"},
    )
    assert enforced["scenario_id"] == "tts"
    assert enforced["scenario_inputs"] == {
        "text": "来自 API 的文本",
        "voice": "model-extracted",
    }
    assert enforced["routing_enforced"] is True


def test_clarification_resumes_same_run_after_all_answers(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "clarification.db")
    scenario = _scenario()
    store.save_scenario_version(scenario, status="published")
    _waiting_run(store)
    engine = ClarificationEngine(store)
    step = engine.evaluate(scenario, {})
    decision = RoutingDecision(
        "tts", 1, 1.0, "interactive", 30, {}, step.missing_inputs,
        ({"capability_id": "speech.synthesize"},), "clarify", "EXPLICIT_SCENARIO",
    )
    store.save_run_scenario_state(
        run_id="run-tts", user_id="user-a", scenario_id="tts", scenario_version=1,
        status="waiting_input", collected_inputs=step.collected_inputs,
        missing_inputs=list(step.missing_inputs), current_node_id=step.node.node_id,
        routing_decision=decision.to_dict(),
    )
    first = engine.create_request(
        run_id="run-tts", user_id="user-a", scenario=scenario, step=step
    )
    second_step, second = engine.resolve(
        run_id="run-tts", user_id="user-a", input_request_id=first.input_request_id,
        answers={"text": "欢迎使用 joyhousebot"},
    )
    assert second_step.missing_inputs == ("voice",)
    assert second is not None and second.node_id == "voice"
    final_step, final_request = engine.resolve(
        run_id="run-tts", user_id="user-a", input_request_id=second.input_request_id,
        answers={"voice": "pro"},
    )
    assert final_step.complete and final_request is None
    run = store.get_runtime_run("run-tts", expected_user_id="user-a")
    assert run is not None and run.status == "queued"
    state = store.get_run_scenario_state("run-tts", expected_user_id="user-a")
    assert state.collected_inputs["format"] == "mp3"

    with pytest.raises(ValueError, match="not pending"):
        engine.resolve(
            run_id="run-tts", user_id="user-a", input_request_id=second.input_request_id,
            answers={"voice": "pro"},
        )


def test_input_request_is_user_isolated(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "isolated-input.db")
    store.save_scenario_version(_scenario(), status="published")
    _waiting_run(store)
    assert store.list_pending_input_requests("run-tts", expected_user_id="user-b") == []


def test_scenario_numeric_validation_honors_configured_bounds(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "bounds.db")
    scenario = ScenarioVersion(
        scenario_id="bounded",
        version=1,
        name="Bounded",
        description="bounded numeric input",
        fields=(
            ScenarioField(
                "limit",
                "integer",
                required=True,
                validation={"minimum": 1, "maximum": 20},
            ),
        ),
        nodes=(ClarificationNode("limit", "question", "How many?", ("limit",)),),
        edges=(),
        allowed_capabilities=(CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.CAPABILITY, "test.plugin", "1.0.0", "sha256:test"),),
    )
    engine = ClarificationEngine(store)
    with pytest.raises(ValueError, match="at least 1"):
        engine.validate_inputs(scenario, {"limit": 0})
    with pytest.raises(ValueError, match="at most 20"):
        engine.validate_inputs(scenario, {"limit": 21})
    assert engine.validate_inputs(scenario, {"limit": 20}) == {"limit": 20}


def test_clarification_follows_condition_edge_and_validates_multi_choice(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "condition-branch.db")
    scenario = ScenarioVersion(
        scenario_id="discovery-intake",
        version=1,
        name="Discovery intake",
        description="",
        fields=(
            ScenarioField(
                "goal", "string", required=True, input_mode="single_choice",
                options=(
                    {"value": "recruit", "label": "招聘"},
                    {"value": "research", "label": "研究"},
                ),
            ),
            ScenarioField(
                "sources", "array", required=True, input_mode="multi_choice",
                options=(
                    {"value": "github", "label": "GitHub"},
                    {"value": "scholar", "label": "Google Scholar"},
                    {"value": "linkedin", "label": "LinkedIn"},
                ),
                min_selections=1,
                max_selections=2,
            ),
            ScenarioField("research_area", "string", required=True),
        ),
        nodes=(
            # Deliberately unordered: persisted scenario nodes have no meaningful
            # row order, so the UI progress must follow graph depth instead.
            ClarificationNode("ask_area", "question", "研究方向？", ("research_area",)),
            ClarificationNode("ready", "terminal", ""),
            ClarificationNode("ask_goal", "question", "主要目标是什么？", ("goal",)),
            ClarificationNode("ask_sources", "question", "选择数据来源", ("sources",)),
        ),
        edges=(
            ClarificationEdge("ask_goal", "ask_sources", "goal == 'recruit'", priority=100),
            ClarificationEdge("ask_goal", "ask_area", "goal == 'research'", priority=100),
            ClarificationEdge("ask_sources", "ready", "present(sources)"),
            ClarificationEdge("ask_area", "ready", "present(research_area)"),
        ),
        allowed_capabilities=(CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.CAPABILITY, "test.plugin", "1.0.0", "sha256:test"),),
    )
    engine = ClarificationEngine(store)
    first = engine.evaluate(scenario, {})
    assert first.node and first.node.node_id == "ask_goal"
    assert first.progress == {"current": 1, "total": 3}
    second = engine.evaluate(scenario, {"goal": "recruit"}, from_node_id="ask_goal")
    assert second.node and second.node.node_id == "ask_sources"
    assert second.progress == {"current": 2, "total": 3}
    assert engine.validate_request_answers(
        [scenario.fields[1].to_dict()], {"sources": ["github", "scholar"]}
    ) == {"sources": ["github", "scholar"]}
    with pytest.raises(ValueError, match="at most 2"):
        engine.validate_request_answers(
            [scenario.fields[1].to_dict()],
            {"sources": ["github", "scholar", "linkedin"]},
        )


def test_dynamic_input_resolution_requeues_same_run_with_answers(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "dynamic-input.db")
    store.create_runtime_run(
        run_id="run-dynamic", user_id="user-a", session_id="session-a",
        agent_id="coordinator", kind="agent", prompt="Help me search", options={"metadata": {}},
        initial_status="waiting_input",
    )
    engine = ClarificationEngine(store)
    request = engine.create_dynamic_request(
        run_id="run-dynamic",
        user_id="user-a",
        question="Which outcome do you need?",
        fields=[{
            "name": "goal", "value_type": "string", "required": True,
            "description": "Goal", "input_mode": "single_choice",
            "options": [{"value": "recruit", "label": "Recruit"}],
            "enum": ["recruit"], "validation": {}, "sensitive": False,
        }],
    )
    assert request.source == "agent"
    assert store.resolve_dynamic_input_request(
        input_request_id=request.input_request_id,
        run_id="run-dynamic", user_id="user-a", answers={"goal": "recruit"},
    )
    run = store.get_runtime_run("run-dynamic", expected_user_id="user-a")
    assert run is not None and run.status == "queued"
    assert run.options["metadata"]["dynamic_inputs"] == {"goal": "recruit"}


def test_scenario_field_round_trips_plugin_owned_interaction_policy() -> None:
    field = ScenarioField(
        "topic", "string", required=True, input_mode="single_choice", allow_other=True,
        options=({"value": "rl", "label": "强化学习"},),
        suggestion_provider={"capability_id": "catalog.search.suggestions", "version": "1"},
        normalization={"strategy": "catalog.search_topic.v1"},
        constraint_policy={"default_strength": "required"},
        confirmation_policy="always", examples=("Deep RL",), group="search_goal", order=10,
    )
    scenario = ScenarioVersion(
        scenario_id="catalog.search", version=1, name="Search", description="",
        fields=(field,), nodes=(ClarificationNode("topic", "question", "方向？", ("topic",)),),
        edges=(), allowed_capabilities=(),
    )
    restored = ScenarioVersion.from_dict(scenario.to_dict()).fields[0]
    assert restored.suggestion_provider["capability_id"] == "catalog.search.suggestions"
    assert restored.constraint_policy["default_strength"] == "required"
    assert restored.examples == ("Deep RL",)
