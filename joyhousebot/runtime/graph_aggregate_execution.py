"""Execution of explicit, replayable Graph aggregate nodes."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.orchestration.aggregation import (
    aggregate_task_results,
    normalize_aggregation_policy,
    synthesis_prompt,
)
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.models import AgentEvent, AgentUsage, EventType
from joyhousebot.runtime.structured import parse_structured_output


def _sources(
    run_id: str, dependency_results: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "task_id": f"{run_id}:{spec_id}",
            "spec_id": spec_id,
            "agent_id": "",
            "status": str(result.get("status") or "completed"),
            "result": dict(result),
        }
        for spec_id, result in sorted(dependency_results.items())
    ]


async def execute_graph_aggregate(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
    cancellation: CancellationToken,
) -> tuple[str, list[str], AgentUsage, Any, dict[str, Any]]:
    policy = normalize_aggregation_policy(
        dict(task.payload.get("aggregate") or {}), aggregate=True
    )
    sources = _sources(run.run_id, dependency_results)
    common = {
        "node_type": "aggregate",
        "policy": policy.to_dict(),
        "source_task_ids": [item["task_id"] for item in sources],
        "source_count": len(sources),
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.AGGREGATION_STARTED.value,
            status="running",
            data=common,
        )
    )
    if policy.mode == "llm_synthesis":
        content, tools, usage = await runtime._call_agent(
            run_id=run.run_id,
            task_id=task.task_id,
            prompt=synthesis_prompt(
                goal=str(task.payload.get("prompt") or run.prompt),
                tasks=sources,
                policy=policy,
            ),
            user_id=run.user_id,
            session_id=f"{run.session_id}:aggregate:{task.payload.get('spec_id')}",
            agent_id=task.agent_id,
            agent_revision_id=(
                str(dict(task.payload.get("metadata") or {}).get("agent_revision_id") or "")
                or None
            ),
            channel="runtime",
            chat_id=str(task.payload.get("spec_id") or task.task_id),
            model=None,
            system_prompt=None,
            output_schema=(
                dict(task.payload["output_schema"])
                if task.payload.get("output_schema")
                else None
            ),
            timeout_seconds=float(task.payload.get("timeout_seconds") or 300),
            max_turns=None,
            max_input_tokens=(
                int(task.payload["max_input_tokens"])
                if task.payload.get("max_input_tokens") is not None
                else None
            ),
            max_output_tokens=(
                int(task.payload["max_output_tokens"])
                if task.payload.get("max_output_tokens") is not None
                else None
            ),
            max_cost_usd=(
                float(task.payload["max_cost_usd"])
                if task.payload.get("max_cost_usd") is not None
                else None
            ),
            permission_mode="default",
            allowed_tools=[],
            disallowed_tools=[],
            cancellation=cancellation,
            metadata={"aggregate_policy": policy.to_dict()},
            verification_policy=dict(task.payload.get("verification_policy") or {}),
            max_repairs=(
                int(task.payload["max_repairs"])
                if task.payload.get("max_repairs") is not None
                else None
            ),
            task_lease_version=task.lease_version,
        )
        structured_output: Any = None
        if task.payload.get("output_schema"):
            structured_output = parse_structured_output(
                content, dict(task.payload["output_schema"])
            )
        else:
            try:
                candidate = json.loads(content)
                if isinstance(candidate, (dict, list)):
                    structured_output = candidate
            except (TypeError, ValueError):
                pass
        audit = {
            **common,
            "execution": "llm_synthesis",
            "conflicts": [],
            "discarded": [],
        }
    else:
        result = aggregate_task_results(sources, policy)
        content = result.content
        tools = []
        usage = AgentUsage()
        structured_output = result.structured_output
        if task.payload.get("output_schema"):
            parse_structured_output(content, dict(task.payload["output_schema"]))
        audit = {**result.audit, "node_type": "aggregate", "execution": "deterministic"}
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.AGGREGATION_COMPLETED.value,
            status="completed",
            data={
                **common,
                "conflict_count": len(audit.get("conflicts") or []),
                "discarded_count": len(audit.get("discarded") or []),
            },
        )
    )
    return content, tools, usage, structured_output, {"aggregation": audit}
