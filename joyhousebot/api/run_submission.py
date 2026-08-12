"""Shared HTTP-to-application Run submission facade.

Public Run and App launch endpoints both resolve to the same application
services.  Keeping the translation here prevents App launches from creating a
second execution path or importing Runtime internals into an API router.
"""

from __future__ import annotations

from typing import Any

from joyhousebot.api.run_schemas import CreateRunRequest
from joyhousebot.application.context import RequestContext
from joyhousebot.application.run_commands import (
    AgentRunTarget,
    ScenarioRunTarget,
    TeamRunTarget,
)
from joyhousebot.application.runs import CreateRunCommand


async def submit_create_run(
    body: CreateRunRequest,
    *,
    context: RequestContext,
    container: Any,
    pinned_revision_id: str | None = None,
) -> Any:
    execution = body.execution
    if execution.mode == "workflow":
        return await container.workflows.execute(
            context,
            execution.workflow_id,
            {
                "revision_id": execution.revision_id,
                "input": body.input.content,
                "session_id": body.session_id,
                "metadata": body.metadata,
                "input_asset_ids": body.input_asset_ids,
                "preview": False,
            },
        )
    target = (
        AgentRunTarget(
            mode="agent",
            agent_id=execution.agent_id,
            revision_id=pinned_revision_id,
        )
        if execution.mode == "agent"
        else TeamRunTarget(
            mode="team",
            team_id=execution.team_id,
            revision_id=pinned_revision_id,
        )
        if execution.mode == "team"
        else ScenarioRunTarget(
            mode="scenario",
            scenario_id=execution.scenario_id,
            version=execution.version,
            agent_id=execution.agent_id,
            revision_id=pinned_revision_id,
            inputs=execution.inputs,
        )
    )
    return await container.runs.create(
        context,
        CreateRunCommand(
            execution=target,
            session_id=body.session_id,
            interaction_mode=body.interaction_mode,
            input=body.input.content,
            model=body.model,
            system_prompt=body.system_prompt,
            allowed_tools=body.allowed_tools,
            output_schema=body.output_schema,
            verification_policy=body.verification_policy,
            timeout_seconds=body.timeout_seconds,
            max_turns=body.max_turns,
            max_repairs=body.max_repairs,
            max_replans=body.max_replans,
            input_asset_ids=body.input_asset_ids,
            metadata=body.metadata,
        ),
    )


__all__ = ["submit_create_run"]
