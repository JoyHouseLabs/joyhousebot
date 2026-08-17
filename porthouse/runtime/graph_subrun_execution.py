"""Execution of frozen Team and Scenario child Runs from Graph nodes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

from porthouse.orchestration.clarification import ClarificationEngine
from porthouse.orchestration.planner import ScenarioPlanner
from porthouse.orchestration.task_graph import render_value
from porthouse.runtime.graph_branch_execution import dependency_result_variables
from porthouse.runtime.models import AgentEvent, AgentOptions, AgentUsage, EventType

_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def _child_run_id(task: Any) -> str:
    identity = f"{task.run_id}\0{task.task_id}\0{task.payload.get('graph_revision_id')}"
    return f"child_{sha256(identity.encode('utf-8')).hexdigest()}"


def _usage(value: dict[str, Any]) -> AgentUsage:
    return AgentUsage.from_dict(value.get("usage"))


async def _submit_team_child(
    runtime: Any,
    run: Any,
    task: Any,
    configuration: dict[str, Any],
    prompt: str,
    child_run_id: str,
) -> Any:
    team = await asyncio.to_thread(
        runtime.stores.catalog.get_agent_team_revision,
        str(configuration["team_revision_id"]),
    )
    if (
        team is None
        or team.status not in {"published", "retired"}
        or team.team_id != str(configuration["team_id"])
        or team.version != int(configuration["team_version"])
        or team.coordinator_member_id != str(configuration["coordinator_member_id"])
        or team.coordinator.agent_id != str(configuration["coordinator_agent_id"])
        or team.coordinator.agent_revision_id
        != str(configuration["coordinator_agent_revision_id"])
    ):
        raise ValueError("Workflow Team subrun no longer matches its frozen revision")
    return await runtime.submit_run(
        AgentOptions(
            prompt=prompt,
            user_id=run.user_id,
            session_id=f"{run.session_id}:subrun:{task.payload.get('spec_id')}",
            agent_id=team.coordinator.agent_id,
            agent_revision_id=team.coordinator.agent_revision_id,
            channel="workflow",
            chat_id=str(task.payload.get("spec_id") or task.task_id),
            metadata={
                "orchestration": {
                    "mode": "team",
                    "team_id": team.team_id,
                    "revision_id": team.revision_id,
                    "version": team.version,
                },
                "coordinator_required": True,
                "team_ref": {
                    "team_id": team.team_id,
                    "revision_id": team.revision_id,
                    "version": team.version,
                    "coordinator_member_id": team.coordinator_member_id,
                },
                "team_members": [item.to_dict() for item in team.members],
                "team_member_id": team.coordinator_member_id,
                "team_context_policy": dict(team.context_policy),
                "team_budget_policy": dict(team.budget_policy),
                "team_approval_policy": dict(team.approval_policy),
                "team_collaboration_blueprint": dict(team.effective_blueprint),
                "workflow_parent": {
                    "run_id": run.run_id,
                    "task_id": task.task_id,
                },
            },
            idempotency_key=f"workflow-subrun:{task.task_id}",
            root_run_id=run.root_run_id or run.run_id,
            parent_run_id=run.run_id,
            parent_task_id=task.task_id,
            max_children_per_root=int(configuration.get("max_children_per_root") or 32),
        ),
        run_id=child_run_id,
    )


async def _submit_scenario_child(
    runtime: Any,
    run: Any,
    task: Any,
    configuration: dict[str, Any],
    prompt: str,
    child_run_id: str,
) -> Any:
    scenario = await asyncio.to_thread(
        runtime.stores.scenarios.get_scenario_version,
        str(configuration["scenario_id"]),
        int(configuration["scenario_version"]),
    )
    if (
        scenario is None
        or scenario.status not in {"published", "retired"}
        or scenario.planning_mode != "fixed"
    ):
        raise ValueError("Workflow Scenario subrun requires its frozen fixed revision")
    inputs = ClarificationEngine(runtime.stores.clarifications).validate_inputs(
        scenario, dict(configuration.get("inputs") or {})
    )
    step = ClarificationEngine(runtime.stores.clarifications).evaluate(scenario, inputs)
    if not step.complete:
        raise ValueError(
            "Workflow Scenario subrun has unresolved required inputs: "
            + ", ".join(step.missing_inputs)
        )
    spec = await asyncio.to_thread(
        ScenarioPlanner(runtime.stores.catalog).build_graph,
        scenario,
        goal=prompt,
        inputs=step.collected_inputs,
        user_id=run.user_id,
        session_id=f"{run.session_id}:subrun:{task.payload.get('spec_id')}",
        agent_id=str(configuration["agent_id"]),
        agent_revision_id=str(configuration["agent_revision_id"]),
        idempotency_key=f"workflow-subrun:{task.task_id}",
        request_id=f"workflow-subrun:{task.task_id}",
    )
    if spec is None:
        raise ValueError("frozen Scenario does not materialize a fixed task graph")
    spec = replace(
        spec,
        root_run_id=run.root_run_id or run.run_id,
        parent_run_id=run.run_id,
        parent_task_id=task.task_id,
        max_children_per_root=int(configuration.get("max_children_per_root") or 32),
        metadata={
            **dict(spec.metadata),
            "orchestration": {
                "mode": "scenario",
                "scenario_id": scenario.scenario_id,
                "version": scenario.version,
            },
            "workflow_parent": {"run_id": run.run_id, "task_id": task.task_id},
        },
    )
    return await runtime.submit_graph(spec, run_id=child_run_id)


async def execute_graph_subrun(
    runtime: Any,
    run: Any,
    task: Any,
    prompt: str,
    dependency_results: dict[str, dict[str, Any]],
) -> tuple[str | None, list[str], AgentUsage, Any, dict[str, Any]] | None:
    configuration = render_value(
        dict(task.payload.get("subrun") or {}),
        dependency_result_variables(dependency_results),
    )
    mode = str(configuration["mode"])
    child_run_id = _child_run_id(task)
    child = await asyncio.to_thread(
        runtime.stores.runs.get_runtime_run,
        child_run_id,
        expected_user_id=run.user_id,
    )
    if child is None:
        child = (
            await _submit_team_child(
                runtime, run, task, configuration, prompt, child_run_id
            )
            if mode == "team"
            else await _submit_scenario_child(
                runtime, run, task, configuration, prompt, child_run_id
            )
        )
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.SUBRUN_STARTED.value,
                status=child.status,
                data={"child_run_id": child.run_id, "mode": mode},
            )
        )
    if child.status not in _TERMINAL:
        suspended = await asyncio.to_thread(
            runtime.stores.graphs.suspend_graph_task_for_subrun,
            run_id=run.run_id,
            task_id=task.task_id,
            child_run_id=child.run_id,
            subrun_mode=mode,
            worker_id=runtime.worker_id,
            lease_version=task.lease_version,
        )
        if not suspended:
            raise asyncio.CancelledError("subrun suspension fenced by a newer Task lease")
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.SUBRUN_WAITING.value,
                status="waiting_external",
                data={"child_run_id": child.run_id, "mode": mode},
            )
        )
        return None
    if child.status != "completed":
        error = dict(child.error or {})
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.SUBRUN_FAILED.value,
                status=child.status,
                data={
                    "child_run_id": child.run_id,
                    "mode": mode,
                    "error": error,
                },
            )
        )
        raise RuntimeError(
            str(error.get("message") or f"{mode} child Run {child.status}")
        )
    result = dict(child.result or {})
    content = result.get("content")
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    # Team children surface their durable artifacts so downstream Workflow
    # nodes can reference the confirmed final outputs by artifact id.
    child_artifact_ids: list[str] = []
    if mode == "team":
        child_artifacts = await asyncio.to_thread(
            runtime.stores.execution.list_runtime_artifacts, child.run_id
        )
        child_artifact_ids = [
            str(item["artifact_id"])
            for item in child_artifacts
            if item.get("name") not in ("coordinator-plan", "coordinator-graph-spec")
        ][-8:]
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.SUBRUN_COMPLETED.value,
            status="completed",
            data={
                "child_run_id": child.run_id,
                "mode": mode,
                "child_artifact_ids": child_artifact_ids,
            },
        )
    )
    return (
        content,
        [str(item) for item in result.get("tools_used") or []],
        _usage(result),
        result.get("structured_output"),
        {
            "node_type": "subrun",
            "subrun_mode": mode,
            "child_run_id": child.run_id,
            "child_status": child.status,
            "child_artifact_ids": child_artifact_ids,
        },
    )
