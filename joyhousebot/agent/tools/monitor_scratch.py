"""Capability-gated private scratch for the current Agent Monitor."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.scheduling.monitor_repository import ScratchRevisionConflictError


class MonitorScratchTool(Tool):
    """Read or optimistically replace only the current monitor's scratch."""

    side_effect = "internal"
    idempotent = True
    retryable = True
    data_classification = "confidential"

    def __init__(self, cron_service: Any) -> None:
        self._cron = cron_service

    @property
    def name(self) -> str:
        return "monitor_scratch"

    @property
    def description(self) -> str:
        return (
            "Read or update the private, versioned scratch belonging to the current "
            "scheduled Agent Monitor. It is unavailable to ordinary Runs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "update"]},
                "content": {
                    "type": "string",
                    "maxLength": 16384,
                    "description": "Complete replacement content for update",
                },
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Revision returned by get or included in the monitor prompt",
                },
            },
            "required": ["action"],
        }

    @staticmethod
    def _identity(kwargs: dict[str, Any]) -> tuple[ToolExecutionContext, str]:
        context = kwargs.get("tool_context")
        if not isinstance(context, ToolExecutionContext):
            raise ToolInvocationError("CONTEXT_REQUIRED", "monitor scratch requires run context")
        metadata = dict(context.metadata or {})
        if metadata.get("schedule_payload_kind") != "agent_monitor":
            raise ToolInvocationError(
                "MONITOR_CONTEXT_REQUIRED",
                "monitor scratch is only available inside a scheduled Agent Monitor Run",
            )
        schedule_id = str(metadata.get("schedule_id") or "")
        if not schedule_id:
            raise ToolInvocationError("MONITOR_CONTEXT_REQUIRED", "monitor schedule id is missing")
        return context, schedule_id

    async def execute(
        self,
        action: str,
        content: str | None = None,
        expected_revision: int | None = None,
        **kwargs: Any,
    ) -> str:
        context, schedule_id = self._identity(kwargs)
        if action == "get":
            state = self._cron.get_monitor_scratch(schedule_id, user_id=context.user_id)
            if state is None:
                raise ToolInvocationError("MONITOR_NOT_FOUND", "Agent Monitor not found")
            return json.dumps(
                {"revision": state["revision"], "content": state["content"]},
                ensure_ascii=False,
            )
        if action != "update":
            raise ToolInvocationError("INVALID_ACTION", f"Unknown action: {action}")
        if content is None or expected_revision is None:
            raise ToolInvocationError(
                "INVALID_PARAMETERS",
                "content and expected_revision are required for update",
            )
        try:
            state = self._cron.update_monitor_scratch(
                schedule_id,
                user_id=context.user_id,
                content=content,
                expected_revision=expected_revision,
                actor_type="agent",
                actor_id=context.agent_id,
                run_id=context.run_id,
                action_id=context.action_id,
            )
        except ScratchRevisionConflictError as exc:
            raise ToolInvocationError("REVISION_CONFLICT", str(exc), retryable=True) from exc
        except ValueError as exc:
            raise ToolInvocationError("INVALID_PARAMETERS", str(exc)) from exc
        if state is None:
            raise ToolInvocationError("MONITOR_NOT_FOUND", "Agent Monitor not found")
        return json.dumps(
            {"revision": state["revision"], "content": state["content"]},
            ensure_ascii=False,
        )
