"""Compile published fixed scenarios into native distributed task graphs."""

from __future__ import annotations

import re
from typing import Any

from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.graphs import GraphTaskSpec, TaskGraphSpec
from porthouse.domain.scenarios import ScenarioVersion
from porthouse.orchestration.task_graph import render_value

_TEAM_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues", "required_changes", "evidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise", "reject"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "required_changes": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}

_TEAM_CHECKPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "summary", "unresolved_issues"],
    "properties": {
        "decision": {"type": "string", "enum": ["continue", "revise", "finish"]},
        "summary": {"type": "string"},
        "unresolved_issues": {"type": "array", "items": {"type": "string"}},
    },
}


class ScenarioPlanner:
    def __init__(self, store: Any) -> None:
        self.store = store

    def build_graph(
        self,
        scenario: ScenarioVersion,
        *,
        goal: str,
        inputs: dict[str, Any],
        user_id: str,
        session_id: str,
        agent_id: str,
        agent_revision_id: str | None = None,
        idempotency_key: str | None,
        request_id: str,
        tracker_id: str | None = None,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> TaskGraphSpec | None:
        templates = list(scenario.execution_policy.get("tasks") or [])
        if scenario.planning_mode != "fixed" or not templates:
            return None
        allowed = {item.identity: item for item in scenario.allowed_capabilities}
        definitions = {
            ref.identity: self.store.get_capability_definition(
                ref.capability_id, ref.version
            )
            for ref in scenario.allowed_capabilities
        }
        definitions = {identity: item for identity, item in definitions.items() if item is not None}
        unknown_allowed = set(allowed) - set(definitions)
        if unknown_allowed:
            raise ValueError(
                f"scenario references unpublished capabilities: {sorted(unknown_allowed)}"
            )
        for identity, definition in definitions.items():
            published_ref = CapabilityRef.from_dict(dict(definition.get("ref") or {}))
            if published_ref.identity != identity:
                raise ValueError(
                    "scenario capability provenance does not match its published definition: "
                    f"{published_ref.capability_id}@{published_ref.version}"
                )
        tool_capabilities = {
            ref.capability_id
            for identity, ref in allowed.items()
            if definitions[identity].get("ref", {}).get("kind") in {"tool", "connector"}
        }
        skills = {
            ref.skill_id.removeprefix("skill.") for ref in scenario.required_skills
        }
        # Render every declared field so a template never sends literal
        # ``${field}`` text to a tool.  Unset optional values are removed from
        # the resulting tool-object below: JSON Schema uses an absent optional
        # property to mean "not supplied"; injecting JSON null is a different
        # value and commonly violates scalar schemas.
        variables = {
            **{field.name: inputs.get(field.name) for field in scenario.fields},
            **inputs,
            "goal": goal,
            "scenario_id": scenario.scenario_id,
        }
        tasks: list[GraphTaskSpec] = []
        for position, raw in enumerate(templates):
            task_agent_id = str(raw.get("agent_id") or agent_id)
            if agent_revision_id and task_agent_id != agent_id:
                raise ValueError(
                    "revision-pinned Scenario tasks must use the Scenario Agent"
                )
            capability = (
                CapabilityRef.from_dict(dict(raw["capability"]))
                if raw.get("capability")
                else None
            )
            if capability and capability.identity not in allowed:
                raise ValueError(
                    "capability is not allowed by scenario: "
                    f"{capability.capability_id}@{capability.version}"
                )
            if capability:
                kind = definitions[capability.identity].get("ref", {}).get("kind")
                if kind not in {"tool", "connector"}:
                    raise ValueError(
                        "fixed task capability must be an executable tool or connector: "
                        f"{capability.capability_id}"
                    )
            rendered_input = render_value(raw.get("input") or {}, variables)
            tasks.append(
                GraphTaskSpec(
                    id=str(raw.get("id") or f"step_{position + 1}"),
                    name=str(raw.get("name") or raw.get("id") or f"Step {position + 1}"),
                    prompt=str(render_value(raw.get("prompt") or goal, variables)),
                    agent_id=task_agent_id,
                    dependencies=[str(item) for item in raw.get("dependencies") or []],
                    timeout_seconds=float(raw.get("timeout_seconds") or 300),
                    max_attempts=int(raw.get("max_attempts") or 1),
                    max_input_tokens=(
                        int(raw["max_input_tokens"])
                        if raw.get("max_input_tokens") is not None
                        else None
                    ),
                    max_output_tokens=(
                        int(raw["max_output_tokens"])
                        if raw.get("max_output_tokens") is not None
                        else None
                    ),
                    max_cost_usd=(
                        float(raw["max_cost_usd"])
                        if raw.get("max_cost_usd") is not None
                        else None
                    ),
                    capability=capability,
                    capability_input=_omit_none_object_values(dict(rendered_input)),
                    output_schema=(
                        dict(raw["output_schema"]) if raw.get("output_schema") else None
                    ),
                    verification_policy=dict(raw.get("verification_policy") or {}),
                    max_repairs=(
                        int(raw["max_repairs"])
                        if raw.get("max_repairs") is not None
                        else None
                    ),
                    allowed_tools=sorted(tool_capabilities),
                    skill_names=sorted(skills),
                    metadata={
                        "scenario_id": scenario.scenario_id,
                        "scenario_version": scenario.version,
                        **(
                            {"agent_revision_id": agent_revision_id}
                            if agent_revision_id
                            else {}
                        ),
                        "skill_refs": [
                            item.to_dict() for item in scenario.required_skills
                        ],
                    },
                )
            )
        return TaskGraphSpec(
            goal=goal,
            tasks=tasks,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            max_concurrent=int(scenario.execution_policy.get("max_concurrent") or 4),
            fail_fast=bool(scenario.execution_policy.get("fail_fast", True)),
            failure_policy=dict(scenario.execution_policy.get("failure_policy") or {}),
            aggregate=bool(scenario.execution_policy.get("aggregate", True)),
            aggregation_policy=dict(scenario.execution_policy.get("aggregation_policy") or {}),
            max_input_tokens=(
                int(scenario.execution_policy["max_input_tokens"])
                if scenario.execution_policy.get("max_input_tokens") is not None
                else None
            ),
            max_output_tokens=(
                int(scenario.execution_policy["max_output_tokens"])
                if scenario.execution_policy.get("max_output_tokens") is not None
                else None
            ),
            max_cost_usd=(
                float(scenario.execution_policy["max_cost_usd"])
                if scenario.execution_policy.get("max_cost_usd") is not None
                else None
            ),
            idempotency_key=idempotency_key,
            request_id=request_id,
            tracker_id=tracker_id,
            traceparent=traceparent,
            tracestate=tracestate,
            metadata={
                "scenario_id": scenario.scenario_id,
                "scenario_version": scenario.version,
                "scenario_inputs": inputs,
            },
        )


def build_coordinator_graph(
    plan: dict[str, Any],
    *,
    goal: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    request_id: str,
    team: Any | None = None,
    member_capabilities: dict[str, set[str]] | None = None,
    member_skills: dict[str, set[str]] | None = None,
    member_skill_refs: dict[str, list[dict[str, Any]]] | None = None,
    shared_inputs: dict[str, Any] | None = None,
    team_workspace_run_id: str | None = None,
) -> TaskGraphSpec | None:
    """Compile a multi-step coordinator plan into durable Agent tasks."""

    steps = list(plan.get("planned_steps") or [])
    if not steps or (team is None and len(steps) < 2):
        return None
    tasks: list[GraphTaskSpec] = []
    barrier: list[str] = []
    parallel_since_barrier: list[str] = []
    for index, step in enumerate(steps):
        raw_name = str(step.get("name") or f"step-{index + 1}")
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_name).strip("-.")[:80]
        task_id = (
            str(step.get("id"))
            if team is not None
            else f"step-{index + 1}-{slug or 'task'}"
        )
        parallel = bool(step.get("can_run_in_parallel"))
        dependencies = (
            [str(item) for item in step.get("depends_on") or ()]
            if team is not None
            else list(barrier if parallel else [*barrier, *parallel_since_barrier])
        )
        member = (
            team.member(str(step.get("member_id") or team.coordinator_member_id))
            if team is not None
            else None
        )
        if team is not None and member is None:
            raise ValueError("coordinator graph references an unknown AgentTeam member")
        selected_tools = {
            str(item.get("capability_id"))
            for item in plan.get("selected_capabilities") or []
            if isinstance(item, dict)
        }
        allowed_member_tools = (
            set((member_capabilities or {}).get(member.member_id, set()))
            if member is not None
            else selected_tools
        )
        selected_skills = set(plan.get("selected_skills") or [])
        allowed_member_skills = (
            set((member_skills or {}).get(member.member_id, set()))
            if member is not None
            else selected_skills
        )
        step_contract = {
            "step_id": task_id,
            "phase": str(step.get("phase") or "execution"),
            "kind": str(step.get("kind") or "produce"),
            "acceptance_criteria": list(step.get("acceptance_criteria") or []),
            "review_of": list(step.get("review_of") or []),
            "revision_of": step.get("revision_of"),
            "review_round": int(step.get("review_round") or 0),
        }
        task_metadata: dict[str, Any] = {
            "coordinator_step": index + 1,
            "team_step_contract": step_contract if team is not None else {},
        }
        if member is not None:
            task_metadata.update(
                {
                    "team_ref": {
                        "team_id": team.team_id,
                        "revision_id": team.revision_id,
                        "version": team.version,
                        "coordinator_member_id": team.coordinator_member_id,
                    },
                    "team_member_id": member.member_id,
                    "team_member": member.to_dict(),
                    "agent_revision_id": member.agent_revision_id,
                    "team_context_policy": dict(team.context_policy),
                    "team_budget_policy": dict(team.budget_policy),
                    "team_approval_policy": dict(team.approval_policy),
                    "team_confirmed_inputs": dict(shared_inputs or {}),
                    "team_workspace_run_id": team_workspace_run_id,
                    "skill_refs": [
                        item
                        for item in (member_skill_refs or {}).get(member.member_id, [])
                        if str(item.get("skill_id") or "").removeprefix("skill.")
                        in selected_skills & allowed_member_skills
                    ],
                }
            )
        kind = str(step.get("kind") or "produce")
        criteria = list(step.get("acceptance_criteria") or [])
        review_instruction = ""
        if kind == "review":
            review_instruction = (
                "\n\nIndependently review the declared review_of results. Return a verdict, "
                "specific issues, required changes, and evidence. Do not silently rewrite "
                "the source deliverable."
            )
        elif kind == "revise":
            review_instruction = (
                "\n\nRevise the declared revision_of deliverable using the review feedback in "
                "the dependency context. Preserve accepted parts and explicitly resolve each "
                "required change."
            )
        elif kind == "checkpoint":
            review_instruction = (
                "\n\nAssess the completed collaboration wave. Record whether the work can "
                "continue, needs revision, or is ready to finish, plus unresolved issues."
            )
        elif kind == "synthesize":
            review_instruction = (
                "\n\nSynthesize the final Team decision. Preserve material disagreements, "
                "state which recommendations were accepted, and cite the supporting artifacts."
            )
        output_schema = (
            dict(step["output_schema"])
            if isinstance(step.get("output_schema"), dict)
            else dict(_TEAM_REVIEW_SCHEMA)
            if kind == "review"
            else dict(_TEAM_CHECKPOINT_SCHEMA)
            if kind == "checkpoint"
            else None
        )
        tasks.append(
            GraphTaskSpec(
                id=task_id,
                name=raw_name[:128],
                prompt=(
                    f"Overall user request:\n{goal}\n\n"
                    f"Assigned objective:\n{str(step.get('objective') or raw_name)}\n\n"
                    f"Phase: {step_contract['phase']}\n"
                    f"Step kind: {kind}\n"
                    f"Acceptance criteria: {criteria}\n"
                    "Complete only this objective and return a concise, evidence-backed result "
                    "for the final coordinating Agent."
                    f"{review_instruction}"
                ),
                agent_id=member.agent_id if member is not None else agent_id,
                dependencies=dependencies,
                allowed_tools=sorted(selected_tools & allowed_member_tools),
                skill_names=sorted(selected_skills & allowed_member_skills),
                metadata=task_metadata,
                output_schema=output_schema,
            )
        )
        if team is not None:
            continue
        if parallel:
            parallel_since_barrier.append(task_id)
        else:
            barrier = [task_id]
            parallel_since_barrier = []
    if team is not None and bool(team.approval_policy.get("require_result_approval")):
        if len(tasks) >= int(team.budget_policy["max_tasks"]):
            raise ValueError("AgentTeam result approval exceeds the task budget")
        depended_on = {
            dependency for item in tasks for dependency in item.dependencies
        }
        terminal_tasks = [item.id for item in tasks if item.id not in depended_on]
        if len(terminal_tasks) > 16:
            raise ValueError("AgentTeam result approval supports at most 16 planned tasks")
        coordinator = team.coordinator
        tasks.append(
            GraphTaskSpec(
                id="team-result-approval",
                name="Approve AgentTeam result",
                prompt="Review and approve the completed AgentTeam results.",
                agent_id=coordinator.agent_id,
                dependencies=terminal_tasks,
                node_type="approval",
                approval={
                    key: team.approval_policy[key]
                    for key in (
                        "title",
                        "description",
                        "required_role",
                        "expires_in_seconds",
                        "risk",
                        "data_classification",
                    )
                },
                metadata={
                    "team_ref": {
                        "team_id": team.team_id,
                        "revision_id": team.revision_id,
                        "version": team.version,
                        "coordinator_member_id": team.coordinator_member_id,
                    },
                    "team_member_id": coordinator.member_id,
                    "team_member": coordinator.to_dict(),
                    "agent_revision_id": coordinator.agent_revision_id,
                    "team_context_policy": dict(team.context_policy),
                    "team_budget_policy": dict(team.budget_policy),
                    "team_approval_policy": dict(team.approval_policy),
                    "team_workspace_run_id": team_workspace_run_id,
                },
            )
        )
    return TaskGraphSpec(
        goal=goal,
        tasks=tasks,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        agent_revision_id=(
            team.coordinator.agent_revision_id if team is not None else None
        ),
        max_concurrent=min(
            8,
            len(tasks),
            int(team.budget_policy["max_parallel_tasks"]) if team is not None else 8,
        ),
        fail_fast=True,
        aggregate=True,
        aggregation_policy={"mode": "llm_synthesis", "version": "v1"},
        request_id=request_id,
        max_input_tokens=(
            team.budget_policy.get("max_input_tokens") if team is not None else None
        ),
        max_output_tokens=(
            team.budget_policy.get("max_output_tokens") if team is not None else None
        ),
        max_cost_usd=(
            team.budget_policy.get("max_cost_usd") if team is not None else None
        ),
        metadata={
            "source": "coordinator",
            "coordinator_plan": plan,
            **(
                {
                    "team_ref": {
                        "team_id": team.team_id,
                        "revision_id": team.revision_id,
                        "version": team.version,
                        "coordinator_member_id": team.coordinator_member_id,
                    },
                    "team_context_policy": dict(team.context_policy),
                    "team_budget_policy": dict(team.budget_policy),
                    "team_approval_policy": dict(team.approval_policy),
                    "team_workspace_run_id": team_workspace_run_id,
                }
                if team is not None
                else {}
            ),
        },
    )


def _omit_none_object_values(value: dict[str, Any]) -> dict[str, Any]:
    """Omit unset optional template fields while retaining intentional list values."""
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, dict):
            cleaned[key] = _omit_none_object_values(item)
        elif isinstance(item, list):
            cleaned[key] = [
                _omit_none_object_values(part) if isinstance(part, dict) else part
                for part in item
            ]
        else:
            cleaned[key] = item
    return cleaned
