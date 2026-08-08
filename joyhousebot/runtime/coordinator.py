"""RuntimeCoordinator for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

from loguru import logger

from joyhousebot.capabilities.dispatcher import capability_result_prompt
from joyhousebot.domain.capabilities import CapabilityRef, InvocationStatus
from joyhousebot.orchestration.planner import ScenarioPlanner
from joyhousebot.orchestration.task_graph import render_value
from joyhousebot.runtime.context import CancellationToken, ToolExecutionContext
from joyhousebot.runtime.graph_finalization import GraphFinalizationMixin
from joyhousebot.runtime.maintenance import (
    _PURGE_INTERVAL_SECONDS,
    RuntimeMaintenanceMixin,
    _env_int,
)
from joyhousebot.runtime.models import (
    AgentEvent,
    AgentUsage,
    EventType,
    RunStatus,
    TaskStatus,
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


class RuntimeCoordinatorMixin(GraphFinalizationMixin, RuntimeMaintenanceMixin):
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
            probe = getattr(self.store, "has_claimable_runtime_task", None)
            if probe is not None and not await asyncio.to_thread(probe):
                return
            self.work_signal.note_activity()
        while not self._closing:
            available = self.task_worker_count - self._graph_active_count - self._graph_task_queue.qsize()
            if available <= 0:
                return
            claim_started = time.monotonic()
            task = await asyncio.to_thread(
                self.store.claim_runtime_task,
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
                await self._execute_claimed_graph_task(task)
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
                    probe = getattr(self.store, "has_incomplete_runtime_work", None)
                    pending = await asyncio.to_thread(probe) if probe is not None else True
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
                heartbeat = getattr(self.store, "heartbeat_runtime_worker", None)
                if heartbeat is not None:
                    await asyncio.to_thread(heartbeat, self.worker_id)
                now = time.monotonic()
                if now - last_worker_reconcile_at >= _WORKER_RECONCILE_INTERVAL_SECONDS:
                    last_worker_reconcile_at = now
                    expire_workers = getattr(self.store, "expire_stale_runtime_workers", None)
                    if expire_workers is not None:
                        await asyncio.to_thread(
                            expire_workers,
                            stale_after_seconds=max(120, self.lease_seconds * 2),
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
        timeout_seconds = _env_int("JOYHOUSEBOT_GRAPH_TIMEOUT_SECONDS", 7200)
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
        records = await asyncio.to_thread(self.store.list_incomplete_runtime_runs)
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
                    }
                ):
                    await self._schedule_record(record.run_id, wake_source=wake_source)
                    active_run_ids.add(record.run_id)
                    available_agent_slots -= 1
                continue
            if not self.scheduler_enabled:
                continue
            if self._graph_deadline_exceeded(record):
                await asyncio.to_thread(self.store.cancel_runtime_tasks, record.run_id)
                await self._finish_error(
                    record.run_id,
                    RunStatus.TIMED_OUT,
                    EventType.RUN_TIMED_OUT,
                    "task graph exceeded the total execution timeout",
                    record.started_at or record.created_at,
                )
                continue
            counts = await asyncio.to_thread(self.store.reconcile_runtime_graph, record.run_id)
            observed_tasks = sum(int(value) for value in counts.values())
            if (
                record.total_task_count > 0
                and observed_tasks >= record.total_task_count
                and not any(counts.get(status, 0) for status in ("queued", "blocked", "running"))
            ):
                if bool(record.options.get("aggregate", True)) and self.agent is None:
                    # A scheduler-only process has no model. Leave aggregation
                    # for an Agent worker; task state is already durable.
                    continue
                await self._try_finalize_graph(record.run_id)

    async def _recover_planning_run(self, record: Any) -> None:
        state = await asyncio.to_thread(
            self.store.get_run_scenario_state,
            record.run_id,
            expected_user_id=record.user_id,
        )
        scenario = (
            await asyncio.to_thread(
                self.store.get_scenario_version,
                state.scenario_id,
                state.scenario_version,
            )
            if state is not None
            else None
        )
        if state is None or scenario is None or state.status != "ready":
            return
        graph = await asyncio.to_thread(
            ScenarioPlanner(self.store).build_graph,
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
            self.store.update_runtime_run, record.run_id, status="queued"
        )
        if queued:
            await asyncio.to_thread(self.store.notify_work, record.run_id)

    async def _execute_claimed_graph_task(self, task: Any) -> None:
        run = await asyncio.to_thread(self.store.get_runtime_run, task.run_id)
        if (
            run is None
            or run.kind != "graph"
            or run.status in {"completed", "failed", "cancelled", "timed_out"}
        ):
            await asyncio.to_thread(
                self.store.update_runtime_task,
                task.task_id,
                status=TaskStatus.CANCELLED.value,
                error={"message": "parent run is not executable"},
                worker_id=self.worker_id,
                lease_version=task.lease_version,
            )
            return

        started = await asyncio.to_thread(self.store.start_runtime_graph, run.run_id)
        if started:
            await self.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    type=EventType.RUN_STARTED.value,
                    data={"kind": "graph", "distributed": True},
                )
            )
            await self._log(
                run.run_id,
                "graph.started",
                "Distributed graph execution started",
            )

        cancellation = CancellationToken()
        owner_task = asyncio.current_task()

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1.0, self.lease_seconds / 3))
                owned = await asyncio.to_thread(
                    self.store.heartbeat_runtime_task,
                    task.task_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    lease_version=task.lease_version,
                )
                if not owned:
                    await self.events.publish(
                        AgentEvent(
                            run_id=run.run_id,
                            task_id=task.task_id,
                            type=EventType.LEASE_LOST.value,
                            worker_id=self.worker_id,
                            lease_version=task.lease_version,
                            data={"reason": "task lease ownership lost"},
                        )
                    )
                    cancellation.cancel("task ownership lost")
                    if owner_task is not None:
                        owner_task.cancel()
                    return

        heartbeat = asyncio.create_task(_heartbeat(), name=f"task-heartbeat:{task.task_id}")
        await self._log(
            run.run_id,
            "task.claimed",
            "Graph task claimed",
            task_id=task.task_id,
            data={"attempt": task.attempt, "lease_version": task.lease_version},
        )
        claim_details = self._task_claim_details.pop(task.task_id, {})
        available_at = _parse_timestamp(task.available_at) or _parse_timestamp(task.created_at)
        if available_at is not None:
            # Include any very short hand-off through the local execution queue.
            claim_details["queue_wait_ms"] = max(
                0, int((time.time() - available_at) * 1000)
            )
        await self.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.TASK_STARTED.value,
                data={
                    "attempt": task.attempt,
                    "name": task.name,
                    "worker_id": self.worker_id,
                    **claim_details,
                },
            )
        )
        capability = (
            CapabilityRef.from_dict(dict(task.payload["capability"]))
            if task.payload.get("capability")
            else None
        )
        capability_id = capability.capability_id if capability else ""
        capability_result = None
        try:
            dependencies = await asyncio.to_thread(
                self.store.get_runtime_task_dependencies, task.task_id
            )
            dependency_context: dict[str, Any] = {}
            for dependency_id in dependencies:
                dependency = await asyncio.to_thread(self.store.get_runtime_task, dependency_id)
                if dependency is not None:
                    key = str(dependency.payload.get("spec_id") or dependency.task_id)
                    dependency_context[key] = (dependency.result or {}).get("content")
            prompt = str(task.payload.get("prompt") or "")
            if dependency_context:
                prompt += (
                    "\n\nContext from dependency tasks:\n"
                    + json.dumps(dependency_context, ensure_ascii=False)[:20000]
                )
            spec_id = str(task.payload.get("spec_id") or task.task_id)
            if capability_id:
                variables = {
                    f"tasks.{key}.content": value for key, value in dependency_context.items()
                }
                capability_input = render_value(
                    dict(task.payload.get("capability_input") or {}), variables
                )
                agent = await self._resolve_execution_agent(run.run_id, task.agent_id)
                registry = getattr(agent, "capabilities", None)
                if registry is None:
                    raise RuntimeError(f"agent has no capability registry: {task.agent_id}")
                scenario_state = await asyncio.to_thread(
                    self.store.get_run_scenario_state,
                    run.run_id,
                    expected_user_id=run.user_id,
                )
                await self.events.publish(
                    AgentEvent(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        type=EventType.CAPABILITY_REQUESTED.value,
                        data={"capability_id": capability_id},
                    )
                )
                await self.events.publish(
                    AgentEvent(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        type=EventType.CAPABILITY_STARTED.value,
                        status="running",
                        data={"capability_id": capability_id},
                    )
                )
                capability_result = await registry.invoke_tool(
                    capability_id,
                    capability_input,
                    version=capability.version,
                    context=ToolExecutionContext(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        root_run_id=run.root_run_id,
                        session_key=f"{run.user_id}:{task.agent_id}:{run.session_id}",
                        session_id=run.session_id,
                        channel="runtime",
                        chat_id=spec_id,
                        user_id=run.user_id,
                        agent_id=task.agent_id,
                        allowed_tools=frozenset({capability_id}),
                        granted_permissions=await self._execution_permissions(
                            run.run_id, task.agent_id
                        ),
                        cancellation=cancellation,
                        worker_id=self.worker_id,
                        metadata={
                            "scenario_id": str(
                                getattr(scenario_state, "scenario_id", "") or ""
                            ),
                            "scenario_version": int(
                                getattr(scenario_state, "scenario_version", 0) or 0
                            ),
                            "scenario_inputs": dict(
                                getattr(scenario_state, "collected_inputs", {}) or {}
                            ),
                        },
                    ),
                    tool_call_id=task.task_id,
                )
                if capability_result.status != InvocationStatus.SUCCEEDED:
                    error = capability_result.error
                    raise RuntimeError(error.message if error else capability_result.summary)
                content = capability_result_prompt(capability_result)
                tools = [capability_id]
                usage = AgentUsage()
                await self.events.publish(
                    AgentEvent(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        type=EventType.CAPABILITY_COMPLETED.value,
                        status="completed",
                        data={
                            "capability_id": capability_id,
                            "invocation_id": capability_result.invocation_id,
                            "summary": capability_result.summary,
                        },
                    )
                )
            else:
                content, tools, usage = await self._call_agent(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    prompt=prompt,
                    user_id=run.user_id,
                    session_id=f"{run.session_id}:task:{spec_id}",
                    agent_id=task.agent_id,
                    channel="runtime",
                    chat_id=spec_id,
                    model=None,
                    system_prompt=None,
                    output_schema=(
                        dict(task.payload["output_schema"])
                        if task.payload.get("output_schema")
                        else None
                    ),
                    timeout_seconds=float(task.payload.get("timeout_seconds") or 300),
                    max_turns=None,
                    max_input_tokens=None,
                    max_output_tokens=None,
                    max_cost_usd=None,
                    permission_mode="default",
                    allowed_tools=[str(item) for item in task.payload.get("allowed_tools") or []],
                    disallowed_tools=[],
                    cancellation=cancellation,
                    metadata={
                        **dict(task.payload.get("metadata") or {}),
                        "skill_names": list(task.payload.get("skill_names") or []),
                    },
                )
            value = {
                "status": "completed",
                "content": content,
                "tools_used": tools,
                "usage": usage.to_dict(),
                "capability_result": (
                    capability_result.to_dict() if capability_result is not None else None
                ),
            }
            await asyncio.to_thread(
                self.store.add_runtime_artifact,
                artifact_id=f"{task.task_id}:output",
                run_id=run.run_id,
                task_id=task.task_id,
                name=f"{task.name}-output",
                media_type="text/plain",
                content=content,
            )
            saved = await asyncio.to_thread(
                self.store.update_runtime_task,
                task.task_id,
                status=TaskStatus.COMPLETED.value,
                result=value,
                worker_id=self.worker_id,
                lease_version=task.lease_version,
            )
            if not saved:
                raise asyncio.CancelledError("task completion fenced by a newer lease")
            await self.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    task_id=task.task_id,
                    type=EventType.TASK_COMPLETED.value,
                    data=value,
                )
            )
            await self._log(
                run.run_id,
                "task.completed",
                "Graph task completed",
                task_id=task.task_id,
                data={"attempt": task.attempt, "usage": usage.to_dict()},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retry = task.attempt < task.max_attempts
            if capability_id:
                await self.events.publish(
                    AgentEvent(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        type=EventType.CAPABILITY_FAILED.value,
                        status="failed",
                        data={
                            "capability_id": capability_id,
                            "invocation_id": (
                                capability_result.invocation_id
                                if capability_result is not None
                                else None
                            ),
                            "error": str(exc),
                            "retry": retry,
                        },
                    )
                )
            status = TaskStatus.QUEUED.value if retry else TaskStatus.FAILED.value
            saved = await asyncio.to_thread(
                self.store.update_runtime_task,
                task.task_id,
                status=status,
                error={"message": str(exc)},
                retry_delay_seconds=min(30.0, 2 ** max(0, task.attempt - 1)) if retry else None,
                worker_id=self.worker_id,
                lease_version=task.lease_version,
            )
            if saved:
                event_type = EventType.TASK_QUEUED if retry else EventType.TASK_FAILED
                await self.events.publish(
                    AgentEvent(
                        run_id=run.run_id,
                        task_id=task.task_id,
                        type=event_type.value,
                        data={"attempt": task.attempt, "error": str(exc), "retry": retry},
                    )
                )
                await self._log(
                    run.run_id,
                    "task.retry" if retry else "task.failed",
                    str(exc),
                    level="warning" if retry else "error",
                    task_id=task.task_id,
                    data={"attempt": task.attempt, "retry": retry},
                )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        counts = await asyncio.to_thread(self.store.reconcile_runtime_graph, run.run_id)
        if bool(run.options.get("fail_fast")) and counts.get("failed", 0):
            await asyncio.to_thread(self.store.cancel_runtime_tasks, run.run_id)
            counts = await asyncio.to_thread(self.store.reconcile_runtime_graph, run.run_id)
        await self._publish_graph_progress(run.run_id, counts=counts)
        await self._log(
            run.run_id,
            "graph.reconciled",
            "Graph dependency state reconciled",
            data={"counts": counts},
        )
        if not any(counts.get(status, 0) for status in ("queued", "blocked", "running")):
            await self._try_finalize_graph(run.run_id)

    async def _publish_graph_progress(
        self, run_id: str, *, counts: dict[str, int] | None = None
    ) -> None:
        """Publish exact progress only when the task graph has a known total."""

        tasks = await asyncio.to_thread(self.store.list_runtime_tasks, run_id=run_id, limit=5000)
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
