from __future__ import annotations

from typing import Any

import pytest

from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.domain.collaboration_blueprints import (
    BLUEPRINT_SCHEMA_VERSION,
    PRESET_REGISTRY,
    default_blueprint,
    derive_preset_phases,
    normalize_collaboration_blueprint,
    preset_summaries,
    resolve_effective_blueprint,
)

_MEMBERS = ("coordinator", "author_a", "author_b", "reviewer")
_MEMBER_IDS = set(_MEMBERS)
_BUDGET = {"max_tasks": 16, "max_parallel_tasks": 4, "max_handoffs": 16, "max_review_rounds": 2}


def _blueprint(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return normalize_collaboration_blueprint(
        value,
        member_ids=_MEMBER_IDS,
        coordinator_member_id="coordinator",
        budget_policy=_BUDGET,
    )


def _full_review_blueprint() -> dict[str, Any]:
    return {
        "schema_version": BLUEPRINT_SCHEMA_VERSION,
        "preset": "parallel_review_revise_synthesize",
        "phases": [
            {"id": "produce", "kind": "produce", "participants": ["author_a", "author_b"], "mode": "parallel", "depends_on": []},
            {"id": "review", "kind": "review", "participants": ["reviewer"], "mode": "sequential", "depends_on": ["produce"]},
            {"id": "revise", "kind": "revise", "participants": ["author_a", "author_b"], "mode": "parallel", "depends_on": ["review"]},
            {"id": "synthesize", "kind": "synthesize", "participants": ["coordinator"], "mode": "sequential", "depends_on": ["revise"]},
        ],
        "guardrails": {"max_parallel_tasks": 2, "require_review": True, "require_plan_confirmation": True},
    }


def _team(blueprint: dict[str, Any] | None = None) -> AgentTeamRevision:
    members = tuple(
        AgentTeamMember(
            member_id=member_id,
            agent_id="default",
            agent_revision_id="default:v1",
            role=member_id,
            responsibility="Shared fixture responsibility.",
            can_delegate=member_id == "coordinator",
            allowed_handoffs=("author_a", "author_b", "reviewer") if member_id == "coordinator" else (),
        )
        for member_id in _MEMBERS
    )
    return AgentTeamRevision(
        team_id="team.compose",
        revision_id="team.compose:v1",
        version=1,
        name="Composer fixture",
        description="Fixture team for blueprint contract tests.",
        coordinator_member_id="coordinator",
        members=members,
        budget_policy=dict(_BUDGET),
        collaboration_blueprint=blueprint,
        status="draft",
    )


def test_preset_registry_covers_the_five_documented_presets() -> None:
    assert set(PRESET_REGISTRY) == {
        "parallel_synthesize",
        "parallel_review_revise_synthesize",
        "sequential_handoff",
        "research_challenge_decide",
        "monitor_diagnose_execute_verify",
    }
    summaries = preset_summaries()
    assert all(item["label"] and item["guidance"] for item in summaries)
    assert all(item["phase_template"] and item["bindings"] for item in summaries)


def test_derive_preset_phases_is_deterministic_and_canonical() -> None:
    for preset in PRESET_REGISTRY:
        first = derive_preset_phases(
            preset, role_bindings=None, member_ids=list(_MEMBERS), coordinator_member_id="coordinator"
        )
        second = derive_preset_phases(
            preset, role_bindings=None, member_ids=list(_MEMBERS), coordinator_member_id="coordinator"
        )
        assert first == second
        normalized = _blueprint(
            {"preset": preset, "phases": first, "guardrails": {}}
        )
        assert normalized is not None and normalized["phases"] == first
        assert normalized["phases"][-1]["kind"] == "synthesize"


def test_derived_input_form_matches_full_canonical_form() -> None:
    derived = _blueprint(
        {
            "preset": "parallel_review_revise_synthesize",
            "role_bindings": {"producers": ["author_a", "author_b"], "reviewers": ["reviewer"]},
            "guardrails": {
                "max_parallel_tasks": 2,
                "require_review": True,
                "require_plan_confirmation": True,
            },
        }
    )
    assert derived == _blueprint(_full_review_blueprint())


def test_none_blueprint_stays_none_for_legacy_revisions() -> None:
    assert _blueprint(None) is None
    team = _team()
    assert team.collaboration_blueprint is None
    assert team.effective_blueprint["origin"] == "implicit_default"
    assert team.effective_blueprint["preset"] == "parallel_synthesize"


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"schema_version": 2}, id="unsupported_schema_version"),
        pytest.param({"preset": "nope"}, id="unknown_preset"),
        pytest.param({"extra": 1}, id="unknown_fields"),
    ],
)
def test_top_level_contracts(mutation: dict[str, Any]) -> None:
    value = _full_review_blueprint() | mutation
    with pytest.raises(ValueError, match=r"^blueprint_"):
        _blueprint(value)


def test_phase_contracts() -> None:
    base = _full_review_blueprint()

    def with_phases(phases: list[dict[str, Any]]) -> dict[str, Any]:
        return base | {"phases": phases}

    valid = _blueprint(base)
    assert valid is not None and len(valid["phases"]) == 4

    unknown_participant = _full_review_blueprint()
    unknown_participant["phases"][0]["participants"] = ["author_a", "ghost"]
    with pytest.raises(ValueError, match="blueprint_unknown_participant"):
        _blueprint(unknown_participant)

    coordinator_only = _full_review_blueprint()
    coordinator_only["phases"][3]["participants"] = ["author_a"]
    with pytest.raises(ValueError, match="blueprint_coordinator_only_phase"):
        _blueprint(coordinator_only)

    missing_synthesize = _full_review_blueprint()
    missing_synthesize["phases"] = missing_synthesize["phases"][:3]
    with pytest.raises(ValueError, match="blueprint_missing_synthesize"):
        _blueprint(missing_synthesize)

    not_final = _full_review_blueprint()
    synthesize = not_final["phases"][3] | {"depends_on": []}
    not_final["phases"] = [synthesize, *not_final["phases"][:3]]
    with pytest.raises(ValueError, match="blueprint_synthesize_not_final"):
        _blueprint(not_final)

    forward_dep = _full_review_blueprint()
    forward_dep["phases"][0]["depends_on"] = ["review"]
    with pytest.raises(ValueError, match="blueprint_invalid_dependency"):
        _blueprint(forward_dep)

    bad_mode = _full_review_blueprint()
    bad_mode["phases"][0]["mode"] = "async"
    with pytest.raises(ValueError, match="blueprint_invalid_phase_mode"):
        _blueprint(bad_mode)

    with pytest.raises(ValueError, match="blueprint_review_independence"):
        _blueprint(with_phases([
            {"id": "produce", "kind": "produce", "participants": ["author_a", "reviewer"], "mode": "parallel", "depends_on": []},
            base["phases"][1],
            base["phases"][2],
            base["phases"][3],
        ]))


def test_guardrail_contracts() -> None:
    base = _full_review_blueprint()

    exceed = base | {"guardrails": {"max_parallel_tasks": 8}}
    with pytest.raises(ValueError, match="blueprint_parallel_exceeds_budget"):
        _blueprint(exceed)

    bad_type = base | {"guardrails": {"require_review": "yes"}}
    with pytest.raises(ValueError, match="blueprint_invalid_guardrails"):
        _blueprint(bad_type)

    unknown = base | {"guardrails": {"curfew": True}}
    with pytest.raises(ValueError, match="blueprint_invalid_guardrails"):
        _blueprint(unknown)

    require_review_missing = base | {
        "phases": [base["phases"][0], base["phases"][3] | {"depends_on": ["produce"]}],
        "preset": "parallel_synthesize",
        "guardrails": {"require_review": True},
    }
    with pytest.raises(ValueError, match="blueprint_review_required_missing"):
        _blueprint(require_review_missing)


def test_preset_kind_coverage_is_enforced() -> None:
    value = _full_review_blueprint()
    value["preset"] = "research_challenge_decide"
    value["phases"] = [value["phases"][0], value["phases"][3] | {"depends_on": ["produce"]}]
    with pytest.raises(ValueError, match="blueprint_preset_kind_missing"):
        _blueprint(value)


def test_revision_round_trip_preserves_blueprint_and_freezes_defaults() -> None:
    team = _team(_full_review_blueprint())
    restored = AgentTeamRevision.from_dict(team.to_dict())
    assert restored.collaboration_blueprint == team.collaboration_blueprint
    assert restored.effective_blueprint["origin"] == "explicit"
    assert restored.effective_blueprint["guardrails"]["require_plan_confirmation"] is True

    legacy = AgentTeamRevision.from_dict(
        {key: value for key, value in team.to_dict().items() if key != "collaboration_blueprint"}
    )
    assert legacy.collaboration_blueprint is None
    implicit = default_blueprint(
        member_ids=list(_MEMBERS),
        coordinator_member_id="coordinator",
        budget_policy=_BUDGET,
    )
    assert legacy.effective_blueprint == resolve_effective_blueprint(
        collaboration_blueprint=None,
        member_ids=list(_MEMBERS),
        coordinator_member_id="coordinator",
        budget_policy=_BUDGET,
    )
    assert implicit["phases"][0]["participants"] == ["author_a", "author_b", "reviewer"]
    assert implicit["guardrails"]["max_parallel_tasks"] == 4
