"""Single scheduler-side submission path for every schedule payload kind.

``agent_turn`` and ``agent_monitor`` occurrences submit a direct Agent run;
``app_entrypoint`` occurrences resolve the installation's pinned Entry Point
and dispatch through the shared ``launch_execution`` core, so scheduled App
runs land on the same Run pipeline (and the same ``metadata.app`` generated
columns) as interactive App launches. Both bootstrap hosts (API process and
Scheduler Worker) assemble this callable instead of duplicating callbacks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from joyhousebot.application.app_releases import AppReleaseService
from joyhousebot.application.context import Principal, RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.application.run_launch import launch_execution
from joyhousebot.application.runs import RunService
from joyhousebot.application.workflows import WorkflowService
from joyhousebot.cron.service import CronService
from joyhousebot.domain.schedules import (
    CronJob,
    ScheduleAppUnavailableError,
    schedule_run_prompt,
    schedule_run_session_id,
)
from joyhousebot.runtime.models import AgentOptions


def schedule_idempotency_key(job: CronJob) -> str:
    """The stable submission key shared by both submission paths."""

    return (
        f"schedule:{job.id}:{job.state.scheduled_for_ms or 'manual'}:"
        f"{job.state.attempt}"
    )


def build_schedule_submission(
    *,
    runtime: Any,
    store: Any,
    cron: CronService,
    default_agent_id: str,
) -> Callable[[CronJob], Awaitable[str]]:
    """Assemble the ``on_job`` callback used by both bootstrap hosts."""

    async def submit(job: CronJob) -> str:
        if job.payload.kind == "app_entrypoint":
            return await _submit_app_entrypoint(job)
        return await _submit_agent(job)

    async def _submit_agent(job: CronJob) -> str:
        monitor_context = await asyncio.to_thread(cron.monitor_run_context, job)
        record = await runtime.submit_run(
            AgentOptions(
                prompt=schedule_run_prompt(
                    job,
                    scratch=str(monitor_context.get("scratch") or ""),
                    scratch_revision=int(monitor_context.get("scratch_revision") or 0),
                    observation=dict(monitor_context.get("observation") or {}),
                ),
                user_id=job.user_id,
                session_id=schedule_run_session_id(job),
                agent_id=job.agent_id or default_agent_id,
                channel="schedule",
                chat_id=job.id,
                metadata={
                    "schedule_id": job.id,
                    "schedule_occurrence_id": job.state.occurrence_id,
                    "schedule_attempt": job.state.attempt,
                    "schedule_payload_kind": job.payload.kind,
                    "monitor_quiet_token": (
                        job.payload.quiet_token
                        if job.payload.kind == "agent_monitor"
                        else None
                    ),
                    "monitor_scratch_revision": monitor_context.get("scratch_revision"),
                    "monitor_observation_hash": monitor_context.get("observation_hash"),
                    "monitor_context_mode": (
                        job.payload.context_mode
                        if job.payload.kind == "agent_monitor"
                        else None
                    ),
                    # Agent Workers must not claim this Run until Scheduler
                    # atomically links it to the occurrence and advances the
                    # schedule cursor.
                    "_runtime_schedule_submission_ready": False,
                },
                idempotency_key=schedule_idempotency_key(job),
            )
        )
        return record.run_id

    async def _submit_app_entrypoint(job: CronJob) -> str:
        installation_id = job.payload.installation_id or ""
        entrypoint_id = job.payload.entrypoint_id
        app_releases = AppReleaseService(store)
        try:
            _selected, launch = await app_releases.resolve_launch(
                installation_id,
                user_id=job.user_id,
                entrypoint_id=entrypoint_id,
                structured_input=dict(job.payload.inputs or {}),
            )
        except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
            # Structural unavailability (uninstalled/suspended installation,
            # drifted dependency lock, missing entrypoint) must settle the
            # occurrence terminally rather than enter submit retries.
            raise ScheduleAppUnavailableError(str(exc)) from exc
        context = RequestContext(
            principal=Principal(
                subject=f"schedule:{job.id}",
                user_id=job.user_id,
                role="user",
                token_type="scheduler",
            ),
            request_id=f"schedule:{job.id}",
            idempotency_key=schedule_idempotency_key(job),
        )
        metadata = {
            "schedule_id": job.id,
            "schedule_occurrence_id": job.state.occurrence_id,
            "schedule_attempt": job.state.attempt,
            "schedule_payload_kind": job.payload.kind,
            "_runtime_schedule_submission_ready": False,
            **dict(launch["metadata"]),
        }
        record = await launch_execution(
            runs=RunService(runtime, store),
            workflows=WorkflowService(runtime, store, default_agent_id=default_agent_id),
            context=context,
            execution=dict(launch["execution"]),
            # The optional schedule message doubles as the run input; agent-mode
            # entrypoints require one, so fall back to a stable description.
            input_text=(
                job.payload.message.strip()
                or f"Scheduled run of App entrypoint {entrypoint_id or 'default'}"
            ),
            pinned_revision_id=launch.get("pinned_revision_id"),
            session_id=schedule_run_session_id(job),
            metadata=metadata,
        )
        return str(record.run_id)

    return submit


__all__ = ["build_schedule_submission", "schedule_idempotency_key"]
