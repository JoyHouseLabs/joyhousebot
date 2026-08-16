"""Successful Agent terminal commit after required verification has passed."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.runtime.context import CancellationToken
from porthouse.runtime.models import (
    AgentEvent,
    AgentResult,
    AgentUsage,
    EventType,
    RunStatus,
    utc_now,
)


async def finalize_agent_result(
    runtime: Any,
    *,
    record: Any,
    cancellation: CancellationToken,
    content: str | None,
    structured_output: Any,
    usage: AgentUsage,
    tools_used: list[str],
    started_at: str,
) -> AgentResult:
    result = AgentResult(
        run_id=record.run_id,
        status=RunStatus.COMPLETED,
        content=content,
        structured_output=structured_output,
        stop_reason="completed",
        usage=usage,
        tools_used=tools_used,
        started_at=started_at,
        finished_at=utc_now(),
    )
    media_type = "application/json" if structured_output is not None else "text/plain"
    await runtime.events.publish(
        AgentEvent(
            run_id=record.run_id,
            type=EventType.PHASE_COMPLETED.value,
            phase="finalizing",
            data={"name": "execution"},
        )
    )
    persisted = await runtime._commit_terminal(
        record.run_id,
        status=RunStatus.COMPLETED,
        event_type=EventType.RUN_COMPLETED,
        result=result.to_dict(),
        artifacts=[
            {
                "artifact_id": f"{record.run_id}:final",
                "name": "final-output",
                "media_type": media_type,
                "content": structured_output if structured_output is not None else content,
                "provenance": {
                    "worker_id": runtime.worker_id,
                    "lease_version": record.lease_version,
                    "terminal": True,
                },
            }
        ],
        worker_id=runtime.worker_id,
        lease_version=record.lease_version,
    )
    if persisted is None:
        cancellation.cancel("run reached a terminal state on another worker")
        raise asyncio.CancelledError(cancellation.reason)
    await runtime._log(
        record.run_id,
        "run.completed",
        "Agent run completed",
        data={"usage": usage.to_dict(), "tools_used": tools_used},
    )
    return result
