"""Direct pinned Capability execution for one durable Graph Task."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.domain.capabilities import CapabilityRef, InvocationStatus
from joyhousebot.orchestration.task_graph import render_value
from joyhousebot.runtime.action_identity import durable_turn_id, payload_hash
from joyhousebot.runtime.context import CancellationToken, ToolExecutionContext
from joyhousebot.runtime.graph_branch_execution import (
    capability_result_prompt,
    dependency_result_variables,
)
from joyhousebot.runtime.models import AgentEvent, AgentUsage, EventType


async def execute_graph_capability(
    runtime: Any,
    run: Any,
    task: Any,
    capability: CapabilityRef,
    dependency_context: dict[str, Any],
    spec_id: str,
    cancellation: CancellationToken,
) -> tuple[str, list[str], AgentUsage, Any, str]:
    variables = dependency_result_variables(dependency_context)
    capability_input = render_value(
        dict(task.payload.get("capability_input") or {}), variables
    )
    turn_id = durable_turn_id(run.run_id, task.task_id, task.attempt)
    turn, _ = await asyncio.to_thread(
        runtime.store.create_runtime_turn,
        turn_id=turn_id,
        run_id=run.run_id,
        task_id=task.task_id,
        turn_index=task.attempt,
        model=None,
        request_hash=payload_hash(
            {"capability": capability.to_dict(), "input": capability_input}
        ),
        worker_id=runtime.worker_id,
    )
    expected_hash = payload_hash(
        {"capability": capability.to_dict(), "input": capability_input}
    )
    if turn.request_hash != expected_hash:
        raise RuntimeError(f"durable Graph Task input conflict: {task.task_id}")
    agent_revision_id = (
        str(dict(task.payload.get("metadata") or {}).get("agent_revision_id") or "")
        or None
    )
    agent = await runtime._resolve_execution_agent(
        run.run_id, task.agent_id, agent_revision_id
    )
    registry = getattr(agent, "capabilities", None)
    if registry is None:
        raise RuntimeError(f"agent has no capability registry: {task.agent_id}")
    scenario_state = await asyncio.to_thread(
        runtime.store.get_run_scenario_state,
        run.run_id,
        expected_user_id=run.user_id,
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.CAPABILITY_REQUESTED.value,
            turn_id=turn_id,
            data={"capability_id": capability.capability_id},
        )
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.CAPABILITY_STARTED.value,
            status="running",
            turn_id=turn_id,
            data={"capability_id": capability.capability_id},
        )
    )
    result = await registry.invoke_tool(
        capability.capability_id,
        capability_input,
        version=capability.version,
        context=ToolExecutionContext(
            run_id=run.run_id,
            task_id=task.task_id,
            root_run_id=run.root_run_id,
            session_key=f"{run.user_id}:{task.agent_id}:{run.session_id}",
            session_id=run.session_id,
            channel="runtime",
            chat_id=spec_id,
            user_id=run.user_id,
            agent_id=task.agent_id,
            allowed_tools=frozenset({capability.capability_id}),
            granted_permissions=await runtime._execution_permissions(
                run.run_id, task.agent_id, agent_revision_id
            ),
            cancellation=cancellation,
            worker_id=runtime.worker_id,
            turn_id=turn_id,
            turn_index=task.attempt,
            action_index=0,
            metadata={
                **dict(task.payload.get("metadata") or {}),
                "scenario_id": str(getattr(scenario_state, "scenario_id", "") or ""),
                "scenario_version": int(
                    getattr(scenario_state, "scenario_version", 0) or 0
                ),
                "scenario_inputs": dict(
                    getattr(scenario_state, "collected_inputs", {}) or {}
                ),
            },
        ),
        tool_call_id=f"{task.task_id}:{task.attempt}:0",
    )
    if result.status != InvocationStatus.SUCCEEDED:
        error = result.error
        raise RuntimeError(error.message if error else result.summary)
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.CAPABILITY_COMPLETED.value,
            status="completed",
            turn_id=turn_id,
            data={
                "capability_id": capability.capability_id,
                "invocation_id": result.invocation_id,
                "summary": result.summary,
            },
        )
    )
    return (
        capability_result_prompt(result),
        [capability.capability_id],
        AgentUsage(),
        result,
        turn_id,
    )
