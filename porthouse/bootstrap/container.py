"""Application container for the stateless HTTP API role."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import partial
from typing import Any

from porthouse.application.action_items import ActionItemService
from porthouse.application.agent_teams import AgentTeamService
from porthouse.application.app_callbacks import AppCallbackService
from porthouse.application.app_delegation import AppDelegationService
from porthouse.application.app_market import AppMarketService
from porthouse.application.app_packs import AppPackService
from porthouse.application.approvals import ApprovalService
from porthouse.application.artifact_uploads import ArtifactUploadService
from porthouse.application.device_hosts import DeviceHostService
from porthouse.application.embedding_profiles import EmbeddingProfileService
from porthouse.application.eval_execution import EvalExecutionService
from porthouse.application.evals import EvalService
from porthouse.application.event_triggers import EventTriggerService
from porthouse.application.experiments import ExperimentService
from porthouse.application.feedback import FeedbackService
from porthouse.application.graph_events import GraphEventService
from porthouse.application.graph_patches import GraphPatchService
from porthouse.application.host_tools import HostToolService
from porthouse.application.input_assets import InputAssetService
from porthouse.application.knowledge_assets import KnowledgeAssetService
from porthouse.application.knowledge_maintenance import KnowledgeMaintenanceService
from porthouse.application.memory_candidates import MemoryCandidateService
from porthouse.application.model_grants import ModelGrantService
from porthouse.application.model_providers import ModelProviderService
from porthouse.application.platform import PlatformService
from porthouse.application.prompts import PromptService
from porthouse.application.reconciliations import ReconciliationService
from porthouse.application.remote_connections import RemoteConnectionService
from porthouse.application.replays import ReplayService
from porthouse.application.runs import RunService
from porthouse.application.scenarios import ScenarioStudioService
from porthouse.application.schedules import ScheduleService
from porthouse.application.sessions import SessionService
from porthouse.application.skills import SkillService
from porthouse.application.workflows import WorkflowService
from porthouse.application.works import WorkService
from porthouse.bootstrap.agent_catalog import default_agent_id
from porthouse.bootstrap.extension_catalog import synchronize_extension_inventory
from porthouse.config.access import get_config
from porthouse.cron.managed_monitor import (
    reconcile_agent_monitor,
    reconcile_existing_agent_monitors,
)
from porthouse.cron.service import CronService
from porthouse.domain.schedules import CronJob, schedule_run_prompt, schedule_run_session_id
from porthouse.runtime.models import AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.security.admin_auth import (
    DEFAULT_DEVELOPMENT_ADMIN_PASSWORD,
    DEFAULT_DEVELOPMENT_ADMIN_USER,
    hash_development_default_password,
    hash_password,
    validate_password,
)
from porthouse.storage.factory import create_runtime_store


@dataclass(slots=True)
class ApplicationContainer:
    config: Any
    store: Any
    runtime: NativeAgentRuntime
    runs: RunService
    approvals: ApprovalService
    artifact_uploads: ArtifactUploadService
    device_hosts: DeviceHostService
    host_tools: HostToolService
    action_items: ActionItemService
    reconciliations: ReconciliationService
    knowledge_assets: KnowledgeAssetService
    knowledge_maintenance: KnowledgeMaintenanceService
    input_assets: InputAssetService
    memory_candidates: MemoryCandidateService
    graph_events: GraphEventService
    graph_patches: GraphPatchService
    sessions: SessionService
    schedules: ScheduleService
    platform: PlatformService
    replays: ReplayService
    feedback: FeedbackService
    evals: EvalService
    eval_execution: EvalExecutionService
    experiments: ExperimentService
    event_triggers: EventTriggerService
    scenarios: ScenarioStudioService
    works: WorkService
    workflows: WorkflowService
    remote_connections: RemoteConnectionService
    model_providers: ModelProviderService
    model_grants: ModelGrantService
    embedding_profiles: EmbeddingProfileService
    skills: SkillService
    prompts: PromptService
    app_packs: AppPackService
    app_delegation: AppDelegationService
    app_callbacks: AppCallbackService
    app_market: AppMarketService
    agent_teams: AgentTeamService
    owns_store: bool = True

    async def close(self) -> None:
        await self.runtime.close()
        if self.owns_store:
            await asyncio.to_thread(self.store.close)


def build_api_container(
    *, config: Any | None = None, store: Any | None = None
) -> ApplicationContainer:
    config = config or get_config()
    owns_store = store is None
    store = store or create_runtime_store(config)
    # API startup performs metadata-only reconciliation. Extension modules are
    # never imported in the HTTP process.
    synchronize_extension_inventory(config, store=store)
    # Local development gets a documented password alongside the explicitly
    # insecure X-User-ID mode. Production has no source-code default: its one-time
    # bootstrap credential must be injected through paired environment vars.
    production = str(os.getenv("PORTHOUSE_ENVIRONMENT") or "development").strip().lower() in {
        "prod",
        "production",
    }
    bootstrap_user = str(os.getenv("PORTHOUSE_BOOTSTRAP_ADMIN_USER") or "").strip()
    bootstrap_password = str(os.getenv("PORTHOUSE_BOOTSTRAP_ADMIN_PASSWORD") or "")
    if bool(bootstrap_user) != bool(bootstrap_password):
        raise ValueError(
            "PORTHOUSE_BOOTSTRAP_ADMIN_USER and PORTHOUSE_BOOTSTRAP_ADMIN_PASSWORD "
            "must be configured together"
        )
    insecure_local = bool(getattr(config.gateway, "allow_insecure_auth", False))
    if not bootstrap_user and insecure_local and not production:
        bootstrap_user = str(
            os.getenv("PORTHOUSE_DEV_USER_ID") or DEFAULT_DEVELOPMENT_ADMIN_USER
        ).strip()
        bootstrap_password = str(
            os.getenv("PORTHOUSE_DEV_ADMIN_PASSWORD")
            or DEFAULT_DEVELOPMENT_ADMIN_PASSWORD
        )
    if bootstrap_user:
        allow_development_default = bool(
            insecure_local
            and not production
            and bootstrap_password == DEFAULT_DEVELOPMENT_ADMIN_PASSWORD
        )
        if not allow_development_default:
            validate_password(bootstrap_password)
        if store.get_platform_admin(bootstrap_user) is None:
            store.upsert_platform_admin(
                user_id=bootstrap_user,
                role="admin",
                permissions=["*"],
                enabled=True,
                is_test_user=not production,
                actor_id=("production-bootstrap" if production else "development-bootstrap"),
            )
        if store.get_admin_login_credential(bootstrap_user) is None:
            store.set_admin_password(
                user_id=bootstrap_user,
                password_hash=(
                    hash_development_default_password()
                    if allow_development_default
                    else hash_password(bootstrap_password)
                ),
                must_change_password=True,
                actor_id=("production-bootstrap" if production else "development-bootstrap"),
                only_if_missing=True,
            )
    schedules = CronService(store, worker_id="api-submit-only")
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=False,
        worker_name="api",
        default_agent_id=default_agent_id(store),
        monitor_reconciler=partial(reconcile_agent_monitor, schedules.repository),
    )

    async def submit_schedule(job: CronJob) -> str:
        """API-side submission only; Agent workers still execute the resulting Run."""
        monitor_context = await asyncio.to_thread(schedules.monitor_run_context, job)
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
                agent_id=job.agent_id or default_agent_id(store),
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
                    "_runtime_schedule_submission_ready": False,
                },
                idempotency_key=(
                    f"schedule:{job.id}:{job.state.scheduled_for_ms or 'manual'}:"
                    f"{job.state.attempt}"
                ),
            )
        )
        return record.run_id

    schedules.on_job = submit_schedule
    runs = RunService(runtime, store)
    evals = EvalService(store)
    scenarios = ScenarioStudioService(store)
    app_packs = AppPackService(store)
    platform = PlatformService(
        store,
        monitor_reconciler=partial(
            reconcile_existing_agent_monitors, schedules.repository
        ),
    )
    return ApplicationContainer(
        config=config,
        store=store,
        runtime=runtime,
        runs=runs,
        approvals=ApprovalService(runtime, runs, store),
        artifact_uploads=ArtifactUploadService(store),
        device_hosts=DeviceHostService(store, runtime),
        host_tools=HostToolService(store),
        action_items=ActionItemService(store),
        reconciliations=ReconciliationService(runtime, runs, store),
        knowledge_assets=KnowledgeAssetService(store, runtime),
        knowledge_maintenance=KnowledgeMaintenanceService(store),
        input_assets=InputAssetService(store),
        memory_candidates=MemoryCandidateService(store),
        graph_events=GraphEventService(runtime, runs, store),
        graph_patches=GraphPatchService(runtime, runs, store),
        sessions=SessionService(store),
        schedules=ScheduleService(schedules, config=config),
        platform=platform,
        replays=ReplayService(runtime, store),
        feedback=FeedbackService(runs, store),
        evals=evals,
        eval_execution=EvalExecutionService(
            store=store,
            runtime=runtime,
            evals=evals,
            scenarios=scenarios,
        ),
        experiments=ExperimentService(store),
        event_triggers=EventTriggerService(
            runtime,
            store,
            default_agent_id=default_agent_id(store),
        ),
        scenarios=scenarios,
        works=WorkService(store, app_packs=app_packs),
        workflows=WorkflowService(
            runtime,
            store,
            default_agent_id=default_agent_id(store),
        ),
        remote_connections=RemoteConnectionService(store, platform),
        model_providers=ModelProviderService(store),
        model_grants=ModelGrantService(store),
        embedding_profiles=EmbeddingProfileService(store),
        skills=SkillService(store),
        prompts=PromptService(store),
        app_packs=app_packs,
        app_delegation=AppDelegationService(store),
        app_callbacks=AppCallbackService(store),
        app_market=AppMarketService(store),
        agent_teams=AgentTeamService(store),
        owns_store=owns_store,
    )
