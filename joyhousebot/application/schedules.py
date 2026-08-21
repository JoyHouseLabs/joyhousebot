"""User-scoped schedule commands and queries."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import asdict
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.config.extensions import allowed_channel_ids
from joyhousebot.cron.service import _compute_next_run
from joyhousebot.domain.schedules import CronPayload, CronPolicy, CronSchedule
from joyhousebot.scheduling.monitor_repository import ScratchRevisionConflictError

# Delivery targets: phone numbers, channel user/chat ids, emails, @handles.
_DELIVERY_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_@.\-:+]{1,128}$")

# App Entry Point schedules are quota'd per installation so one App cannot
# fill a user's MAX_JOBS_PER_USER budget on its own.
MAX_APP_SCHEDULES_PER_INSTALLATION = 20


def _allowed_channels(config: Any) -> set[str]:
    return allowed_channel_ids(config)


class ScheduleService:
    def __init__(self, scheduler: Any, config: Any | None = None) -> None:
        self.scheduler = scheduler
        self.config = config

    def _validate_delivery(self, deliver: bool, channel: Any, to: Any) -> None:
        """deliver=true may only target an enabled channel with a safe target."""
        if not deliver:
            return
        enabled = _allowed_channels(self.config) if self.config is not None else set()
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
        self,
        context: RequestContext,
        *,
        include_disabled: bool,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.scheduler.list_jobs,
            include_disabled=include_disabled,
            user_id=context.user_id,
        )
        return [asdict(row) for row in rows if kind is None or row.payload.kind == kind]

    async def list_runs(
        self, context: RequestContext, *, schedule_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.scheduler.list_runs,
            user_id=context.user_id,
            job_id=schedule_id,
            limit=limit,
        )

    async def execution_summary(
        self, context: RequestContext, schedule_id: str
    ) -> dict[str, Any]:
        row = await asyncio.to_thread(
            self.scheduler.execution_summary,
            schedule_id,
            user_id=context.user_id,
        )
        if row is None:
            raise NotFoundError("schedule not found")
        return row

    async def resume(
        self, context: RequestContext, schedule_id: str, body: Any
    ) -> dict[str, Any]:
        row = await asyncio.to_thread(
            self.scheduler.resume_job,
            schedule_id,
            user_id=context.user_id,
            actor_id=context.principal.subject,
            reset_counters=body.reset_counters,
        )
        if row is None:
            raise NotFoundError("schedule not found")
        return asdict(row)

    async def run_now(self, context: RequestContext, schedule_id: str) -> dict[str, Any]:
        """Submit one durable manual occurrence without bypassing schedule policy."""
        accepted = await self.scheduler.run_job(
            schedule_id,
            force=True,
            user_id=context.user_id,
        )
        if not accepted:
            current = await asyncio.to_thread(
                self.scheduler.list_jobs,
                include_disabled=True,
                user_id=context.user_id,
            )
            if not any(item.id == schedule_id for item in current):
                raise NotFoundError("schedule not found")
            raise ConflictError("schedule is already being triggered")
        rows = await asyncio.to_thread(
            self.scheduler.list_runs,
            user_id=context.user_id,
            job_id=schedule_id,
            limit=1,
        )
        if not rows:
            raise ConflictError("manual schedule occurrence was not recorded")
        return rows[0]

    async def create_app_schedule(
        self,
        context: RequestContext,
        body: Any,
        *,
        installation_id: str,
        app_releases: Any,
    ) -> dict[str, Any]:
        """Create one App Entry Point schedule after resolving it once up front.

        The creation-time ``resolve_launch`` pre-check keeps dependency drift
        out of the stored schedule: an inactive installation or a missing
        Entry Point fails here with 409/404 instead of disabling itself on the
        first tick.
        """

        spec = body.schedule
        payload = body.payload
        self._validate_delivery(payload.deliver, payload.channel, payload.to)
        await app_releases.resolve_launch(
            installation_id,
            user_id=context.user_id,
            entrypoint_id=payload.entrypoint_id,
            structured_input=payload.inputs,
        )
        existing = await asyncio.to_thread(
            self.scheduler.list_jobs,
            include_disabled=True,
            user_id=context.user_id,
        )
        owned = [row for row in existing if row.installation_id == installation_id]
        if len(owned) >= MAX_APP_SCHEDULES_PER_INSTALLATION:
            raise ConflictError(
                "App installation has reached its schedule limit "
                f"({MAX_APP_SCHEDULES_PER_INSTALLATION})"
            )
        policy_values = body.policy.model_dump()
        if "misfire_policy" not in body.policy.model_fields_set:
            policy_values["misfire_policy"] = "skip"
        if "overlap_policy" not in body.policy.model_fields_set:
            policy_values["overlap_policy"] = "skip"
        policy = CronPolicy(**policy_values)
        # Same durable idempotency contract as personal schedules, but scoped
        # to the installation so retries cannot collide across Apps.
        job_id = None
        if context.idempotency_key:
            identity = (
                f"app-schedule:{installation_id}:{context.idempotency_key}".encode("utf-8")
            )
            job_id = f"idem-{hashlib.sha256(identity).hexdigest()}"
        row = await asyncio.to_thread(
            self.scheduler.add_job,
            name=body.name,
            user_id=context.user_id,
            schedule=CronSchedule(
                kind=spec.kind,
                at_ms=spec.at_ms,
                every_ms=spec.every_ms,
                expr=spec.cron_expr,
                tz=spec.timezone,
            ),
            message=payload.message,
            deliver=payload.deliver,
            channel=payload.channel,
            to=payload.to,
            payload_kind="app_entrypoint",
            installation_id=installation_id,
            entrypoint_id=payload.entrypoint_id,
            inputs=payload.inputs,
            policy=policy,
            job_id=job_id,
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

    async def list_app_schedules(
        self, context: RequestContext, *, installation_id: str
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.scheduler.list_jobs,
            include_disabled=True,
            user_id=context.user_id,
        )
        return [asdict(row) for row in rows if row.installation_id == installation_id]

    async def app_execution_summary(
        self,
        context: RequestContext,
        *,
        installation_id: str,
        schedule_id: str,
    ) -> dict[str, Any]:
        schedules = await self.list_app_schedules(
            context, installation_id=installation_id
        )
        if not any(row["id"] == schedule_id for row in schedules):
            raise NotFoundError("App schedule not found")
        return await self.execution_summary(context, schedule_id)

    async def resume_app_schedule(
        self,
        context: RequestContext,
        body: Any,
        *,
        installation_id: str,
        schedule_id: str,
    ) -> dict[str, Any]:
        schedules = await self.list_app_schedules(
            context, installation_id=installation_id
        )
        if not any(row["id"] == schedule_id for row in schedules):
            raise NotFoundError("App schedule not found")
        return await self.resume(context, schedule_id, body)

    async def create(self, context: RequestContext, body: Any) -> dict[str, Any]:
        spec = body.schedule
        payload = body.payload
        self._validate_delivery(body.payload.deliver, body.payload.channel, body.payload.to)
        policy_values = body.policy.model_dump()
        if payload.kind == "agent_monitor":
            if "misfire_policy" not in body.policy.model_fields_set:
                policy_values["misfire_policy"] = "skip"
            if "overlap_policy" not in body.policy.model_fields_set:
                policy_values["overlap_policy"] = "skip"
        policy = CronPolicy(**policy_values)
        # Schedule creation participates in the same durable idempotency contract as
        # Run submission.  Gateways can safely retry a request after losing the HTTP
        # response without creating a second live schedule.  The request body is
        # intentionally frozen by the first successful write for a given key.
        job_id = None
        if context.idempotency_key:
            identity = (
                f"schedule:{context.user_id}:{context.idempotency_key}".encode("utf-8")
            )
            job_id = f"idem-{hashlib.sha256(identity).hexdigest()}"
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
            message=payload.message,
            deliver=payload.deliver,
            channel=payload.channel,
            to=payload.to,
            payload_kind=payload.kind,
            session_mode=payload.session_mode,
            session_id=payload.session_id,
            quiet_token=payload.quiet_token,
            defer_when_busy=payload.defer_when_busy,
            busy_backoff_ms=payload.busy_backoff_ms,
            preflight_mode=payload.preflight_mode,
            context_mode=payload.context_mode,
            active_hours=payload.active_hours,
            policy=policy,
            job_id=job_id,
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
        if current.payload.managed_by == "agent_revision":
            raise ConflictError(
                "schedule is managed by an Agent revision; update monitor_policy instead"
            )
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
                kind=payload.kind,
                message=payload.message,
                deliver=payload.deliver,
                channel=payload.channel,
                to=payload.to,
                session_mode=payload.session_mode,
                session_id=payload.session_id,
                quiet_token=payload.quiet_token,
                defer_when_busy=payload.defer_when_busy,
                busy_backoff_ms=payload.busy_backoff_ms,
                preflight_mode=payload.preflight_mode,
                context_mode=payload.context_mode,
                active_hours=payload.active_hours,
            )
        if "policy" in fields and body.policy is not None:
            current.policy = CronPolicy(**body.policy.model_dump())
        self._validate_delivery(
            current.payload.deliver, current.payload.channel, current.payload.to
        )
        now = await asyncio.to_thread(self.scheduler.repository.db_now_ms)
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
        if current is not None and current.payload.managed_by == "agent_revision":
            raise ConflictError(
                "schedule is managed by an Agent revision; disable monitor_policy instead"
            )
        removed = await asyncio.to_thread(
            self.scheduler.remove_job, schedule_id, user_id=context.user_id
        )
        if not removed:
            raise NotFoundError("schedule not found")

    async def monitor_scratch(
        self, context: RequestContext, schedule_id: str
    ) -> dict[str, Any]:
        row = await asyncio.to_thread(
            self.scheduler.get_monitor_scratch,
            schedule_id,
            user_id=context.user_id,
        )
        if row is None:
            raise NotFoundError("Agent Monitor not found")
        return row

    async def update_monitor_scratch(
        self, context: RequestContext, schedule_id: str, body: Any
    ) -> dict[str, Any]:
        action_id = (
            f"api-monitor-scratch:{context.user_id}:{schedule_id}:{context.idempotency_key}"
            if context.idempotency_key
            else None
        )
        try:
            row = await asyncio.to_thread(
                self.scheduler.update_monitor_scratch,
                schedule_id,
                user_id=context.user_id,
                content=body.content,
                expected_revision=body.expected_revision,
                actor_type="api",
                actor_id=context.principal.subject,
                action_id=action_id,
            )
        except ScratchRevisionConflictError as exc:
            raise ConflictError(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if row is None:
            raise NotFoundError("Agent Monitor not found")
        return row

    async def monitor_scratch_revisions(
        self, context: RequestContext, schedule_id: str, *, limit: int
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.scheduler.list_monitor_scratch_revisions,
            schedule_id,
            user_id=context.user_id,
            limit=limit,
        )
        if rows is None:
            raise NotFoundError("Agent Monitor not found")
        return rows
