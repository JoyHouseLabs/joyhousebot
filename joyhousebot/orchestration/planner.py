"""Compile published fixed scenarios into native distributed task graphs."""

from __future__ import annotations

import re
from typing import Any

from joyhousebot.domain.scenarios import ScenarioVersion
from joyhousebot.orchestration.task_graph import render_value
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec


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
        idempotency_key: str | None,
        request_id: str,
    ) -> TaskGraphSpec | None:
        templates = list(scenario.execution_policy.get("tasks") or [])
        if scenario.planning_mode != "fixed" or not templates:
            return None
        allowed = set(scenario.allowed_capabilities)
        definitions = {
            str(item.get("ref", {}).get("capability_id")): item
            for item in self.store.list_capability_definitions()
        }
        unknown_allowed = allowed - set(definitions)
        if unknown_allowed:
            raise ValueError(
                f"scenario references unpublished capabilities: {sorted(unknown_allowed)}"
            )
        tool_capabilities = {
            capability_id
            for capability_id in allowed
            if definitions[capability_id].get("ref", {}).get("kind")
            in {"tool", "connector"}
        }
        skills = {
            capability_id.removeprefix("skill.")
            for capability_id in allowed
            if definitions[capability_id].get("ref", {}).get("kind") == "skill"
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
            capability_id = str(raw.get("capability_id") or "").strip() or None
            if capability_id and capability_id not in allowed:
                raise ValueError(f"capability is not allowed by scenario: {capability_id}")
            if capability_id:
                kind = definitions[capability_id].get("ref", {}).get("kind")
                if kind not in {"tool", "connector"}:
                    raise ValueError(
                        "fixed task capability_id must be an executable tool or connector: "
                        f"{capability_id}"
                    )
            rendered_input = render_value(raw.get("input") or {}, variables)
            tasks.append(
                GraphTaskSpec(
                    id=str(raw.get("id") or f"step_{position + 1}"),
                    name=str(raw.get("name") or raw.get("id") or f"Step {position + 1}"),
                    prompt=str(render_value(raw.get("prompt") or goal, variables)),
                    agent_id=str(raw.get("agent_id") or agent_id),
                    dependencies=[str(item) for item in raw.get("dependencies") or []],
                    timeout_seconds=float(raw.get("timeout_seconds") or 300),
                    max_attempts=int(raw.get("max_attempts") or 1),
                    capability_id=capability_id,
                    capability_input=_omit_none_object_values(dict(rendered_input)),
                    output_schema=(
                        dict(raw["output_schema"]) if raw.get("output_schema") else None
                    ),
                    allowed_tools=sorted(tool_capabilities),
                    skill_names=sorted(skills),
                    metadata={
                        "scenario_id": scenario.scenario_id,
                        "scenario_version": scenario.version,
                    },
                )
            )
        return TaskGraphSpec(
            goal=goal,
            tasks=tasks,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            max_concurrent=int(scenario.execution_policy.get("max_concurrent") or 4),
            fail_fast=bool(scenario.execution_policy.get("fail_fast", True)),
            aggregate=bool(scenario.execution_policy.get("aggregate", True)),
            aggregation_policy=dict(scenario.execution_policy.get("aggregation_policy") or {}),
            idempotency_key=idempotency_key,
            request_id=request_id,
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
) -> TaskGraphSpec | None:
    """Compile a multi-step coordinator plan into durable Agent tasks."""

    steps = list(plan.get("planned_steps") or [])
    if len(steps) < 2:
        return None
    tasks: list[GraphTaskSpec] = []
    barrier: list[str] = []
    parallel_since_barrier: list[str] = []
    for index, step in enumerate(steps):
        raw_name = str(step.get("name") or f"step-{index + 1}")
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw_name).strip("-.")[:80]
        task_id = f"step-{index + 1}-{slug or 'task'}"
        parallel = bool(step.get("can_run_in_parallel"))
        dependencies = list(barrier if parallel else [*barrier, *parallel_since_barrier])
        tasks.append(
            GraphTaskSpec(
                id=task_id,
                name=raw_name[:128],
                prompt=(
                    f"Overall user request:\n{goal}\n\n"
                    f"Assigned objective:\n{str(step.get('objective') or raw_name)}\n\n"
                    "Complete only this objective and return a concise, evidence-backed result "
                    "for the final coordinating Agent."
                ),
                agent_id=agent_id,
                dependencies=dependencies,
                allowed_tools=list(plan.get("selected_capabilities") or []),
                skill_names=list(plan.get("selected_skills") or []),
                metadata={"coordinator_step": index + 1},
            )
        )
        if parallel:
            parallel_since_barrier.append(task_id)
        else:
            barrier = [task_id]
            parallel_since_barrier = []
    return TaskGraphSpec(
        goal=goal,
        tasks=tasks,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        max_concurrent=min(8, len(tasks)),
        fail_fast=True,
        aggregate=True,
        aggregation_policy={"mode": "llm_synthesis", "version": "v1"},
        request_id=request_id,
        metadata={
            "source": "main-coordinator",
            "coordinator_plan": plan,
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
