"""Composition roots for execution and scheduler worker roles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from joyhousebot.bootstrap.agent_catalog import default_agent_id
from joyhousebot.bootstrap.agent_runtime_catalog import AgentRuntimeCatalog
from joyhousebot.channels.manager import ChannelManager
from joyhousebot.channels.repository import ChannelRepository
from joyhousebot.channels.runtime_bridge import ChannelRuntimeBridge
from joyhousebot.config.access import get_config
from joyhousebot.cron.service import CronService
from joyhousebot.cron.types import CronJob
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.storage.factory import create_runtime_store


def _plugin_releases(agent: Any) -> list[dict[str, Any]]:
    registry = getattr(getattr(agent, "capabilities", None), "plugins", None)
    manifests = getattr(registry, "manifests", None)
    if not callable(manifests):
        return []
    return [item.to_dict() for item in manifests()]


@dataclass(slots=True)
class ExecutionWorker:
    runtime: NativeAgentRuntime
    catalog: AgentRuntimeCatalog
    store: Any

    async def run(self) -> None:
        await self.runtime.start()
        catalog_task = asyncio.create_task(
            self.catalog.watch(), name="agent-runtime-catalog"
        )
        try:
            await asyncio.Event().wait()
        finally:
            catalog_task.cancel()
            await asyncio.gather(catalog_task, return_exceptions=True)
            await self.catalog.close()
            await self.runtime.close()
            await asyncio.to_thread(self.store.close)


@dataclass(slots=True)
class SchedulerWorker:
    runtime: NativeAgentRuntime
    cron: CronService
    store: Any

    async def run(self) -> None:
        await self.runtime.start()
        await self.cron.start()
        try:
            await asyncio.Event().wait()
        finally:
            self.cron.stop()
            await self.cron.wait_stopped()
            await self.runtime.close()
            await asyncio.to_thread(self.store.close)


@dataclass(slots=True)
class ChannelWorker:
    """Own external channel connections; submit runs but never execute models."""

    runtime: NativeAgentRuntime
    manager: ChannelManager
    bridge: ChannelRuntimeBridge
    store: Any

    async def run(self) -> None:
        await self.runtime.start()
        bridge_task = asyncio.create_task(self.bridge.run(), name="channel-runtime-bridge")
        try:
            await self.manager.start_all()
            await asyncio.Event().wait()
        finally:
            await self.bridge.close()
            bridge_task.cancel()
            await asyncio.gather(bridge_task, return_exceptions=True)
            await self.manager.stop_all()
            await self.runtime.close()
            await asyncio.to_thread(self.store.close)


def build_execution_worker(config: Any | None = None) -> ExecutionWorker:
    config = config or get_config()
    store = create_runtime_store(config)
    schedules = CronService(store, worker_id="agent-schedule-tools")
    outbox = ChannelRepository(store)

    async def outbound_sink(message: Any) -> None:
        await asyncio.to_thread(outbox.enqueue_message, message)

    default_id = default_agent_id(store)
    catalog = AgentRuntimeCatalog(
        config=config,
        store=store,
        cron_service=schedules,
        outbound_sink=outbound_sink,
    )
    default_agent = catalog.resolve(default_id)
    if default_agent is None:
        raise RuntimeError(f"default Agent runtime unavailable: {default_id}")
    runtime = NativeAgentRuntime(
        agent=default_agent,
        agent_resolver=catalog.resolve,
        store=store,
        max_concurrent_runs=config.gateway.max_concurrent_sessions,
        lease_seconds=config.runtime.store.lease_seconds,
        worker_enabled=True,
        # Agent workers also recover/finalize Graphs. The scheduler-only role
        # cannot perform model-based aggregation.
        scheduler_enabled=True,
        maintenance_enabled=False,
        worker_name=config.runtime.worker_name or "agent-worker",
        capabilities={"agent": True, "graph_task": True, "graph_finalizer": True},
        plugin_releases=_plugin_releases(default_agent),
        projection_registry=getattr(default_agent.capabilities, "plugins", None),
        default_agent_id=default_id,
        poll_interval_seconds=config.runtime.store.poll_interval_seconds,
    )
    catalog.set_runtime(runtime)
    return ExecutionWorker(runtime=runtime, catalog=catalog, store=store)


def build_scheduler_worker(config: Any | None = None) -> SchedulerWorker:
    config = config or get_config()
    store = create_runtime_store(config)
    resolved_default_id = default_agent_id(store)
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=True,
        maintenance_enabled=True,
        worker_name=config.runtime.worker_name or "scheduler",
        capabilities={"scheduler": True, "maintenance": True},
        default_agent_id=resolved_default_id,
        poll_interval_seconds=config.runtime.store.poll_interval_seconds,
    )
    cron = CronService(store, worker_id=runtime.worker_id)

    async def submit_schedule(job: CronJob) -> str:
        record = await runtime.submit_run(
            AgentOptions(
                prompt=job.payload.message,
                user_id=job.user_id,
                session_id=f"schedule:{job.id}",
                agent_id=job.agent_id or resolved_default_id,
                channel="schedule",
                chat_id=job.id,
                metadata={"schedule_id": job.id},
                idempotency_key=(f"schedule:{job.id}:{job.state.next_run_at_ms or 'manual'}"),
            )
        )
        return record.run_id

    cron.on_job = submit_schedule
    return SchedulerWorker(runtime=runtime, cron=cron, store=store)


def build_channel_worker(config: Any | None = None) -> ChannelWorker:
    config = config or get_config()
    store = create_runtime_store(config)
    resolved_default_id = default_agent_id(store)
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=False,
        worker_name=config.runtime.worker_name or "channel-worker",
        capabilities={"channels": True},
        default_agent_id=resolved_default_id,
    )
    manager = ChannelManager(config, runtime_store=store, worker_id=runtime.worker_id)
    bridge = ChannelRuntimeBridge(
        runtime=runtime,
        outbound_sink=manager.publish_outbound,
        default_agent_id=resolved_default_id,
    )
    manager.set_run_adapter(bridge)
    return ChannelWorker(runtime=runtime, manager=manager, bridge=bridge, store=store)
