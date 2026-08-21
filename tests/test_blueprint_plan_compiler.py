from __future__ import annotations

from typing import Any

import pytest

from joyhousebot.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from joyhousebot.domain.collaboration_blueprints import normalize_collaboration_blueprint
from joyhousebot.orchestration.blueprint_compiler import (
    BlueprintRepairError,
    PlanBoundaryViolationError,
    apply_blueprint_boundary,
    compile_plan_against_blueprint,
    enforce_final_plan_boundary,
)

_BUDGET = {"max_tasks": 16, "max_parallel_tasks": 4, "max_handoffs": 16, "max_review_rounds": 2}


def _team() -> AgentTeamRevision:
    def member(member_id: str, *, delegate: bool = False) -> AgentTeamMember:
        return AgentTeamMember(
            member_id=member_id,
            agent_id="default",
            agent_revision_id="default:v1",
            role=member_id,
            responsibility="Fixture responsibility.",
            can_delegate=delegate,
            allowed_handoffs=("author_a", "author_b", "reviewer") if delegate else (),
        )

    return AgentTeamRevision(
        team_id="team.compiler",
        revision_id="team.compiler:v1",
        version=1,
        name="Compiler fixture",
        description="Fixture team for compiler contract tests.",
        coordinator_member_id="coordinator",
        members=(
            member("coordinator", delegate=True),
            member("author_a"),
            member("author_b"),
            member("reviewer"),
        ),
        budget_policy=dict(_BUDGET),
        status="published",
    )


def _blueprint() -> dict[str, Any]:
    return normalize_collaboration_blueprint(
        {
            "preset": "parallel_review_revise_synthesize",
            "role_bindings": {
                "producers": ["author_a", "author_b"],
                "reviewers": ["reviewer"],
            },
            "guardrails": {"max_parallel_tasks": 2},
        },
        member_ids={"coordinator", "author_a", "author_b", "reviewer"},
        coordinator_member_id="coordinator",
        budget_policy=_BUDGET,
    )


def _step(
    step_id: str,
    *,
    kind: str = "produce",
    member_id: str = "author_a",
    depends_on: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    step = {
        "id": step_id,
        "name": step_id,
        "objective": f"Objective for {step_id}",
        "phase": "execution",
        "kind": kind,
        "member_id": member_id,
        "can_run_in_parallel": True,
        "depends_on": depends_on or [],
        "acceptance_criteria": ["Result is attributable"],
        "review_of": [],
        "revision_of": None,
        "review_round": 0,
        "output_schema": None,
    }
    step.update(extra)
    return step


def _plan(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "intent": "compose",
        "summary": "Fixture plan",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 60,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": steps,
        "clarification": None,
    }


def _valid_steps() -> list[dict[str, Any]]:
    return [
        _step("author_a.produce", member_id="author_a"),
        _step("author_b.produce", member_id="author_b"),
        _step(
            "reviewer.review",
            kind="review",
            member_id="reviewer",
            depends_on=["author_a.produce", "author_b.produce"],
            review_of=["author_a.produce", "author_b.produce"],
        ),
        _step(
            "author_a.revise",
            kind="revise",
            member_id="author_a",
            depends_on=["reviewer.review"],
            revision_of="author_a.produce",
        ),
        _step(
            "author_b.revise",
            kind="revise",
            member_id="author_b",
            depends_on=["reviewer.review"],
            revision_of="author_b.produce",
        ),
        _step(
            "coordinator.synthesize",
            kind="synthesize",
            member_id="coordinator",
            depends_on=["author_a.revise", "author_b.revise"],
        ),
    ]


def test_valid_plan_passes_without_violations() -> None:
    assert compile_plan_against_blueprint(_plan(_valid_steps()), _blueprint(), team=_team()) == []


def test_step_outside_phase_participants_is_repairable() -> None:
    steps = _valid_steps()
    steps[0]["member_id"] = "reviewer"  # reviewer is not a producer
    violations = compile_plan_against_blueprint(_plan(steps), _blueprint(), team=_team())
    codes = [item.code for item in violations]
    assert "blueprint_step_member_not_in_phase" in codes
    assert all(item.repairable for item in violations)
    with pytest.raises(BlueprintRepairError):
        apply_blueprint_boundary(_plan(steps), _blueprint(), team=_team())


def test_uncovered_phase_is_repairable() -> None:
    steps = _valid_steps()
    steps = [item for item in steps if item["kind"] != "revise"]  # revise phase uncovered
    for step in steps:
        step["depends_on"] = [dep for dep in step["depends_on"] if "revise" not in dep]
    steps[-1]["depends_on"] = ["reviewer.review"]
    violations = compile_plan_against_blueprint(_plan(steps), _blueprint(), team=_team())
    assert "blueprint_missing_phase" in [item.code for item in violations]
    assert all(item.repairable for item in violations)


def test_phase_order_violation_is_repairable() -> None:
    steps = _valid_steps()
    # Synthesize no longer depends on the revise wave.
    steps[-1]["depends_on"] = ["reviewer.review"]
    violations = compile_plan_against_blueprint(_plan(steps), _blueprint(), team=_team())
    assert "blueprint_phase_order_violation" in [item.code for item in violations]


def test_self_review_is_repairable() -> None:
    steps = _valid_steps()
    # author_a reviews their own produce step: reviewer becomes author_a.
    steps[2]["member_id"] = "author_a"
    violations = compile_plan_against_blueprint(_plan(steps), _blueprint(), team=_team())
    codes = [item.code for item in violations]
    assert "blueprint_review_independence" in codes or "blueprint_step_member_not_in_phase" in codes
    # Defense-in-depth: a blueprint that lets one member both produce and
    # review is rejected by the domain normalizer, so exercise the plan-level
    # fence directly against the (invalid but frozen) shape.
    blueprint = {
        "schema_version": 1,
        "preset": "research_challenge_decide",
        "phases": [
            {"id": "research", "kind": "produce", "participants": ["author_a", "author_b"], "mode": "parallel", "depends_on": []},
            {"id": "challenge", "kind": "review", "participants": ["author_a"], "mode": "sequential", "depends_on": ["research"]},
            {"id": "decide", "kind": "synthesize", "participants": ["coordinator"], "mode": "sequential", "depends_on": ["challenge"]},
        ],
        "guardrails": {"max_parallel_tasks": 2, "require_review": True},
    }
    plan = _plan(
        [
            _step("a.produce", member_id="author_a"),
            _step("b.produce", member_id="author_b"),
            _step(
                "a.challenge",
                kind="review",
                member_id="author_a",
                depends_on=["a.produce", "b.produce"],
                review_of=["a.produce", "b.produce"],
            ),
            _step("coordinator.decide", kind="synthesize", member_id="coordinator", depends_on=["a.challenge"]),
        ]
    )
    violations = compile_plan_against_blueprint(plan, blueprint, team=_team())
    assert "blueprint_review_independence" in [item.code for item in violations]


def test_parallel_width_above_guardrail_is_repairable() -> None:
    blueprint = dict(_blueprint())
    blueprint["guardrails"] = {**blueprint["guardrails"], "max_parallel_tasks": 1}
    violations = compile_plan_against_blueprint(_plan(_valid_steps()), blueprint, team=_team())
    assert "blueprint_parallel_budget_exceeded" in [item.code for item in violations]


def test_boundary_fences_fail_closed() -> None:
    team = _team()
    ghost = _step("ghost.produce", member_id="ghost")
    with pytest.raises(PlanBoundaryViolationError, match="blueprint_boundary_violation"):
        apply_blueprint_boundary(_plan([ghost]), _blueprint(), team=team)

    rogue_synthesis = _step("author.synthesize", kind="synthesize", member_id="author_a")
    with pytest.raises(PlanBoundaryViolationError):
        apply_blueprint_boundary(_plan([rogue_synthesis]), _blueprint(), team=team)


def test_final_gate_fails_closed_even_for_repairable_deviations() -> None:
    steps = _valid_steps()
    steps[0]["member_id"] = "reviewer"
    with pytest.raises(PlanBoundaryViolationError):
        enforce_final_plan_boundary(_plan(steps), _blueprint(), team=_team())
    enforce_final_plan_boundary(_plan(_valid_steps()), _blueprint(), team=_team())


def test_repair_feedback_carries_structured_violations() -> None:
    steps = _valid_steps()
    steps[0]["member_id"] = "reviewer"
    with pytest.raises(BlueprintRepairError) as excinfo:
        apply_blueprint_boundary(_plan(steps), _blueprint(), team=_team())
    assert excinfo.value.violations[0].step_id == "author_a.produce"
    assert str(excinfo.value).startswith("blueprint_violations:")
