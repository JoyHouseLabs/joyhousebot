"""Durable schedule service backed by normalized repository tables."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from joyhousebot.cron.active_hours import is_within_active_hours, normalize_active_hours
from joyhousebot.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronPolicy,
    CronSchedule,
    schedule_run_session_id,
)
from joyhousebot.scheduling.monitor_repository import MonitorRepository
from joyhousebot.scheduling.repository import ScheduleRepository


def _now_ms() -> int:
    """Client wall clock; only for local cron-expression validation.

    Lease and schedule state transitions must use the database clock via
    ``ScheduleRepository.db_now_ms()`` instead (database time owns leases).
    """
    return int(time.time() * 1000)


MIN_SCHEDULE_INTERVAL_MS = 60_000
MAX_JOBS_PER_USER = 50


def _validate_schedule_limits(schedule: CronSchedule) -> None:
    """Reject schedules that would trigger more often than once per minute."""
    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms < MIN_SCHEDULE_INTERVAL_MS:
            raise ValueError("every_ms must be at least 60000 (60s)")
    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
        except ImportError:
            return
        zone = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
        base = datetime.fromtimestamp(_now_ms() / 1000, tz=zone)
        try:
            occurrences = croniter(schedule.expr, base)
            first = occurrences.get_next(datetime)
            second = occurrences.get_next(datetime)
        except Exception:
            raise ValueError("invalid cron expression")
        if (second - first).total_seconds() * 1000 < MIN_SCHEDULE_INTERVAL_MS:
            raise ValueError("cron expression must not trigger more often than once per 60s")


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None
    if schedule.kind == "every":
        return now_ms + schedule.every_ms if schedule.every_ms and schedule.every_ms > 0 else None
    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter

            zone = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base = datetime.fromtimestamp(now_ms / 1000, tz=zone)
            return int(croniter(schedule.expr, base).get_next(datetime).timestamp() * 1000)
        except Exception:
            return None
    return None


class CronService:
    """Manage schedules and claim due occurrences across scheduler replicas."""

    def __init__(
        self,
        runtime_store: Any,
        on_job: Callable[[CronJob], Awaitable[str | None]] | None = None,
        *,
        worker_id: str | None = None,
        lease_ms: int = 5 * 60 * 1000,
        poll_seconds: float = 1.0,
        default_agent_id: str = "default",
    ) -> None:
        self.on_job = on_job
        self.runtime_store = runtime_store
        self.repository = ScheduleRepository(runtime_store)
        self.monitors = MonitorRepository(runtime_store)
        self.worker_id = worker_id or f"scheduler-{uuid.uuid4().hex}"
        self.lease_ms = max(10_000, lease_ms)
        self.poll_seconds = max(0.1, poll_seconds)
        self.default_agent_id = default_agent_id
        self._running = False
        self._timer_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._timer_task = asyncio.create_task(self._run_loop(), name="schedule-claim-loop")
        logger.info("Schedule service started: worker={}", self.worker_id)

    def stop(self) -> None:
        """Signal shutdown and cancel the poll loop.

        Cancellation is initiated synchronously so existing sync callers keep
        working; call ``wait_stopped`` afterwards to await the loop's exit.
        """
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()

    async def wait_stopped(self) -> None:
        """Await the poll loop after ``stop`` cancelled it."""
        task = self._timer_task
        self._timer_task = None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _run_loop(self) -> None:
        while self._running:
            retries, due = await asyncio.gather(
                asyncio.to_thread(self._claim_due_retries),
                asyncio.to_thread(self._claim_due_jobs),
            )
            claimed = [*retries, *due]
            if claimed:
                await asyncio.gather(*(self._execute_claimed_job(job) for job in claimed))
                continue
            await asyncio.sleep(self.poll_seconds)

    def _claim_due_jobs(self) -> list[CronJob]:
        return self.repository.claim_due(
            worker_id=self.worker_id,
            lease_ms=self.lease_ms,
        )

    def _claim_due_retries(self) -> list[CronJob]:
        return self.repository.claim_due_retries(
            worker_id=self.worker_id,
            lease_ms=self.lease_ms,
        )

    def _renew_claimed_job(self, job: CronJob) -> bool:
        return self.repository.renew(
            job,
            worker_id=self.worker_id,
            lease_ms=self.lease_ms,
        )

    async def _execute_claimed_job(
        self,
        job: CronJob,
        *,
        enabled_after_run: bool | None = None,
        ignore_active_hours: bool = False,
    ) -> None:
        async def renew() -> None:
            while True:
                await asyncio.sleep(max(1.0, self.lease_ms / 3000))
                if not await asyncio.to_thread(self._renew_claimed_job, job):
                    return

        occurrence_id = job.state.occurrence_id or job.id
        renewal = asyncio.create_task(renew(), name=f"schedule-renew:{occurrence_id}")
        run_id: str | None = None
        status = "submitted"
        error: str | None = None
        cancelled = False
        finished = await asyncio.to_thread(self.repository.db_now_ms)
        if job.state.claim_scope == "schedule":
            if (
                job.payload.kind == "agent_monitor"
                and not ignore_active_hours
                and not is_within_active_hours(job.payload.active_hours, finished)
            ):
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                await self._settle_without_run(
                    job,
                    status="skipped_inactive_hours",
                    error="outside configured active hours",
                    enabled_after_run=enabled_after_run,
                )
                return
            lateness = max(0, finished - int(job.state.scheduled_for_ms or finished))
            if (
                job.policy.misfire_policy == "skip"
                and lateness > max(0, job.policy.misfire_grace_ms)
            ):
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                await self._settle_without_run(
                    job,
                    status="skipped_misfire",
                    error=f"late by {lateness}ms",
                    enabled_after_run=enabled_after_run,
                )
                return
            if job.policy.overlap_policy == "skip" and await asyncio.to_thread(
                self.repository.has_active_occurrence,
                job.id,
                exclude_occurrence_id=occurrence_id,
            ):
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                await self._settle_without_run(
                    job,
                    status="skipped_overlap",
                    error="previous occurrence is still active",
                    enabled_after_run=enabled_after_run,
                )
                return
        if (
            job.payload.kind == "agent_monitor"
            and job.payload.preflight_mode == "runtime_attention"
            and job.state.claim_scope == "schedule"
        ):
            preflight = await asyncio.to_thread(
                self.monitors.evaluate_runtime_attention,
                schedule_id=job.id,
                occurrence_id=occurrence_id,
                user_id=job.user_id,
                worker_id=self.worker_id,
                lease_version=job.lease_version,
            )
            if preflight is None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                return
            job.state.monitor_observation_hash = str(preflight["hash"])
            job.state.monitor_observation = dict(preflight["observation"])
            if not preflight["should_run"]:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                await self._settle_without_run(
                    job,
                    status="skipped_unchanged",
                    error=str(preflight["reason"]),
                    enabled_after_run=enabled_after_run,
                )
                return
        if job.payload.kind == "agent_monitor" and job.payload.defer_when_busy:
            target_agent_id = (
                job.agent_id
                if job.agent_id and job.agent_id != "default"
                else self.default_agent_id
            )
            target_session_id = schedule_run_session_id(job)
            busy = await asyncio.to_thread(
                self.repository.has_active_runtime_session,
                user_id=job.user_id,
                agent_id=target_agent_id,
                session_id=target_session_id,
            )
            if busy:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                now_ms = await asyncio.to_thread(self.repository.db_now_ms)
                lateness = max(0, now_ms - int(job.state.scheduled_for_ms or now_ms))
                if (
                    job.policy.misfire_policy == "skip"
                    and lateness > max(0, job.policy.misfire_grace_ms)
                ):
                    await self._settle_without_run(
                        job,
                        status="skipped_busy",
                        error=f"target session remained busy for {lateness}ms",
                        enabled_after_run=enabled_after_run,
                    )
                else:
                    await self._defer_monitor(job, enabled_after_run=enabled_after_run)
                return
        if job.payload.kind == "agent_monitor":
            monitor_context = await asyncio.to_thread(
                self.monitors.freeze_scratch,
                schedule_id=job.id,
                occurrence_id=occurrence_id,
                user_id=job.user_id,
                worker_id=self.worker_id,
                lease_version=job.lease_version,
            )
            if monitor_context is None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                return
            job.state.monitor_scratch_revision = int(
                monitor_context["scratch_revision"]
            )
        prepared = await asyncio.to_thread(
            self.repository.begin_submit, job, worker_id=self.worker_id
        )
        if prepared is None:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            return
        try:
            if self.on_job:
                run_id = await self.on_job(job)
        except asyncio.CancelledError:
            # Worker shutdown must still settle the claimed occurrence so the
            # job does not linger in a claimed state until the lease expires.
            status = "error"
            error = "worker shutdown"
            cancelled = True
        except Exception as exc:
            status = "error"
            error = str(exc)
            logger.exception("Schedule execution failed: {}", job.id)
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)

        finished = await asyncio.to_thread(self.repository.db_now_ms)
        remains_enabled = job.enabled if enabled_after_run is None else enabled_after_run
        # Recompute the next occurrence from the schedule definition.  A
        # one-shot ``at`` job whose time is still in the future (manual "run
        # now" ahead of schedule) keeps that planned occurrence; a due ``at``
        # job yields None and is disabled as consumed.
        next_run = (
            _compute_next_run(job.schedule, finished)
            if remains_enabled and job.state.claim_scope == "schedule"
            else None
        )
        next_attempt_at_ms: int | None = None
        delivery_content: str | None = None
        if (
            status == "error"
            and not cancelled
            and job.state.submit_attempt < max(1, job.policy.max_submit_attempts)
        ):
            status = "retry_wait"
            backoff = min(
                3_600_000,
                max(1_000, job.policy.retry_backoff_ms)
                * (2 ** max(0, job.state.submit_attempt - 1)),
            )
            next_attempt_at_ms = finished + backoff
        elif status == "error" and job.payload.deliver:
            delivery_content = f"定时任务提交失败：{error or 'unknown error'}"
        await asyncio.to_thread(
            self.repository.finish,
            job,
            worker_id=self.worker_id,
            status=status,
            error=error,
            run_id=run_id,
            next_run_at_ms=next_run,
            enabled=remains_enabled and next_run is not None,
            finished_at_ms=finished,
            next_attempt_at_ms=next_attempt_at_ms,
            delivery_content=delivery_content,
        )
        if cancelled:
            raise asyncio.CancelledError(error)

    async def _settle_without_run(
        self,
        job: CronJob,
        *,
        status: str,
        error: str,
        enabled_after_run: bool | None,
    ) -> None:
        finished = await asyncio.to_thread(self.repository.db_now_ms)
        remains_enabled = job.enabled if enabled_after_run is None else enabled_after_run
        next_run = _compute_next_run(job.schedule, finished) if remains_enabled else None
        await asyncio.to_thread(
            self.repository.finish,
            job,
            worker_id=self.worker_id,
            status=status,
            error=error,
            run_id=None,
            next_run_at_ms=next_run,
            enabled=remains_enabled and next_run is not None,
            finished_at_ms=finished,
        )

    async def _defer_monitor(
        self,
        job: CronJob,
        *,
        enabled_after_run: bool | None,
    ) -> None:
        finished = await asyncio.to_thread(self.repository.db_now_ms)
        remains_enabled = job.enabled if enabled_after_run is None else enabled_after_run
        next_run = (
            _compute_next_run(job.schedule, finished)
            if remains_enabled and job.state.claim_scope == "schedule"
            else None
        )
        busy_backoff_ms = min(3_600_000, max(1_000, job.payload.busy_backoff_ms))
        await asyncio.to_thread(
            self.repository.finish,
            job,
            worker_id=self.worker_id,
            status="retry_wait",
            error="target monitor session is busy",
            run_id=None,
            next_run_at_ms=next_run,
            enabled=remains_enabled and next_run is not None,
            finished_at_ms=finished,
            next_attempt_at_ms=finished + busy_backoff_ms,
        )

    def list_runs(
        self, *, user_id: str, job_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self.repository.list_occurrences(
            user_id=user_id, schedule_id=job_id, limit=max(1, min(limit, 500))
        )

    def list_jobs(
        self, include_disabled: bool = False, *, user_id: str | None = None
    ) -> list[CronJob]:
        return self.repository.list(user_id=user_id, include_disabled=include_disabled)

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str = "",
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        agent_id: str | None = None,
        payload_kind: str = "agent_turn",
        user_id: str = "system",
        policy: CronPolicy | None = None,
        session_mode: str = "isolated",
        session_id: str | None = None,
        quiet_token: str = "NO_ACTION",
        defer_when_busy: bool = True,
        busy_backoff_ms: int = 60_000,
        preflight_mode: str = "always",
        context_mode: str = "full",
        active_hours: dict[str, str] | None = None,
    ) -> CronJob:
        _validate_schedule_limits(schedule)
        existing = self.repository.list(user_id=user_id, include_disabled=True)
        if len(existing) >= MAX_JOBS_PER_USER:
            raise ValueError(f"user has reached the scheduled job limit ({MAX_JOBS_PER_USER})")
        now = self.repository.db_now_ms()
        kind = (
            payload_kind
            if payload_kind in {"agent_turn", "system_event", "agent_monitor"}
            else "agent_turn"
        )
        resolved_policy = policy or CronPolicy()
        if kind == "agent_monitor" and policy is None:
            resolved_policy.misfire_policy = "skip"
            resolved_policy.overlap_policy = "skip"
        job = CronJob(
            id=uuid.uuid4().hex,
            name=name,
            user_id=user_id,
            agent_id=agent_id,
            schedule=schedule,
            payload=CronPayload(
                kind=kind,
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
                session_mode=("main" if session_mode == "main" else "isolated"),
                session_id=session_id,
                quiet_token=quiet_token.strip() or "NO_ACTION",
                defer_when_busy=defer_when_busy,
                busy_backoff_ms=min(3_600_000, max(1_000, busy_backoff_ms)),
                preflight_mode=(
                    "runtime_attention"
                    if kind == "agent_monitor" and preflight_mode == "runtime_attention"
                    else "always"
                ),
                context_mode=(
                    "light"
                    if kind == "agent_monitor" and context_mode == "light"
                    else "full"
                ),
                active_hours=(
                    normalize_active_hours(active_hours)
                    if kind == "agent_monitor"
                    else None
                ),
            ),
            policy=resolved_policy,
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        if job.state.next_run_at_ms is None:
            raise ValueError("schedule does not produce a future occurrence")
        return self.repository.create(job)

    def remove_job(self, job_id: str, *, user_id: str | None = None) -> bool:
        return self.repository.delete(job_id, user_id=user_id)

    def get_monitor_scratch(
        self, job_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        return self.monitors.get_state(job_id, user_id=user_id)

    def update_monitor_scratch(self, job_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.monitors.update_scratch(job_id, **kwargs)

    def list_monitor_scratch_revisions(
        self, job_id: str, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]] | None:
        return self.monitors.list_scratch_revisions(job_id, user_id=user_id, limit=limit)

    def monitor_run_context(self, job: CronJob) -> dict[str, Any]:
        if job.payload.kind != "agent_monitor" or not job.state.occurrence_id:
            return {}
        value = self.monitors.occurrence_context(
            job.id,
            job.state.occurrence_id,
            user_id=job.user_id,
        )
        return value or {}

    def enable_job(
        self, job_id: str, enabled: bool = True, *, user_id: str | None = None
    ) -> CronJob | None:
        rows = self.repository.list(user_id=user_id, include_disabled=True)
        current = next((job for job in rows if job.id == job_id), None)
        if current is None:
            return None
        now_ms = self.repository.db_now_ms()
        next_run = _compute_next_run(current.schedule, now_ms) if enabled else None
        return self.repository.set_enabled(
            job_id,
            enabled,
            user_id=user_id,
            next_run_at_ms=next_run,
            now_ms=now_ms,
        )

    async def run_job(
        self, job_id: str, force: bool = False, *, user_id: str | None = None
    ) -> bool:
        current = next(
            (
                job
                for job in self.repository.list(user_id=user_id, include_disabled=True)
                if job.id == job_id
            ),
            None,
        )
        if current is None or (not force and not current.enabled):
            return False
        selected = await asyncio.to_thread(
            self.repository.claim_one,
            job_id,
            worker_id=self.worker_id,
            lease_ms=self.lease_ms,
            manual=True,
        )
        if selected is None:
            return False
        await self._execute_claimed_job(
            selected,
            enabled_after_run=current.enabled,
            ignore_active_hours=True,
        )
        return True

    def status(self, *, user_id: str | None = None) -> dict[str, Any]:
        jobs = self.repository.list(user_id=user_id, include_disabled=True)
        next_runs = [
            job.state.next_run_at_ms for job in jobs if job.enabled and job.state.next_run_at_ms
        ]
        return {
            "enabled": self._running,
            "jobs": len(jobs),
            "next_wake_at_ms": min(next_runs) if next_runs else None,
        }
