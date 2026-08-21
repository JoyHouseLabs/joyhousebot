"""Single execution-mode dispatch shared by HTTP, App launch, and Scheduler.

Every entrypoint into Run creation — the operator ``/control/v1/runs`` API, App
Entry Point launch, scheduled occurrences, and event triggers — resolves
its execution mode here. Keeping one dispatch core prevents the App and
scheduler paths from drifting into a second execution pipeline.
"""

from __future__ import annotations

from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.run_commands import (
    AgentRunTarget,
    ScenarioRunTarget,
    TeamRunTarget,
)
from joyhousebot.application.runs import CreateRunCommand


async def launch_execution(
    *,
    runs: Any,
    workflows: Any | None,
    context: RequestContext,
    execution: dict[str, Any],
    input_text: str = "",
    pinned_revision_id: str | None = None,
    session_id: str | None = None,
    interaction_mode: str = "auto",
    model: str | None = None,
    system_prompt: str | None = None,
    experiment_id: str | None = None,
    allowed_tools: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    verification_policy: dict[str, Any] | None = None,
    timeout_seconds: float = 300.0,
    max_turns: int | None = None,
    max_repairs: int | None = None,
    max_replans: int | None = None,
    input_asset_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    preview: bool = False,
) -> Any:
    """Dispatch one resolved execution mode to the existing services."""

    mode = str(execution.get("mode") or "agent")
    if mode == "workflow":
        if workflows is None:
            raise ValueError("workflow execution requires a WorkflowService")
        return await workflows.execute(
            context,
            str(execution.get("workflow_id") or ""),
            {
                "revision_id": execution.get("revision_id"),
                "input": input_text,
                "session_id": session_id,
                "metadata": dict(metadata or {}),
                "input_asset_ids": list(input_asset_ids or []),
                "preview": preview,
            },
        )
    target = (
        AgentRunTarget(
            mode="agent",
            agent_id=execution.get("agent_id"),
            revision_id=pinned_revision_id,
        )
        if mode == "agent"
        else TeamRunTarget(
            mode="team",
            team_id=execution.get("team_id"),
            revision_id=pinned_revision_id,
        )
        if mode == "team"
        else ScenarioRunTarget(
            mode="scenario",
            scenario_id=execution.get("scenario_id"),
            version=execution.get("version"),
            agent_id=execution.get("agent_id"),
            revision_id=pinned_revision_id,
            inputs=execution.get("inputs"),
        )
    )
    return await runs.create(
        context,
        CreateRunCommand(
            execution=target,
            session_id=session_id,
            interaction_mode=interaction_mode,
            input=input_text,
            model=model,
            system_prompt=system_prompt,
            experiment_id=experiment_id,
            allowed_tools=allowed_tools,
            output_schema=output_schema,
            verification_policy=dict(verification_policy or {}),
            timeout_seconds=timeout_seconds,
            max_turns=max_turns,
            max_repairs=max_repairs,
            max_replans=max_replans,
            input_asset_ids=list(input_asset_ids or []),
            metadata=dict(metadata or {}),
        ),
    )


__all__ = ["launch_execution"]
