"""Unified native Agent and DAG runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from joyhousebot.runtime.agent_execution import AgentExecutionMixin
from joyhousebot.runtime.controls import RuntimeControlsMixin
from joyhousebot.runtime.coordinator import RuntimeCoordinatorMixin
from joyhousebot.runtime.events import EventBroker
from joyhousebot.runtime.narrative import redact_runtime_value
from joyhousebot.runtime.request_coordination import RequestCoordinationMixin
from joyhousebot.runtime.submission import SubmissionMixin
from joyhousebot.runtime.supervisor import TaskSupervisor
from joyhousebot.runtime.work_signal import RuntimeWorkSignal


class NativeAgentRuntime(
    SubmissionMixin,
    AgentExecutionMixin,
    RuntimeCoordinatorMixin,
    RequestCoordinationMixin,
    RuntimeControlsMixin,
):
    """Own durable lifecycle, events, cancellation, and multi-task execution."""

    def __init__(
        self,
        *,
        agent: Any,
        agent_resolver: Callable[[str], Any | None] | None = None,
        store: Any,
        max_concurrent_runs: int | None = None,
        lease_seconds: int = 30,
        worker_enabled: bool = True,
        scheduler_enabled: bool = True,
        maintenance_enabled: bool = False,
        presence_enabled: bool | None = None,
        worker_name: str = "runtime",
        capabilities: dict[str, Any] | None = None,
        plugin_releases: list[dict[str, Any]] | None = None,
        default_agent_id: str = "default",
        poll_interval_seconds: float = 0.2,
        monitor_reconciler: Callable[..., Any] | None = None,
    ) -> None:
        self.agent = agent
        self.agent_resolver = agent_resolver
        self.store = store
        self.events = EventBroker(store)
        self.supervisor = TaskSupervisor(max_concurrent=max_concurrent_runs)
        self.max_concurrent_runs = (
            int(max_concurrent_runs)
            if isinstance(max_concurrent_runs, int) and max_concurrent_runs > 0
            else None
        )
        self.worker_id = f"{worker_name or 'runtime'}-{uuid4().hex}"
        self.lease_seconds = max(5, int(lease_seconds))
        self.worker_enabled = worker_enabled
        self.scheduler_enabled = scheduler_enabled
        self.maintenance_enabled = maintenance_enabled
        self.presence_enabled = (
            bool(worker_enabled or scheduler_enabled)
            if presence_enabled is None
            else bool(presence_enabled)
        )
        self.capabilities = capabilities or {"agent": True, "graph_task": True}
        self.plugin_releases = [dict(item) for item in (plugin_releases or [])]
        self.default_agent_id = str(default_agent_id or "default").strip() or "default"
        self.monitor_reconciler = monitor_reconciler
        self.task_worker_count = max(1, min(int(max_concurrent_runs or 4), 32))
        self.work_signal = RuntimeWorkSignal(
            store, fallback_poll_seconds=poll_interval_seconds
        )
        self._graph_task_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._graph_active_count = 0
        self._run_claim_details: dict[str, dict[str, Any]] = {}
        self._task_claim_details: dict[str, dict[str, Any]] = {}
        self._started = False
        self._closing = False
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Join the worker pool and recover durable work after process restart."""
        async with self._start_lock:
            if self._started:
                return
            self._started = True
            self._closing = False
            # API replicas use a submit-only runtime facade. Channel workers
            # do not execute Runs either, but they still need a leased cluster
            # identity so operators can distinguish a live connector process
            # from a configured-but-unowned Channel.
            if not self.presence_enabled:
                return
            if self.worker_enabled or self.scheduler_enabled:
                await self.work_signal.start()
            register = getattr(self.store, "register_runtime_worker", None)
            if register is not None:
                await asyncio.to_thread(
                    register,
                    worker_id=self.worker_id,
                    capabilities=self.capabilities,
                    metadata={
                        "task_worker_count": self.task_worker_count,
                        "extensions": self.plugin_releases,
                    },
                )
            self._worker_tasks = []
            if self.worker_enabled or self.scheduler_enabled:
                await self._scan_incomplete_runs()
                self._worker_tasks.append(
                    asyncio.create_task(
                        self._runtime_coordinator_loop(), name="runtime-coordinator"
                    )
                )
            else:
                self._worker_tasks.append(
                    asyncio.create_task(
                        self._runtime_presence_loop(), name="runtime-presence"
                    )
                )
            if self.worker_enabled:
                self._worker_tasks.extend(
                    [
                        asyncio.create_task(
                            self._task_dispatcher_loop(), name="runtime-task-dispatcher"
                        ),
                        *[
                            asyncio.create_task(
                                self._graph_executor_loop(index),
                                name=f"graph-executor:{index}",
                            )
                            for index in range(self.task_worker_count)
                        ],
                    ]
                )

    async def close(self) -> None:
        self._closing = True
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        await self.work_signal.close()
        await self.supervisor.close()
        unregister = getattr(self.store, "unregister_runtime_worker", None)
        if unregister is not None and self.presence_enabled:
            await asyncio.to_thread(unregister, self.worker_id)
        self._started = False

    async def _runtime_presence_loop(self) -> None:
        """Renew a non-executing role's PostgreSQL-backed presence lease."""
        while not self._closing:
            await asyncio.sleep(30)
            heartbeat = getattr(self.store, "heartbeat_runtime_worker", None)
            if heartbeat is not None:
                await asyncio.to_thread(heartbeat, self.worker_id)

    async def _log(
        self,
        run_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self.store.append_runtime_log,
            run_id=run_id,
            task_id=task_id,
            worker_id=self.worker_id,
            level=level,
            stage=stage,
            message=message,
            data=redact_runtime_value(data or {}),
        )

    async def _resolve_execution_agent(
        self,
        run_id: str,
        agent_id: str,
        agent_revision_id: str | None = None,
    ) -> Any | None:
        """Resolve the immutable Agent revision frozen when the Run was accepted."""
        revision_key = agent_revision_id or agent_id
        if agent_revision_id:
            revision = await asyncio.to_thread(
                self.store.get_agent_revision, agent_revision_id
            )
            if (
                revision is None
                or revision.agent_id != agent_id
                or revision.status not in {"published", "retired"}
            ):
                raise ValueError(
                    f"published Agent revision not found: {agent_id}@{agent_revision_id}"
                )
            self._assert_plugin_requirements(revision.plugin_requirements)
        snapshot_reader = getattr(self.store, "get_run_execution_snapshot", None)
        if not agent_revision_id and snapshot_reader is not None:
            snapshot = await asyncio.to_thread(snapshot_reader, run_id)
            if snapshot is not None and (
                agent_id in {"default", snapshot.agent_id}
                or snapshot.agent_id == self.default_agent_id
            ):
                self._assert_plugin_requirements(snapshot.plugin_requirements)
                revision_key = snapshot.agent_revision_id
        if self.agent_resolver is None:
            return self.agent
        resolved = await asyncio.to_thread(self.agent_resolver, revision_key)
        connect = getattr(resolved, "_connect_tool_connectors", None)
        if callable(connect):
            # AgentRuntimeCatalog.resolve() is intentionally synchronous, so a
            # newly lazy-loaded Agent cannot connect async Tool Connectors there.
            # Ensure its registry is ready before a direct Graph Capability node
            # uses it; AgentLoop owns the idempotent connection lock.
            await connect()
        return resolved

    async def _execution_permissions(
        self,
        run_id: str,
        agent_id: str,
        agent_revision_id: str | None = None,
    ) -> frozenset[str]:
        """Read the capability grants frozen with this Run's Agent revision.

        Permissions are not read from the mutable Agent catalog while work is
        executing.  A retry or replay therefore sees the same grants as the
        original attempt, even after an administrator publishes a new
        revision.
        """
        authority_permissions: frozenset[str] = frozenset()
        run = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if run is not None:
            raw_authority = dict(run.options or {}).get("authority_permissions", ())
            if isinstance(raw_authority, (list, tuple, set, frozenset)):
                authority_permissions = frozenset(
                    str(item).strip() for item in raw_authority if str(item).strip()
                )
        if agent_revision_id:
            revision = await asyncio.to_thread(
                self.store.get_agent_revision, agent_revision_id
            )
            if (
                revision is None
                or revision.agent_id != agent_id
                or revision.status not in {"published", "retired"}
            ):
                return authority_permissions
            value = revision.capability_policy.get("permissions", ())
            if not isinstance(value, (list, tuple, set, frozenset)):
                return authority_permissions
            return authority_permissions | frozenset(
                str(item).strip() for item in value if str(item).strip()
            )
        reader = getattr(self.store, "get_run_execution_snapshot", None)
        if reader is None:
            return authority_permissions
        snapshot = await asyncio.to_thread(reader, run_id)
        if snapshot is None or agent_id not in {"default", snapshot.agent_id}:
            return authority_permissions
        value = snapshot.capability_policy.get("permissions", ())
        if not isinstance(value, (list, tuple, set, frozenset)):
            return authority_permissions
        return authority_permissions | frozenset(
            str(item).strip() for item in value if str(item).strip()
        )

    def _assert_plugin_requirements(self, requirements: Any) -> None:
        """Refuse execution on a node without each pinned plugin artifact."""
        loaded = {
            (
                str(item.get("plugin_id") or ""),
                str(item.get("version") or ""),
                str(item.get("build_digest") or ""),
            )
            for item in self.plugin_releases
        }
        missing = [
            f"{item.plugin_id}@{item.version}"
            for item in requirements
            if (item.plugin_id, item.version, item.build_digest) not in loaded
        ]
        if missing:
            raise RuntimeError(
                "worker does not have required plugin releases: " + ", ".join(missing)
            )
