"""Submission for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict, replace
from typing import Any
from uuid import uuid4

from joyhousebot.orchestration.task_graph import graph_task_id, validate_and_order_graph
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.graph_materialization import GraphMaterializationMixin
from joyhousebot.runtime.models import (
    AgentEvent,
    AgentOptions,
    AgentResult,
    EventType,
    RunStatus,
    TaskGraphSpec,
)
from joyhousebot.runtime.tracking import (
    append_trace_event_async,
    ensure_tracking_ids,
    get_request_tracking,
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


class SubmissionMixin(GraphMaterializationMixin):
    async def submit_run(
        self,
        options: AgentOptions,
        *,
        run_id: str | None = None,
        initial_status: str = "queued",
    ) -> Any:
        await self.start()
        inherited_tracking = get_request_tracking()
        request_id, tracker_id = ensure_tracking_ids(
            request_id=options.request_id
            or (inherited_tracking.request_id if inherited_tracking else None),
            tracker_id=options.tracker_id
            or (inherited_tracking.tracker_id if inherited_tracking else None),
        )
        options = replace(
            options,
            request_id=request_id,
            tracker_id=tracker_id,
            parent_request_id=(
                options.parent_request_id
                or (inherited_tracking.parent_request_id if inherited_tracking else None)
            ),
            metadata={
                **options.metadata,
                "request_id": request_id,
                "tracker_id": tracker_id,
                # A distributed worker may observe the new run immediately.
                # Store claim predicates use this marker to wait until the
                # canonical accepted/queued event prefix has committed.
                "_runtime_initial_events_required": True,
            },
        )
        if options.agent_id == "default" and self.default_agent_id != "default":
            options = replace(options, agent_id=self.default_agent_id)
        if not options.prompt.strip():
            raise ValueError("prompt is required")
        for name, value in (
            ("user_id", options.user_id),
            ("session_id", options.session_id),
            ("agent_id", options.agent_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        profile = await asyncio.to_thread(self.store.get_agent_profile, options.agent_id)
        if profile is None:
            raise ValueError(f"active published Agent not found: {options.agent_id}")
        if options.agent_id != profile.definition.agent_id:
            options = replace(options, agent_id=profile.definition.agent_id)
        if options.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        # Child runs spawned by the runtime itself (subagents, graph tasks)
        # stay exempt: their fan-out is already bounded by the parent run.
        top_level = not (
            options.root_run_id or options.parent_run_id or options.parent_task_id
        )
        for name, value in (
            ("max_turns", options.max_turns),
            ("max_input_tokens", options.max_input_tokens),
            ("max_output_tokens", options.max_output_tokens),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if options.max_cost_usd is not None and options.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be greater than zero")
        for field_name, referenced_run_id in (
            ("root_run_id", options.root_run_id),
            ("parent_run_id", options.parent_run_id),
        ):
            if not referenced_run_id:
                continue
            referenced = await asyncio.to_thread(self.store.get_runtime_run, referenced_run_id)
            if referenced is None or referenced.user_id != options.user_id:
                raise ValueError(f"{field_name} does not belong to user_id")
        if options.parent_task_id:
            parent_task = await asyncio.to_thread(
                self.store.get_runtime_task, options.parent_task_id
            )
            parent_run = (
                await asyncio.to_thread(self.store.get_runtime_run, parent_task.run_id)
                if parent_task is not None
                else None
            )
            if parent_run is None or parent_run.user_id != options.user_id:
                raise ValueError("parent_task_id does not belong to user_id")
        run_id = run_id or uuid4().hex
        record, created = await asyncio.to_thread(
            self.store.create_runtime_run,
            run_id=run_id,
            user_id=options.user_id,
            session_id=options.session_id,
            agent_id=options.agent_id,
            kind="agent",
            prompt=options.prompt,
            options=options.to_dict(),
            idempotency_key=options.idempotency_key,
            root_run_id=options.root_run_id,
            parent_run_id=options.parent_run_id,
            parent_task_id=options.parent_task_id,
            initial_status=initial_status,
            max_children_per_root=options.max_children_per_root,
            max_active_per_user=(
                _env_int("JOYHOUSEBOT_MAX_RUNS_PER_USER", 4) if top_level else None
            ),
            max_submissions_per_minute=(
                _env_int("JOYHOUSEBOT_RUN_SUBMIT_PER_MINUTE", 30) if top_level else None
            ),
        )
        if created:
            snapshot = await asyncio.to_thread(
                self.store.create_run_execution_snapshot,
                record.run_id,
                options.agent_id,
            )
            await append_trace_event_async(
                store=self.store,
                tracker_id=tracker_id,
                request_id=request_id,
                parent_request_id=options.parent_request_id,
                user_id=record.user_id,
                run_id=record.run_id,
                transport="runtime",
                direction="internal",
                operation="agent.run",
                stage="queued",
                status=record.status,
                data={
                    "agent_id": record.agent_id,
                    "agent_revision_id": snapshot.agent_revision_id,
                    "session_id": record.session_id,
                },
            )
            await self._log(
                record.run_id,
                "run.queued" if initial_status == "queued" else "run.waiting_input",
                "Agent run queued" if initial_status == "queued" else "Run awaits user input",
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.RUN_ACCEPTED.value,
                    status=record.status,
                    data={"kind": "agent"},
                )
            )
            if initial_status == "queued":
                await self.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        type=EventType.RUN_QUEUED.value,
                        status=RunStatus.QUEUED.value,
                        data={
                            "user_id": record.user_id,
                            "session_id": record.session_id,
                            "agent_id": record.agent_id,
                            "kind": "agent",
                        },
                    )
                )
        if record.status in {"queued", "running"}:
            if self.worker_enabled and not await self.supervisor.is_active(record.run_id):
                await self._schedule_record(record.run_id)
            else:
                await asyncio.to_thread(self.store.notify_work, record.run_id)
        return await asyncio.to_thread(self.store.get_runtime_run, record.run_id)

    async def submit_graph(
        self,
        spec: TaskGraphSpec,
        *,
        run_id: str | None = None,
    ) -> Any:
        await self.start()
        inherited_tracking = get_request_tracking()
        request_id, tracker_id = ensure_tracking_ids(
            request_id=spec.request_id
            or (inherited_tracking.request_id if inherited_tracking else None),
            tracker_id=spec.tracker_id
            or (inherited_tracking.tracker_id if inherited_tracking else None),
        )
        spec = replace(
            spec,
            request_id=request_id,
            tracker_id=tracker_id,
            parent_request_id=(
                spec.parent_request_id
                or (inherited_tracking.parent_request_id if inherited_tracking else None)
            ),
        )
        if spec.agent_id == "default" and self.default_agent_id != "default":
            spec = replace(spec, agent_id=self.default_agent_id)
        for name, value in (
            ("user_id", spec.user_id),
            ("session_id", spec.session_id),
            ("agent_id", spec.agent_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        profile = await asyncio.to_thread(self.store.get_agent_profile, spec.agent_id)
        if profile is None:
            raise ValueError(f"active published Agent not found: {spec.agent_id}")
        if spec.agent_id != profile.definition.agent_id:
            spec = replace(spec, agent_id=profile.definition.agent_id)
        ordered = validate_and_order_graph(spec.tasks)
        max_active_per_user = _env_int("JOYHOUSEBOT_MAX_RUNS_PER_USER", 4)
        max_submissions_per_minute = _env_int("JOYHOUSEBOT_RUN_SUBMIT_PER_MINUTE", 30)
        run_id = run_id or uuid4().hex
        options = {
            "goal": spec.goal,
            "user_id": spec.user_id,
            "session_id": spec.session_id,
            "agent_id": spec.agent_id,
            "max_concurrent": max(1, spec.max_concurrent),
            "fail_fast": spec.fail_fast,
            "aggregate": spec.aggregate,
            "aggregation_policy": dict(spec.aggregation_policy),
            "idempotency_key": spec.idempotency_key,
            "request_id": request_id,
            "tracker_id": tracker_id,
            "parent_request_id": spec.parent_request_id,
            "metadata": {**spec.metadata, "_runtime_initial_events_required": True},
            "tasks": [asdict(task) for task in ordered],
        }
        graph_rows = [
            {
                "task_id": graph_task_id(run_id, task.id),
                "agent_id": task.agent_id or spec.agent_id,
                "name": task.name or task.id,
                "payload": {
                    "spec_id": task.id,
                    "agent_id": task.agent_id or spec.agent_id,
                    "prompt": task.prompt,
                    "metadata": task.metadata,
                    "timeout_seconds": task.timeout_seconds,
                    "capability_id": task.capability_id,
                    "capability_input": task.capability_input,
                    "output_schema": task.output_schema,
                    "allowed_tools": task.allowed_tools,
                    "skill_names": task.skill_names,
                },
                "dependencies": [
                    graph_task_id(run_id, dependency) for dependency in task.dependencies
                ],
                "priority": index,
                "max_attempts": task.max_attempts,
            }
            for index, task in enumerate(ordered)
        ]
        create_graph = getattr(self.store, "create_runtime_graph", None)
        if create_graph is not None:
            record, created = await asyncio.to_thread(
                create_graph,
                run_id=run_id,
                user_id=spec.user_id,
                session_id=spec.session_id,
                agent_id=spec.agent_id,
                prompt=spec.goal,
                options=options,
                tasks=graph_rows,
                idempotency_key=spec.idempotency_key,
                max_active_per_user=max_active_per_user,
                max_submissions_per_minute=max_submissions_per_minute,
            )
        else:
            record, created = await asyncio.to_thread(
                self.store.create_runtime_run,
                run_id=run_id,
                user_id=spec.user_id,
                session_id=spec.session_id,
                agent_id=spec.agent_id,
                kind="graph",
                prompt=spec.goal,
                options=options,
                idempotency_key=spec.idempotency_key,
                total_task_count=len(graph_rows),
                max_active_per_user=max_active_per_user,
                max_submissions_per_minute=max_submissions_per_minute,
            )
        if created:
            snapshot = await asyncio.to_thread(
                self.store.create_run_execution_snapshot,
                record.run_id,
                spec.agent_id,
            )
            await append_trace_event_async(
                store=self.store,
                tracker_id=tracker_id,
                request_id=request_id,
                parent_request_id=spec.parent_request_id,
                user_id=record.user_id,
                run_id=record.run_id,
                transport="runtime",
                direction="internal",
                operation="task_graph.run",
                stage="queued",
                status=record.status,
                data={
                    "agent_id": record.agent_id,
                    "agent_revision_id": snapshot.agent_revision_id,
                    "task_count": len(ordered),
                },
            )
            for task_row in graph_rows if create_graph is None else []:
                await asyncio.to_thread(
                    self.store.create_runtime_task,
                    task_id=task_row["task_id"],
                    run_id=record.run_id,
                    agent_id=task_row["agent_id"],
                    name=task_row["name"],
                    payload=task_row["payload"],
                    dependencies=task_row["dependencies"],
                    priority=task_row["priority"],
                    max_attempts=task_row["max_attempts"],
                )
            await self._log(
                record.run_id,
                "graph.queued",
                "Task graph persisted and ready for distributed execution",
                data={"task_count": len(ordered)},
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.RUN_ACCEPTED.value,
                    status=RunStatus.QUEUED.value,
                    data={"kind": "graph", "task_count": len(ordered)},
                )
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.RUN_QUEUED.value,
                    status=RunStatus.QUEUED.value,
                    data={
                        "user_id": record.user_id,
                        "session_id": record.session_id,
                        "agent_id": record.agent_id,
                        "kind": "graph",
                        "task_count": len(ordered),
                    },
                )
            )
            await self.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.PLAN_CREATED.value,
                    phase="planning",
                    data={
                        "goal": spec.goal,
                        "steps": [
                            {
                                "task_id": graph_task_id(record.run_id, task.id),
                                "name": task.name or task.id,
                                "agent_id": task.agent_id or spec.agent_id,
                                "dependencies": task.dependencies,
                            }
                            for task in ordered
                        ],
                    },
                )
            )
            for task in ordered:
                await self.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        task_id=graph_task_id(record.run_id, task.id),
                        type=EventType.TASK_QUEUED.value,
                        data={"name": task.name or task.id, "dependencies": task.dependencies},
                    )
                )
        await asyncio.to_thread(self.store.notify_work, record.run_id)
        # Give an already-running worker a scheduling opportunity before an
        # accepted response is observed; execution itself remains asynchronous.
        await asyncio.sleep(0)
        return await asyncio.to_thread(self.store.get_runtime_run, record.run_id)

    async def _schedule_record(self, run_id: str, *, wake_source: str = "local") -> None:
        existing = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if existing is not None and existing.kind == "graph":
            await asyncio.to_thread(self.store.notify_work, run_id)
            return

        async def _factory(cancellation: CancellationToken) -> AgentResult:
            claim_started = time.monotonic()
            record = await asyncio.to_thread(
                self.store.claim_runtime_run,
                run_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if record is None:
                return None
            created_at = _timestamp_seconds(record.created_at)
            self._run_claim_details[run_id] = {
                "wake_source": wake_source,
                "queue_wait_ms": (
                    max(0, int((time.time() - created_at) * 1000))
                    if created_at is not None
                    else None
                ),
                "claim_latency_ms": int((time.monotonic() - claim_started) * 1000),
            }
            owner_task = asyncio.current_task()

            async def _heartbeat() -> None:
                while True:
                    await asyncio.sleep(max(1.0, self.lease_seconds / 3))
                    owned = await asyncio.to_thread(
                        self.store.heartbeat_runtime_run,
                        run_id,
                        worker_id=self.worker_id,
                        lease_seconds=self.lease_seconds,
                        lease_version=record.lease_version,
                    )
                    if not owned:
                        await self.events.publish(
                            AgentEvent(
                                run_id=run_id,
                                type=EventType.LEASE_LOST.value,
                                worker_id=self.worker_id,
                                lease_version=record.lease_version,
                                data={"reason": "run lease ownership lost"},
                            )
                        )
                        cancellation.cancel("run ownership lost")
                        if owner_task is not None:
                            owner_task.cancel()
                        return

            heartbeat = asyncio.create_task(_heartbeat(), name=f"run-heartbeat:{run_id}")
            try:
                try:
                    return await self._execute_agent_record(record, cancellation)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    return await self._finish_error(
                        record.run_id,
                        RunStatus.FAILED,
                        EventType.RUN_FAILED,
                        str(exc),
                        record.started_at or record.created_at,
                        worker_id=self.worker_id,
                        lease_version=record.lease_version,
                    )
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

        await self.supervisor.submit(run_id, _factory)


def _timestamp_seconds(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
