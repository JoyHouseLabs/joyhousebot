"""Durable schedule service backed by normalized repository tables."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from joyhousebot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
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
    ) -> None:
        self.on_job = on_job
        self.runtime_store = runtime_store
        self.repository = ScheduleRepository(runtime_store)
        self.worker_id = worker_id or f"scheduler-{uuid.uuid4().hex}"
        self.lease_ms = max(10_000, lease_ms)
        self.poll_seconds = max(0.1, poll_seconds)
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
            claimed = await asyncio.to_thread(self._claim_due_jobs)
            if claimed:
                await asyncio.gather(*(self._execute_claimed_job(job) for job in claimed))
                continue
            await asyncio.sleep(self.poll_seconds)

    def _claim_due_jobs(self) -> list[CronJob]:
        return self.repository.claim_due(
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
    ) -> None:
        async def renew() -> None:
            while True:
                await asyncio.sleep(max(1.0, self.lease_ms / 3000))
                if not await asyncio.to_thread(self._renew_claimed_job, job):
                    return

        renewal = asyncio.create_task(renew(), name=f"schedule-renew:{job.id}")
        run_id: str | None = None
        status = "ok"
        error: str | None = None
        cancelled = False
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
        next_run = _compute_next_run(job.schedule, finished) if remains_enabled else None
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
        )
        if cancelled:
            raise asyncio.CancelledError(error)

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
    ) -> CronJob:
        _validate_schedule_limits(schedule)
        existing = self.repository.list(user_id=user_id, include_disabled=True)
        if len(existing) >= MAX_JOBS_PER_USER:
            raise ValueError(f"user has reached the scheduled job limit ({MAX_JOBS_PER_USER})")
        now = self.repository.db_now_ms()
        kind = payload_kind if payload_kind in {"agent_turn", "system_event"} else "agent_turn"
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
            ),
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
        await self._execute_claimed_job(selected, enabled_after_run=current.enabled)
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
