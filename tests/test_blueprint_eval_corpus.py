from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.domain.collaboration_blueprints import normalize_collaboration_blueprint
from porthouse.orchestration.blueprint_compiler import compile_plan_against_blueprint

_CORPUS = json.loads(
    (Path(__file__).parent / "evals" / "blueprint_plan_eval_cases.json").read_text()
)


def _corpus_team() -> AgentTeamRevision:
    members = tuple(
        AgentTeamMember(
            member_id=member_id,
            agent_id="default",
            agent_revision_id="default:v1",
            role=member_id,
            responsibility="Corpus responsibility.",
            can_delegate=member_id == "coordinator",
            allowed_handoffs=("author_a", "author_b", "reviewer")
            if member_id == "coordinator"
            else (),
        )
        for member_id in _CORPUS["member_ids"]
    )
    return AgentTeamRevision(
        team_id="team.corpus",
        revision_id="team.corpus:v1",
        version=1,
        name="Corpus team",
        description="Offline evaluation fixture.",
        coordinator_member_id=str(_CORPUS["coordinator_member_id"]),
        members=members,
        budget_policy=dict(_CORPUS["budget_policy"]),
        status="published",
    )


def _plan(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "intent": "corpus",
        "summary": "Corpus plan",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 60,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": [
            {
                "name": str(step["id"]),
                "objective": f"Objective {step['id']}",
                **step,
                "can_run_in_parallel": True,
                "acceptance_criteria": ["Result is attributable"],
                "review_of": step.get("review_of", []),
                "revision_of": step.get("revision_of"),
                "review_round": 0,
                "output_schema": None,
            }
            for step in steps
        ],
        "clarification": None,
    }


@pytest.mark.parametrize(
    "case", _CORPUS["cases"], ids=[item["id"] for item in _CORPUS["cases"]]
)
def test_blueprint_eval_corpus(case: dict[str, Any]) -> None:
    team = _corpus_team()
    if "phases" in case["blueprint"]:
        blueprint = {
            "schema_version": 1,
            "preset": "research_challenge_decide",
            **case["blueprint"],
        }
    else:
        blueprint = normalize_collaboration_blueprint(
            case["blueprint"],
            member_ids=set(_CORPUS["member_ids"]),
            coordinator_member_id=str(_CORPUS["coordinator_member_id"]),
            budget_policy=dict(_CORPUS["budget_policy"]),
        )
    violations = compile_plan_against_blueprint(
        _plan(case["plan_steps"]), blueprint, team=team
    )
    codes = {item.code for item in violations}
    expectation = str(case["expectation"])
    if expectation == "pass":
        assert not violations, sorted(codes)
        return
    _, _, expected_code = expectation.partition("violation:")
    assert expected_code in codes, f"expected {expected_code}, got {sorted(codes)}"
