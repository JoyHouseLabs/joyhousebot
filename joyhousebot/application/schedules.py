"""User-scoped schedule commands and queries."""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import NotFoundError, ValidationError
from joyhousebot.cron.service import _compute_next_run, _now_ms
from joyhousebot.cron.types import CronPayload, CronSchedule

# Delivery targets: phone numbers, channel user/chat ids, emails, @handles.
_DELIVERY_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_@.\-:+]{1,128}$")


def _enabled_channels(config: Any) -> set[str]:
    """Channel names whose config section exists and is enabled."""
    channels = getattr(config, "channels", None)
    enabled: set[str] = set()
    for name in getattr(type(channels), "model_fields", {}) or {}:
        section = getattr(channels, name, None)
        if getattr(section, "enabled", False):
            enabled.add(name)
    return enabled


class ScheduleService:
    def __init__(self, scheduler: Any, config: Any | None = None) -> None:
        self.scheduler = scheduler
        self.config = config

    def _validate_delivery(self, deliver: bool, channel: Any, to: Any) -> None:
        """deliver=true may only target an enabled channel with a safe target."""
        if not deliver:
            return
        enabled = _enabled_channels(self.config) if self.config is not None else set()
        if not enabled:
            raise ValidationError(
                "delivery requested but no channels are enabled in configuration"
            )
        normalized = str(channel or "").strip().lower()
        if normalized not in enabled:
            raise ValidationError(
                f"delivery channel must be one of the enabled channels: {sorted(enabled)}"
            )
        target = str(to or "").strip()
        if not _DELIVERY_TARGET_PATTERN.match(target):
            raise ValidationError(
                "delivery target 'to' is required and may only contain "
                "letters, digits and '_@.-:+', up to 128 characters"
            )

    async def list(
        self, context: RequestContext, *, include_disabled: bool
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.scheduler.list_jobs,
            include_disabled=include_disabled,
            user_id=context.user_id,
        )
        return [asdict(row) for row in rows]

    async def list_runs(
        self, context: RequestContext, *, schedule_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.scheduler.list_runs,
            user_id=context.user_id,
            job_id=schedule_id,
            limit=limit,
        )

    async def create(self, context: RequestContext, body: Any) -> dict[str, Any]:
        spec = body.schedule
        self._validate_delivery(body.payload.deliver, body.payload.channel, body.payload.to)
        row = await asyncio.to_thread(
            self.scheduler.add_job,
            name=body.name,
            agent_id=body.agent_id,
            user_id=context.user_id,
            schedule=CronSchedule(
                kind=spec.kind,
                at_ms=spec.at_ms,
                every_ms=spec.every_ms,
                expr=spec.cron_expr,
                tz=spec.timezone,
            ),
            message=body.payload.message,
            deliver=body.payload.deliver,
            channel=body.payload.channel,
            to=body.payload.to,
        )
        if not body.enabled:
            row = (
                await asyncio.to_thread(
                    self.scheduler.enable_job,
                    row.id,
                    False,
                    user_id=context.user_id,
                )
                or row
            )
        return asdict(row)

    async def update(self, context: RequestContext, schedule_id: str, body: Any) -> dict[str, Any]:
        fields = body.model_dump(exclude_unset=True)
        current = next(
            (
                row
                for row in await asyncio.to_thread(
                    self.scheduler.list_jobs,
                    include_disabled=True,
                    user_id=context.user_id,
                )
                if row.id == schedule_id
            ),
            None,
        )
        if current is None:
            raise NotFoundError("schedule not found")
        if "name" in fields:
            current.name = body.name
        if "agent_id" in fields:
            current.agent_id = body.agent_id
        if "enabled" in fields:
            current.enabled = bool(body.enabled)
        if "schedule" in fields:
            spec = body.schedule
            current.schedule = CronSchedule(
                kind=spec.kind,
                at_ms=spec.at_ms,
                every_ms=spec.every_ms,
                expr=spec.cron_expr,
                tz=spec.timezone,
            )
        if "payload" in fields:
            payload = body.payload
            current.payload = CronPayload(
                message=payload.message,
                deliver=payload.deliver,
                channel=payload.channel,
                to=payload.to,
            )
        self._validate_delivery(
            current.payload.deliver, current.payload.channel, current.payload.to
        )
        now = _now_ms()
        current.updated_at_ms = now
        current.state.next_run_at_ms = (
            _compute_next_run(current.schedule, now) if current.enabled else None
        )
        if current.enabled and current.state.next_run_at_ms is None:
            raise ValidationError("schedule does not produce a future occurrence")
        row = await asyncio.to_thread(self.scheduler.repository.update, current)
        if row is None:
            raise NotFoundError("schedule not found")
        return asdict(row)

    async def delete(self, context: RequestContext, schedule_id: str) -> None:
        removed = await asyncio.to_thread(
            self.scheduler.remove_job, schedule_id, user_id=context.user_id
        )
        if not removed:
            raise NotFoundError("schedule not found")
