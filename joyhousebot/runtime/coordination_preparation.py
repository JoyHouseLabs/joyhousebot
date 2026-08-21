"""Typed preparation state and catalog loading for coordinator execution."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from joyhousebot.domain.capabilities import capability_id, capability_kind
from joyhousebot.domain.collaboration_blueprints import frozen_enforced_blueprint
from joyhousebot.orchestration.blueprint_compiler import apply_blueprint_boundary
from joyhousebot.orchestration.coordinator_agent import normalize_coordinator_plan
from joyhousebot.runtime.models import AgentOptions, AgentUsage
from joyhousebot.runtime.team_coordination import resolve_team_coordination_scope
from joyhousebot.storage.contracts import RuntimeStores


@dataclass(slots=True)
class CoordinationPreparation:
    """Mutable state passed through the explicit coordination preparation pipeline."""

    record: Any
    options: AgentOptions
    capability_catalog: list[dict[str, Any]]
    snapshot: Any
    bound_skills: dict[str, dict[str, Any]]
    always_skill_names: list[str]
    team_scope: Any
    scenario_state: Any
    prompt: str
    tools: list[str]
    metadata: dict[str, Any]
    usage: AgentUsage = field(default_factory=AgentUsage)
    dynamic_inputs: dict[str, Any] = field(default_factory=dict)
    scenarios: list[Any] = field(default_factory=list)
    planning_catalog: list[dict[str, Any]] = field(default_factory=list)
    frozen_blueprint: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] | None = None
    selected_scenario: Any = None
    graph: Any = None


def binding_ref(binding: dict[str, Any]) -> dict[str, str]:
    return {
        "skill_id": str(binding["skill_id"]),
        "version": str(binding["skill_version"]),
        "content_sha256": str(binding.get("content_sha256") or ""),
    }


def _requested_skills(options: AgentOptions) -> tuple[list[str], list[str]]:
    requested_ids = [
        str(item.get("skill_id") or item.get("capability_id") or "").strip()
        for item in (options.metadata.get("skill_refs") or ())
        if isinstance(item, dict)
        and str(item.get("skill_id") or item.get("capability_id") or "").strip()
    ]
    requested_names = [
        str(item).removeprefix("skill.")
        for item in (options.metadata.get("skill_names") or ())
        if str(item).strip()
    ]
    return requested_ids, requested_names


def _validate_requested_authority(
    options: AgentOptions,
    *,
    bound_skills: dict[str, dict[str, Any]],
    effective_capabilities: set[str],
) -> list[str]:
    requested_ids, requested_names = _requested_skills(options)
    unauthorized_capabilities = [
        item
        for item in dict.fromkeys(options.allowed_tools)
        if item not in effective_capabilities
    ]
    unauthorized_skills = [
        item
        for item in dict.fromkeys(
            [*requested_names, *(item.removeprefix("skill.") for item in requested_ids)]
        )
        if item not in bound_skills
    ]
    if unauthorized_capabilities:
        raise ValueError(
            "Agent revision does not authorize requested capabilities: "
            + ", ".join(unauthorized_capabilities)
        )
    if unauthorized_skills:
        raise ValueError(
            "Agent revision does not bind requested Skills: "
            + ", ".join(f"skill.{item}" for item in unauthorized_skills)
        )
    for requested_ref in options.metadata.get("skill_refs") or ():
        if not isinstance(requested_ref, dict):
            continue
        requested_id = str(
            requested_ref.get("skill_id") or requested_ref.get("capability_id") or ""
        )
        binding = bound_skills.get(requested_id.removeprefix("skill."))
        if binding is None:
            continue
        requested_version = str(requested_ref.get("version") or "")
        requested_digest = str(requested_ref.get("content_sha256") or "")
        if requested_version and requested_version != str(binding["skill_version"]):
            raise ValueError(
                "requested Skill version does not match the Agent binding: "
                f"{requested_id}@{requested_version}"
            )
        if requested_digest and requested_digest != str(binding.get("content_sha256") or ""):
            raise ValueError(
                "requested Skill digest does not match the Agent binding: "
                f"{requested_id}@{requested_version or binding['skill_version']}"
            )
    return requested_names


async def initialize_coordination_preparation(
    stores: RuntimeStores,
    *,
    record: Any,
    options: AgentOptions,
) -> CoordinationPreparation:
    capability_catalog = await asyncio.to_thread(
        stores.catalog.list_capability_definitions
    )
    snapshot = await asyncio.to_thread(
        stores.catalog.get_run_execution_snapshot, record.run_id
    )
    skill_bindings = list(snapshot.skill_bindings if snapshot is not None else ())
    bound_skills = {
        str(item.get("skill_id") or "").removeprefix("skill."): dict(item)
        for item in skill_bindings
        if str(item.get("skill_id") or "").startswith("skill.")
    }
    always_skill_names = [
        name
        for name, binding in bound_skills.items()
        if binding.get("activation_mode") == "always"
    ]
    team_scope = await resolve_team_coordination_scope(
        stores.catalog,
        record=record,
        metadata=options.metadata,
        capability_catalog=capability_catalog,
        snapshot=snapshot,
    )
    effective_capabilities = team_scope.effective_capabilities
    requested_skill_names = _validate_requested_authority(
        options,
        bound_skills=bound_skills,
        effective_capabilities=effective_capabilities,
    )
    effective_tools = [
        capability_id(item)
        for item in capability_catalog
        if capability_id(item) in effective_capabilities
        and capability_kind(item) in {"capability", "connector"}
    ]
    requested_tools = list(options.allowed_tools)
    tools = (
        requested_tools
        if options.metadata.get("caller_tool_allowlist_enforced")
        else requested_tools or effective_tools
    )
    initial_skill_names = list(
        dict.fromkeys([*always_skill_names, *requested_skill_names])
    )
    metadata = {
        **dict(options.metadata),
        "capability_allowlist_enforced": True,
        "effective_capabilities": sorted(effective_capabilities),
        "skill_names": initial_skill_names,
        "skill_refs": [
            binding_ref(bound_skills[name])
            for name in initial_skill_names
            if name in bound_skills
        ],
        "skill_binding_enforced": True,
    }
    scenario_state = await asyncio.to_thread(
        stores.scenarios.get_run_scenario_state,
        record.run_id,
        expected_user_id=record.user_id,
    )
    return CoordinationPreparation(
        record=record,
        options=options,
        capability_catalog=capability_catalog,
        snapshot=snapshot,
        bound_skills=bound_skills,
        always_skill_names=always_skill_names,
        team_scope=team_scope,
        scenario_state=scenario_state,
        prompt=options.prompt,
        tools=tools,
        metadata=metadata,
        dynamic_inputs=dict(options.metadata.get("dynamic_inputs") or {}),
    )


def _scenario_is_available(state: CoordinationPreparation, scenario: Any) -> bool:
    effective_capabilities = state.team_scope.effective_capabilities
    if any(
        reference.capability_id not in effective_capabilities
        for reference in scenario.allowed_capabilities
    ):
        return False
    for reference in scenario.required_skills:
        binding = state.bound_skills.get(reference.skill_id.removeprefix("skill."))
        if binding is None:
            return False
        if str(binding.get("skill_version") or "") != reference.version:
            return False
        if str(binding.get("content_sha256") or "") != reference.content_sha256:
            return False
    return True


async def load_coordination_catalog(
    stores: RuntimeStores, state: CoordinationPreparation
) -> None:
    options = state.options
    scenarios = (
        []
        if dict(options.metadata.get("orchestration") or {}).get("mode") != "scenario"
        else await asyncio.to_thread(
            stores.scenarios.list_scenario_versions, published_only=True
        )
    )
    state.scenarios = [item for item in scenarios if _scenario_is_available(state, item)]
    member_capabilities = state.team_scope.member_capabilities
    capabilities = [
        {
            **item,
            "team_member_ids": [
                member_id
                for member_id, allowed in member_capabilities.items()
                if capability_id(item) in allowed
            ],
        }
        for item in state.capability_catalog
        if capability_id(item) in state.team_scope.effective_capabilities
    ]
    coordinator_skills: list[dict[str, Any]] = []
    for name, binding in state.bound_skills.items():
        if binding.get("activation_mode") not in {"always", "coordinator_selected"}:
            continue
        skill = await asyncio.to_thread(
            stores.catalog.get_published_skill,
            str(binding["skill_id"]),
            str(binding["skill_version"]),
        )
        if skill is None or str(skill.get("content_sha256") or "") != str(
            binding.get("content_sha256") or ""
        ):
            raise ValueError(
                "Agent Skill binding is no longer available with its exact digest: "
                f"{binding['skill_id']}@{binding['skill_version']}"
            )
        coordinator_skills.append(
            {
                "skill_id": str(binding["skill_id"]),
                "version": str(binding["skill_version"]),
                "content_sha256": str(binding.get("content_sha256") or ""),
                "name": str(skill.get("name") or name),
                "description": str(skill.get("description") or ""),
                "activation_mode": str(binding.get("activation_mode") or ""),
            }
        )
    state.capability_catalog = capabilities
    state.planning_catalog = [*capabilities, *coordinator_skills]
    team = state.team_scope.team
    state.frozen_blueprint = (
        frozen_enforced_blueprint(options.metadata.get("team_collaboration_blueprint"))
        if team is not None
        else {}
    )


def build_planning_prompt(state: CoordinationPreparation) -> str:
    prompt = state.options.prompt
    if state.dynamic_inputs:
        prompt = (
            f"{prompt}\n\n## Answers already supplied by the user\n"
            f"{json.dumps(state.dynamic_inputs, ensure_ascii=False)}"
        )
    regeneration = dict(state.options.metadata.get("plan_regeneration") or {})
    if regeneration.get("feedback"):
        prompt = (
            f"{prompt}\n\n## Prior plan user feedback\n"
            "The user rejected the previous plan. Address this feedback in the "
            f"replacement plan:\n{str(regeneration['feedback'])[:4000]}"
        )
    return prompt


def normalize_plan(state: CoordinationPreparation, raw_plan: dict[str, Any]) -> dict[str, Any]:
    scenario_values = [item.to_dict() for item in state.scenarios]
    team = state.team_scope.team
    normalized = normalize_coordinator_plan(
        raw_plan,
        state.planning_catalog,
        scenario_values,
        team=team,
    )
    normalized = _enforce_routed_scenario(
        normalized,
        scenarios=state.scenarios,
        routing_decision=dict(state.options.metadata.get("routing_decision") or {}),
        supplied_inputs=dict(state.options.metadata.get("scenario_inputs") or {}),
    )
    if team is not None and state.frozen_blueprint:
        apply_blueprint_boundary(normalized, state.frozen_blueprint, team=team)
    return normalized


def _enforce_routed_scenario(
    plan: dict[str, Any],
    *,
    scenarios: list[Any],
    routing_decision: dict[str, Any],
    supplied_inputs: dict[str, Any],
) -> dict[str, Any]:
    """Pin a coordinator plan to a deterministic scenario route."""
    routed_id = str(routing_decision.get("scenario_id") or "").strip()
    if not routed_id:
        return plan
    selected = next(
        (item for item in scenarios if str(getattr(item, "scenario_id", "")) == routed_id),
        None,
    )
    if selected is None:
        return plan
    known_fields = {str(item.name) for item in selected.fields}
    extracted = {
        str(key): value
        for key, value in dict(plan.get("scenario_inputs") or {}).items()
        if str(key) in known_fields
    }
    explicit = {
        key: value for key, value in supplied_inputs.items() if key in known_fields
    }
    enforced = dict(plan)
    enforced["scenario_id"] = routed_id
    enforced["scenario_inputs"] = {**extracted, **explicit}
    enforced["routing_enforced"] = True
    return enforced


def apply_plan_selection(state: CoordinationPreparation, plan: dict[str, Any]) -> list[str]:
    selected_tool_ids = [
        str(item.get("capability_id"))
        for item in plan["selected_capabilities"]
        if isinstance(item, dict)
    ]
    state.tools = selected_tool_ids or state.tools
    selected_skill_names = list(
        dict.fromkeys([*state.always_skill_names, *plan["selected_skills"]])
    )
    state.metadata["skill_names"] = selected_skill_names
    state.metadata["skill_refs"] = [
        binding_ref(state.bound_skills[name])
        for name in selected_skill_names
        if name in state.bound_skills
    ]
    state.metadata["coordinator_plan"] = plan
    state.prompt = (
        f"{state.options.prompt}\n\n## Coordinator plan\n"
        f"{json.dumps(plan, ensure_ascii=False)}\n\n"
        "Execute this plan. Independent substantial steps may be delegated to durable "
        "child Agents; keep the final response grounded in their results."
    )
    state.plan = plan
    state.selected_scenario = next(
        (item for item in state.scenarios if item.scenario_id == plan.get("scenario_id")),
        None,
    )
    return selected_tool_ids
