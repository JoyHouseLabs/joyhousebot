"""Composition roots for execution and scheduler worker roles."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Any

from loguru import logger

from joyhousebot.application.app_callbacks import AppCallbackDispatcher
from joyhousebot.application.app_market import AppMarketService
from joyhousebot.application.eval_execution import EvalExecutionService
from joyhousebot.application.evals import EvalService
from joyhousebot.application.knowledge_maintenance import KnowledgeMaintenanceService
from joyhousebot.application.scenarios import ScenarioStudioService
from joyhousebot.bootstrap.agent_catalog import default_agent_id
from joyhousebot.bootstrap.agent_runtime_catalog import AgentRuntimeCatalog
from joyhousebot.bootstrap.extension_rollouts import ExtensionRolloutWatcher
from joyhousebot.bootstrap.host_tool_worker import HostToolBrokerWorker
from joyhousebot.channels.manager import ChannelManager
from joyhousebot.channels.repository import ChannelRepository
from joyhousebot.channels.runtime_bridge import ChannelRuntimeBridge
from joyhousebot.config.access import get_config
from joyhousebot.cron.managed_monitor import reconcile_agent_monitor
from joyhousebot.cron.service import CronService
from joyhousebot.domain.schedules import CronJob, schedule_run_prompt, schedule_run_session_id
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.storage.factory import create_runtime_store


def _plugin_health_checks(plugin: Any) -> list[dict[str, str]]:
    checks = getattr(plugin, "health_checks", None)
    if not callable(checks):
        return []
    try:
        values = checks() or ()
    except Exception as exc:
        return [
            {
                "name": "plugin_health",
                "status": "failed",
                "summary": f"health check failed: {type(exc).__name__}",
            }
        ]
    output = []
    for value in values:
        item = value.to_dict() if hasattr(value, "to_dict") else value
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "degraded").strip().lower()
        if not name or status not in {"healthy", "degraded", "failed"}:
            continue
        output.append(
            {
                "name": name[:128],
                "status": status,
                "summary": str(item.get("summary") or "")[:500],
            }
        )
    return output


def _extension_releases(agent: Any, config: Any) -> list[dict[str, Any]]:
    releases: dict[str, dict[str, Any]] = {}
    registry = getattr(getattr(agent, "capabilities", None), "plugins", None)
    manifests = getattr(registry, "manifests", None)
    if callable(manifests):
        plugins = {
            str(getattr(plugin, "plugin_id", "")): plugin
            for plugin in getattr(registry, "plugins", ())
        }
        for manifest in manifests():
            release = manifest.to_release_dict()
            release["health_checks"] = _plugin_health_checks(
                plugins.get(manifest.plugin_id)
            )
            releases[manifest.plugin_id] = release
    from joyhousebot.providers.registry import get_provider_registry

    for manifest in get_provider_registry(config).manifests():
        releases[manifest.extension_id] = manifest.to_release_dict()
    connectors = getattr(agent, "tool_connectors", None)
    connector_manifests = getattr(connectors, "manifests", None)
    if callable(connector_manifests):
        for manifest in connector_manifests():
            releases[manifest.extension_id] = manifest.to_release_dict()
    return list(releases.values())


@dataclass(slots=True)
class ExecutionWorker:
    runtime: NativeAgentRuntime
    catalog: AgentRuntimeCatalog
    store: Any
    knowledge_maintenance: KnowledgeMaintenanceService

    async def run(self) -> None:
        await self.catalog.start()
        await self.runtime.start()
        catalog_task = asyncio.create_task(
            self.catalog.watch(), name="agent-runtime-catalog"
        )
        knowledge_task = asyncio.create_task(
            self._knowledge_maintenance_loop(), name="knowledge-maintenance-worker"
        )
        artifact_task = asyncio.create_task(
            self._artifact_materialization_loop(), name="artifact-materialization-worker"
        )
        host_tool_worker = HostToolBrokerWorker(
            store=self.store,
            catalog=self.catalog,
            worker_id=self.runtime.worker_id,
            lease_seconds=self.runtime.lease_seconds,
        )
        host_tool_task = asyncio.create_task(
            host_tool_worker.run(), name="host-tool-broker-worker"
        )
        try:
            await asyncio.Event().wait()
        finally:
            catalog_task.cancel()
            knowledge_task.cancel()
            artifact_task.cancel()
            host_tool_task.cancel()
            await asyncio.gather(
                catalog_task,
                knowledge_task,
                artifact_task,
                host_tool_task,
                return_exceptions=True,
            )
            await self.catalog.close()
            await self.runtime.close()
            await asyncio.to_thread(self.store.close)

    async def _artifact_materialization_loop(self) -> None:
        while True:
            try:
                grant = await asyncio.to_thread(
                    self.store.claim_artifact_upload,
                    worker_id=self.runtime.worker_id,
                    lease_seconds=60,
                )
                if grant is None:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    saved = await asyncio.to_thread(
                        self.store.materialize_artifact_upload,
                        grant.grant_id,
                        worker_id=self.runtime.worker_id,
                        lease_version=grant.lease_version,
                    )
                    if not saved:
                        raise RuntimeError("Artifact materialization lease was fenced")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self.store.fail_artifact_upload,
                        grant.grant_id,
                        worker_id=self.runtime.worker_id,
                        lease_version=grant.lease_version,
                        error={"type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                    logger.exception(
                        "Artifact materialization failed grant_id={}", grant.grant_id
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Artifact materialization worker loop failed; retrying")
                await asyncio.sleep(1.0)

    async def _knowledge_maintenance_loop(self) -> None:
        lease_seconds = 120
        reconcile_at = 0.0
        while True:
            try:
                now = asyncio.get_running_loop().time()
                if now >= reconcile_at:
                    await asyncio.to_thread(
                        self.knowledge_maintenance.repository.reconcile_vector_indexes,
                        limit=20,
                    )
                    reconcile_at = now + 300
                item = await asyncio.to_thread(
                    self.knowledge_maintenance.repository.claim_reembedding_item,
                    worker_id=self.runtime.worker_id,
                    lease_seconds=lease_seconds,
                )
                if item is None:
                    await asyncio.sleep(1.0)
                    continue
                heartbeat = asyncio.create_task(
                    self._heartbeat_knowledge_item(item, lease_seconds),
                    name=f"knowledge-heartbeat:{item['job_id']}:{item['doc_id']}",
                )
                try:
                    await self.knowledge_maintenance.process_item(
                        item, worker_id=self.runtime.worker_id
                    )
                    completed = await asyncio.to_thread(
                        self.knowledge_maintenance.repository.complete_reembedding_item,
                        item["job_id"],
                        item["doc_id"],
                        item["revision_id"],
                        worker_id=self.runtime.worker_id,
                        lease_version=int(item["lease_version"]),
                    )
                    if not completed:
                        raise RuntimeError("Knowledge maintenance completion was fenced")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await asyncio.to_thread(
                        self.knowledge_maintenance.repository.fail_reembedding_item,
                        item["job_id"],
                        item["doc_id"],
                        item["revision_id"],
                        worker_id=self.runtime.worker_id,
                        lease_version=int(item["lease_version"]),
                        error={"type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                    logger.exception(
                        "Knowledge re-embedding failed job_id={} doc_id={}",
                        item["job_id"],
                        item["doc_id"],
                    )
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Knowledge maintenance worker loop failed; retrying")
                await asyncio.sleep(1.0)

    async def _heartbeat_knowledge_item(
        self, item: dict[str, Any], lease_seconds: int
    ) -> None:
        while True:
            await asyncio.sleep(max(10, lease_seconds // 3))
            renewed = await asyncio.to_thread(
                self.knowledge_maintenance.repository.heartbeat_reembedding_item,
                item["job_id"],
                item["doc_id"],
                item["revision_id"],
                worker_id=self.runtime.worker_id,
                lease_version=int(item["lease_version"]),
                lease_seconds=lease_seconds,
            )
            if not renewed:
                raise RuntimeError("Knowledge maintenance lease was fenced")


@dataclass(slots=True)
class SchedulerWorker:
    runtime: NativeAgentRuntime
    cron: CronService
    store: Any
    eval_execution: EvalExecutionService
    app_market: AppMarketService
    app_callbacks: AppCallbackDispatcher

    async def run(self) -> None:
        await self.runtime.start()
        await self.cron.start()
        eval_task = asyncio.create_task(self._eval_loop(), name="eval-execution-worker")
        market_task = asyncio.create_task(
            self._app_acquisition_loop(), name="app-acquisition-worker"
        )
        callback_task = asyncio.create_task(
            self._app_callback_loop(), name="app-callback-worker"
        )
        try:
            await asyncio.Event().wait()
        finally:
            eval_task.cancel()
            market_task.cancel()
            callback_task.cancel()
            await asyncio.gather(
                eval_task,
                market_task,
                callback_task,
                return_exceptions=True,
            )
            self.cron.stop()
            await self.cron.wait_stopped()
            await self.runtime.close()
            await asyncio.to_thread(self.store.close)

    async def _eval_loop(self) -> None:
        lease_seconds = 90
        while True:
            try:
                await asyncio.to_thread(self.store.reconcile_due_eval_schedules)
                job = await asyncio.to_thread(
                    self.store.claim_eval_execution_job,
                    worker_id=self.runtime.worker_id,
                    lease_seconds=lease_seconds,
                )
                if job is None:
                    await asyncio.sleep(1.0)
                    continue
                heartbeat = asyncio.create_task(
                    self._heartbeat_eval_job(job, lease_seconds),
                    name=f"eval-heartbeat:{job['eval_run_id']}",
                )
                try:
                    configuration = dict(job.get("configuration") or {})
                    await self.eval_execution.execute(
                        job["eval_run_id"],
                        actor_id=str(job["requested_by"]),
                        max_concurrency=int(configuration.get("max_concurrency", 4)),
                        case_timeout_seconds=float(
                            configuration.get("case_timeout_seconds", 300)
                        ),
                    )
                    completed = await asyncio.to_thread(
                        self.store.complete_eval_execution_job,
                        job["eval_run_id"],
                        worker_id=self.runtime.worker_id,
                        lease_version=int(job["lease_version"]),
                    )
                    if not completed:
                        raise RuntimeError("Eval execution completion was fenced")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # job state records bounded safe error metadata
                    await asyncio.to_thread(
                        self.store.fail_eval_execution_job,
                        job["eval_run_id"],
                        worker_id=self.runtime.worker_id,
                        lease_version=int(job["lease_version"]),
                        error={"type": type(exc).__name__, "message": str(exc)[:1000]},
                    )
                    logger.exception(
                        "Eval execution failed eval_run_id={}", job["eval_run_id"]
                    )
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Eval worker loop failed; retrying")
                await asyncio.sleep(1.0)

    async def _heartbeat_eval_job(
        self, job: dict[str, Any], lease_seconds: int
    ) -> None:
        while True:
            await asyncio.sleep(max(10, lease_seconds // 3))
            renewed = await asyncio.to_thread(
                self.store.heartbeat_eval_execution_job,
                job["eval_run_id"],
                worker_id=self.runtime.worker_id,
                lease_version=int(job["lease_version"]),
                lease_seconds=lease_seconds,
            )
            if not renewed:
                raise RuntimeError("Eval execution lease was fenced")

    async def _app_acquisition_loop(self) -> None:
        while True:
            try:
                processed = await self.app_market.process_next(
                    worker_id=self.runtime.worker_id
                )
                if not processed:
                    processed = await self.app_market.process_update_next(
                        worker_id=self.runtime.worker_id
                    )
                if not processed:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("App acquisition worker loop failed; retrying")
                await asyncio.sleep(1.0)

    async def _app_callback_loop(self) -> None:
        while True:
            try:
                processed = await self.app_callbacks.process_next(
                    worker_id=self.runtime.worker_id
                )
                if not processed:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("App callback worker loop failed; retrying")
                await asyncio.sleep(1.0)


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
        rollout_task = asyncio.create_task(
            ExtensionRolloutWatcher(store=self.store, runtime=self.runtime).watch(),
            name="channel-extension-rollouts",
        )
        try:
            await self.manager.start_all()
            await asyncio.Event().wait()
        finally:
            await self.bridge.close()
            bridge_task.cancel()
            rollout_task.cancel()
            await asyncio.gather(bridge_task, rollout_task, return_exceptions=True)
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
        plugin_releases=_extension_releases(default_agent, config),
        default_agent_id=default_id,
        poll_interval_seconds=config.runtime.store.poll_interval_seconds,
        monitor_reconciler=partial(reconcile_agent_monitor, schedules.repository),
    )
    catalog.set_runtime(runtime)
    return ExecutionWorker(
        runtime=runtime,
        catalog=catalog,
        store=store,
        knowledge_maintenance=KnowledgeMaintenanceService(
            store,
            embedding_provider_resolver=catalog.resolve_embedding_provider,
        ),
    )


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
        capabilities={
            "scheduler": True,
            "maintenance": True,
            "app_acquisition": True,
            "app_callbacks": True,
        },
        default_agent_id=resolved_default_id,
        poll_interval_seconds=config.runtime.store.poll_interval_seconds,
    )
    ChannelRepository(store)
    cron = CronService(
        store,
        worker_id=runtime.worker_id,
        default_agent_id=resolved_default_id,
    )

    async def submit_schedule(job: CronJob) -> str:
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
                agent_id=job.agent_id or resolved_default_id,
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
                idempotency_key=(
                    f"schedule:{job.id}:{job.state.scheduled_for_ms or 'manual'}:"
                    f"{job.state.attempt}"
                ),
            )
        )
        return record.run_id

    cron.on_job = submit_schedule
    evals = EvalService(store)
    return SchedulerWorker(
        runtime=runtime,
        cron=cron,
        store=store,
        eval_execution=EvalExecutionService(
            store=store,
            runtime=runtime,
            evals=evals,
            scenarios=ScenarioStudioService(store),
        ),
        app_market=AppMarketService(store),
        app_callbacks=AppCallbackDispatcher(store),
    )


def build_channel_worker(config: Any | None = None) -> ChannelWorker:
    config = config or get_config()
    store = create_runtime_store(config)
    resolved_default_id = default_agent_id(store)
    schedules = CronService(store, worker_id="channel-monitor-reconcile")
    manager = ChannelManager(config, runtime_store=store)
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=False,
        presence_enabled=True,
        worker_name=config.runtime.worker_name or "channel-worker",
        capabilities={"channels": True},
        plugin_releases=manager.extension_releases(),
        default_agent_id=resolved_default_id,
        monitor_reconciler=partial(reconcile_agent_monitor, schedules.repository),
    )
    manager.worker_id = runtime.worker_id
    bridge = ChannelRuntimeBridge(
        runtime=runtime,
        outbound_sink=manager.publish_outbound,
        default_agent_id=resolved_default_id,
    )
    manager.set_run_adapter(bridge)
    return ChannelWorker(runtime=runtime, manager=manager, bridge=bridge, store=store)
