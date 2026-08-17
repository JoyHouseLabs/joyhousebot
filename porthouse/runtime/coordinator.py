"""RuntimeCoordinator for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from loguru import logger

from porthouse.observability.otel import telemetry_span
from porthouse.orchestration.planner import ScenarioPlanner
from porthouse.runtime.graph_finalization import GraphFinalizationMixin
from porthouse.runtime.graph_saga_execution import reconcile_graph_saga
from porthouse.runtime.graph_task_execution import GraphTaskExecutionMixin
from porthouse.runtime.maintenance import (
    _PURGE_INTERVAL_SECONDS,
    RuntimeMaintenanceMixin,
    _env_int,
)
from porthouse.runtime.models import (
    AgentEvent,
    EventType,
    RunStatus,
)

# Exceptions in the background loops are logged at most once per minute per
# loop so a persistent failure cannot flood the log.
_LOOP_ERROR_LOG_INTERVAL_SECONDS = 60.0
# A worker row is a leased presence record. Reconcile abandoned rows promptly
# after deployments or abrupt process termination without polling the database
# on every work notification.
_WORKER_RECONCILE_INTERVAL_SECONDS = 30.0
# Idle poll wakes only run a cheap EXISTS probe; a full fair-queue scan still
# runs at this cadence as the safety net for missed notifications.
_IDLE_DEEP_SCAN_INTERVAL_SECONDS = 30.0


def _parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class RuntimeCoordinatorMixin(
    GraphTaskExecutionMixin, GraphFinalizationMixin, RuntimeMaintenanceMixin
):
    def _log_loop_exception(self, loop_name: str) -> None:
        """Log a background-loop exception, rate limited to one per minute."""
        now = time.monotonic()
        logged_at = getattr(self, "_loop_error_logged_at", None)
        if logged_at is None:
            logged_at = self._loop_error_logged_at = {}
        last = logged_at.get(loop_name, 0.0)
        if now - last >= _LOOP_ERROR_LOG_INTERVAL_SECONDS:
            logged_at[loop_name] = now
            logger.exception("Runtime {} loop failed; retrying", loop_name)

    async def _task_dispatcher_loop(self) -> None:
        """Claim only enough durable Tasks to fill local execution slots."""
        generation = 0
        while not self._closing:
            try:
                wake = await self.work_signal.wait(generation)
                generation = wake.generation
                await self._dispatch_ready_graph_tasks(wake.source)
            except asyncio.CancelledError:
                if self._closing:
                    raise
                await asyncio.sleep(0)
            except Exception:
                self._log_loop_exception("task-dispatcher")
                await asyncio.sleep(1.0)

    async def _dispatch_ready_graph_tasks(self, wake_source: str) -> None:
        """Fill currently idle slots; only this dispatcher performs task claims."""
        if wake_source == "poll":
            # Idle fallback wake: probe before paying for the claim CTE and
            # the lease sweeps.  NOTIFY/local wakes go straight to claiming.
            if not await asyncio.to_thread(
                self.stores.tasks.has_claimable_runtime_task
            ):
                return
            self.work_signal.note_activity()
        while not self._closing:
            available = self.task_worker_count - self._graph_active_count - self._graph_task_queue.qsize()
            if available <= 0:
                return
            claim_started = time.monotonic()
            task = await asyncio.to_thread(
                self.stores.tasks.claim_runtime_task,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            claim_latency_ms = int((time.monotonic() - claim_started) * 1000)
            if task is None:
                return
            available_at = _parse_timestamp(task.available_at) or _parse_timestamp(task.created_at)
            queue_wait_ms = (
                max(0, int((time.time() - available_at) * 1000))
                if available_at is not None
                else None
            )
            self._task_claim_details[task.task_id] = {
                "wake_source": wake_source,
                "queue_wait_ms": queue_wait_ms,
                "claim_latency_ms": claim_latency_ms,
            }
            await self._graph_task_queue.put(task)

    async def _graph_executor_loop(self, index: int) -> None:
        """Execute already-claimed Tasks without contending for PG notifications."""
        del index
        while not self._closing:
            task = await self._graph_task_queue.get()
            self._graph_active_count += 1
            try:
                run = await asyncio.to_thread(
                    self.stores.runs.get_runtime_run, task.run_id
                )
                options = dict(run.options or {}) if run is not None else {}
                with telemetry_span(
                    "porthouse.graph_task.execute",
                    carrier={
                        key: str(options[key])
                        for key in ("traceparent", "tracestate")
                        if options.get(key)
                    },
                    attributes={
                        "porthouse.run_id": task.run_id,
                        "porthouse.task_id": task.task_id,
                        "porthouse.agent_id": task.agent_id,
                        "porthouse.worker_id": self.worker_id,
                    },
                ):
                    await self._execute_claimed_graph_task(task)
            except asyncio.CancelledError:
                if self._closing:
                    raise
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
            finally:
                self._graph_active_count -= 1
                self._graph_task_queue.task_done()
                await self.work_signal.signal("local")

    async def _runtime_coordinator_loop(self) -> None:
        """Continuously recover queued runs and maintain worker presence."""
        last_purge_at = 0.0
        last_worker_reconcile_at = 0.0
        last_deep_scan_at = time.monotonic()
        generation = 0
        while not self._closing:
            try:
                wake = await self.work_signal.wait(generation)
                generation = wake.generation
                now = time.monotonic()
                if wake.source == "poll":
                    # Idle fallback wake: run the expensive fair-queue scan
                    # only when the cheap probe sees pending work, or when the
                    # deep-scan safety net comes due for missed notifications.
                    pending = await asyncio.to_thread(
                        self.stores.tasks.has_incomplete_runtime_work
                    )
                    if pending:
                        self.work_signal.note_activity()
                        await self._scan_incomplete_runs(wake_source=wake.source)
                        last_deep_scan_at = now
                    elif now - last_deep_scan_at >= _IDLE_DEEP_SCAN_INTERVAL_SECONDS:
                        await self._scan_incomplete_runs(wake_source="recovery")
                        last_deep_scan_at = now
                else:
                    await self._scan_incomplete_runs(wake_source=wake.source)
                    last_deep_scan_at = now
                await self._heartbeat_worker()
                now = time.monotonic()
                if now - last_worker_reconcile_at >= _WORKER_RECONCILE_INTERVAL_SECONDS:
                    last_worker_reconcile_at = now
                    await asyncio.to_thread(
                        self.stores.workers.expire_stale_runtime_workers,
                        stale_after_seconds=max(120, self.lease_seconds * 2),
                    )
                    if self.maintenance_enabled:
                        await asyncio.to_thread(
                            self.stores.maintenance.reconcile_configuration_rollouts
                        )
                if self.maintenance_enabled and now - last_purge_at >= _PURGE_INTERVAL_SECONDS:
                    last_purge_at = now
                    await self._purge_old_runtime_data()
            except asyncio.CancelledError:
                if self._closing:
                    raise
                await asyncio.sleep(0)
            except Exception:
                self._log_loop_exception("coordinator")
                await asyncio.sleep(1.0)

    def _graph_deadline_exceeded(self, record: Any) -> bool:
        """A graph run may not exceed the graph-level total timeout."""
        if record.status not in {RunStatus.QUEUED.value, RunStatus.RUNNING.value}:
            return False
        timeout_seconds = _env_int("PORTHOUSE_GRAPH_TIMEOUT_SECONDS", 7200)
        started = _parse_timestamp(record.started_at or record.created_at)
        if started is None:
            return False
        return time.time() - started > timeout_seconds

    async def _scan_incomplete_runs(self, *, wake_source: str = "recovery") -> None:
        """Recover queued agent runs and reconcile graphs while this worker stays alive.

        A top-level run can fail its first claim when another run for the same
        conversation owns the session lease.  Startup-only recovery would leave
        that run queued forever, so the worker continuously gives queued agent
        runs another scheduling opportunity.
        """
        records = await asyncio.to_thread(
            self.stores.runs.list_incomplete_runtime_runs
        )
        active_run_ids = set(await self.supervisor.active_run_ids())
        available_agent_slots = (
            max(0, self.max_concurrent_runs - len(active_run_ids))
            if self.max_concurrent_runs is not None
            else 256
        )
        for record in records:
            if record.cancel_requested_at is not None:
                await self._finish_cancel_requested_run(record)
                continue
            if record.status == RunStatus.PLANNING.value:
                await self._recover_planning_run(record)
                continue
            if record.kind != "graph":
                if (
                    self.worker_enabled
                    and available_agent_slots > 0
                    and record.run_id not in active_run_ids
                    and record.status
                    in {
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.WAITING_EXTERNAL.value,
                    }
                ):
                    await self._schedule_record(record.run_id, wake_source=wake_source)
                    active_run_ids.add(record.run_id)
                    available_agent_slots -= 1
                continue
            if not self.scheduler_enabled:
                continue
            expired_waits = await asyncio.to_thread(
                self.stores.graphs.expire_due_graph_event_waits,
                run_id=record.run_id,
                limit=128,
            )
            for wait in expired_waits:
                await self.events.publish(
                    AgentEvent(
                        run_id=wait.run_id,
                        task_id=wait.task_id,
                        type=EventType.EVENT_EXPIRED.value,
                        status="expired",
                        data={
                            "wait_id": wait.wait_id,
                            "event_type": wait.event_type,
                            "deadline_at": wait.deadline_at,
                        },
                    )
                )
                await self.events.publish(
                    AgentEvent(
                        run_id=wait.run_id,
                        task_id=wait.task_id,
                        type=EventType.TASK_FAILED.value,
                        status="failed",
                        data={"reason": "event_deadline_expired"},
                    )
                )
            if self._graph_deadline_exceeded(record):
                await asyncio.to_thread(
                    self.stores.tasks.cancel_runtime_tasks, record.run_id
                )
                await self._finish_error(
                    record.run_id,
                    RunStatus.TIMED_OUT,
                    EventType.RUN_TIMED_OUT,
                    "task graph exceeded the total execution timeout",
                    record.started_at or record.created_at,
                )
                continue
            counts = await asyncio.to_thread(
                self.stores.graphs.reconcile_runtime_graph, record.run_id
            )
            if dict(record.options.get("failure_policy") or {}).get("mode") == "saga":
                await reconcile_graph_saga(self, record)
                counts = await asyncio.to_thread(
                    self.stores.graphs.reconcile_runtime_graph, record.run_id
                )
            observed_tasks = sum(int(value) for value in counts.values())
            if (
                record.total_task_count > 0
                and observed_tasks >= record.total_task_count
                and not any(
                    counts.get(status, 0)
                    for status in (
                        "queued",
                        "blocked",
                        "running",
                        "waiting_approval",
                        "waiting_external",
                    )
                )
            ):
                if bool(record.options.get("aggregate", True)) and self.agent is None:
                    # A scheduler-only process has no model. Leave aggregation
                    # for an Agent worker; task state is already durable.
                    continue
                await self._try_finalize_graph(record.run_id)

    async def _recover_planning_run(self, record: Any) -> None:
        state = await asyncio.to_thread(
            self.stores.scenarios.get_run_scenario_state,
            record.run_id,
            expected_user_id=record.user_id,
        )
        scenario = (
            await asyncio.to_thread(
                self.stores.scenarios.get_scenario_version,
                state.scenario_id,
                state.scenario_version,
            )
            if state is not None
            else None
        )
        if state is None or scenario is None or state.status != "ready":
            return
        graph = await asyncio.to_thread(
            ScenarioPlanner(self.stores.catalog).build_graph,
            scenario,
            goal=record.prompt,
            inputs=state.collected_inputs,
            user_id=record.user_id,
            session_id=record.session_id,
            agent_id=record.agent_id,
            idempotency_key=record.idempotency_key,
            request_id=str(record.options.get("request_id") or f"req_{record.run_id}"),
        )
        if graph is not None:
            await self.materialize_graph(record.run_id, graph)
            return
        queued = await asyncio.to_thread(
            self.stores.runs.update_runtime_run, record.run_id, status="queued"
        )
        if queued:
            await asyncio.to_thread(self.stores.workers.notify_work, record.run_id)


    async def _publish_graph_progress(
        self, run_id: str, *, counts: dict[str, int] | None = None
    ) -> None:
        """Publish exact progress only when the task graph has a known total."""

        tasks = await asyncio.to_thread(
            self.stores.tasks.list_runtime_tasks, run_id=run_id, limit=5000
        )
        terminal = {"completed", "failed", "cancelled", "timed_out", "skipped"}
        completed = sum(1 for task in tasks if task.status in terminal)
        await self.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.TASK_PROGRESS.value,
                data={
                    "completed": completed,
                    "total": len(tasks),
                    "exact": True,
                    "counts": counts or {},
                },
            )
        )
