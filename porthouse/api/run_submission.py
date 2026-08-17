"""Shared HTTP-to-application Run submission facade.

Public Run and App launch endpoints both resolve to the same application
services. Keeping the translation here prevents App launches from creating a
second execution path or importing Runtime internals into an API router.
The mode dispatch itself lives in ``application.run_launch`` and is shared
with the scheduler and event-trigger entrypoints.
"""

from __future__ import annotations

from typing import Any

from porthouse.api.run_schemas import CreateRunRequest
from porthouse.application.context import RequestContext
from porthouse.application.run_launch import launch_execution


async def submit_create_run(
    body: CreateRunRequest,
    *,
    context: RequestContext,
    container: Any,
    pinned_revision_id: str | None = None,
) -> Any:
    execution = (
        dict(body.execution.model_dump(exclude_none=True))
        if hasattr(body.execution, "model_dump")
        else dict(vars(body.execution))
    )
    execution.pop("revision_id", None)
    return await launch_execution(
        runs=container.runs,
        workflows=container.workflows,
        context=context,
        execution=execution,
        input_text=body.input.content,
        pinned_revision_id=pinned_revision_id,
        session_id=body.session_id,
        interaction_mode=body.interaction_mode,
        model=body.model,
        system_prompt=body.system_prompt,
        experiment_id=body.experiment_id,
        allowed_tools=body.allowed_tools,
        output_schema=body.output_schema,
        verification_policy=body.verification_policy,
        timeout_seconds=body.timeout_seconds,
        max_turns=body.max_turns,
        max_repairs=body.max_repairs,
        max_replans=body.max_replans,
        input_asset_ids=body.input_asset_ids,
        metadata=body.metadata,
        preview=False,
    )


__all__ = ["submit_create_run"]
