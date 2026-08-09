"""Cron tool for scheduling reminders and tasks."""

from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.cron.service import CronService
from joyhousebot.cron.types import CronSchedule
from joyhousebot.runtime.context import ToolExecutionContext


class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""

    side_effect = "write"

    def __init__(self, cron_service: CronService):
        self._cron = cron_service

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "message": {"type": "string", "description": "Reminder message (for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
                "monitor": {
                    "type": "boolean",
                    "description": (
                        "Create an Agent Monitor that stays quiet when no action is needed"
                    ),
                },
                "session_mode": {
                    "type": "string",
                    "enum": ["isolated", "main"],
                    "description": "Monitor in a private session or the current main session",
                },
                "preflight_mode": {
                    "type": "string",
                    "enum": ["always", "runtime_attention"],
                    "description": (
                        "Run every tick, or only when deterministic Runtime attention changes"
                    ),
                },
                "context_mode": {
                    "type": "string",
                    "enum": ["full", "light"],
                    "description": "Use full conversation context or the Monitor light context",
                },
                "active_hours_start": {
                    "type": "string",
                    "description": "Optional Monitor active-window start in HH:MM",
                },
                "active_hours_end": {
                    "type": "string",
                    "description": "Optional Monitor active-window end in HH:MM",
                },
                "active_hours_timezone": {
                    "type": "string",
                    "description": "IANA timezone for Monitor active hours",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        monitor: bool = False,
        session_mode: str = "isolated",
        preflight_mode: str = "always",
        context_mode: str = "full",
        active_hours_start: str | None = None,
        active_hours_end: str | None = None,
        active_hours_timezone: str | None = None,
        **kwargs: Any,
    ) -> str:
        tool_context = kwargs.get("tool_context")
        if not isinstance(tool_context, ToolExecutionContext):
            raise ToolInvocationError("CONTEXT_REQUIRED", "Cron tool requires run context")
        if action == "add":
            return self._add_job(
                message,
                every_seconds,
                cron_expr,
                at,
                tool_context.channel,
                tool_context.chat_id,
                tool_context.user_id,
                tool_context.agent_id,
                monitor,
                session_mode,
                preflight_mode,
                context_mode,
                active_hours_start,
                active_hours_end,
                active_hours_timezone,
                tool_context.session_key,
            )
        elif action == "list":
            return self._list_jobs(tool_context.user_id)
        elif action == "remove":
            return self._remove_job(job_id, tool_context.user_id)
        raise ToolInvocationError("INVALID_ACTION", f"Unknown action: {action}")

    def _add_job(
        self,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        at: str | None,
        channel: str,
        chat_id: str,
        user_id: str,
        agent_id: str,
        monitor: bool,
        session_mode: str,
        preflight_mode: str,
        context_mode: str,
        active_hours_start: str | None,
        active_hours_end: str | None,
        active_hours_timezone: str | None,
        session_id: str,
    ) -> str:
        if not message:
            raise ToolInvocationError("INVALID_PARAMETERS", "message is required for add")
        if not channel or not chat_id:
            raise ToolInvocationError("DELIVERY_TARGET_REQUIRED", "no session delivery target")

        # Build schedule
        delete_after = False
        if every_seconds:
            if every_seconds < 60:
                raise ToolInvocationError("INVALID_PARAMETERS", "every_seconds must be at least 60")
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr)
        elif at:
            from datetime import datetime

            dt = datetime.fromisoformat(at)
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            raise ToolInvocationError(
                "INVALID_PARAMETERS", "either every_seconds, cron_expr, or at is required"
            )
        if session_mode not in {"isolated", "main"}:
            raise ToolInvocationError(
                "INVALID_PARAMETERS", "session_mode must be isolated or main"
            )
        if preflight_mode not in {"always", "runtime_attention"}:
            raise ToolInvocationError(
                "INVALID_PARAMETERS",
                "preflight_mode must be always or runtime_attention",
            )
        if context_mode not in {"full", "light"}:
            raise ToolInvocationError(
                "INVALID_PARAMETERS", "context_mode must be full or light"
            )
        active_values = (
            active_hours_start,
            active_hours_end,
            active_hours_timezone,
        )
        if any(active_values) and not all(active_values):
            raise ToolInvocationError(
                "INVALID_PARAMETERS",
                "active hours require start, end, and timezone",
            )

        job = self._cron.add_job(
            name=message[:30],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=channel,
            to=chat_id,
            delete_after_run=delete_after,
            user_id=user_id,
            agent_id=agent_id,
            payload_kind="agent_monitor" if monitor else "agent_turn",
            session_mode=session_mode,
            session_id=session_id if monitor and session_mode == "main" else None,
            preflight_mode=preflight_mode if monitor else "always",
            context_mode=context_mode if monitor else "full",
            active_hours=(
                {
                    "start": str(active_hours_start),
                    "end": str(active_hours_end),
                    "timezone": str(active_hours_timezone),
                }
                if monitor and all(active_values)
                else None
            ),
        )
        resource = "monitor" if monitor else "job"
        return f"Created {resource} '{job.name}' (id: {job.id})"

    def _list_jobs(self, user_id: str) -> str:
        jobs = self._cron.list_jobs(user_id=user_id)
        if not jobs:
            return "No scheduled jobs."
        lines = [
            f"- {j.name} (id: {j.id}, {j.schedule.kind}, {j.payload.kind})" for j in jobs
        ]
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None, user_id: str) -> str:
        if not job_id:
            raise ToolInvocationError("INVALID_PARAMETERS", "job_id is required for remove")
        if self._cron.remove_job(job_id, user_id=user_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"
