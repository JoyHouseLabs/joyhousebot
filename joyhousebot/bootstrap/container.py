"""Application container for the stateless HTTP API role."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from functools import partial
from typing import Any

from joyhousebot.application.agent_teams import AgentTeamService
from joyhousebot.application.app_callbacks import AppCallbackService
from joyhousebot.application.app_delegation import AppDelegationService
from joyhousebot.application.app_market import AppMarketService
from joyhousebot.application.app_packs import AppPackService
from joyhousebot.application.approvals import ApprovalService
from joyhousebot.application.eval_execution import EvalExecutionService
from joyhousebot.application.evals import EvalService
from joyhousebot.application.event_triggers import EventTriggerService
from joyhousebot.application.feedback import FeedbackService
from joyhousebot.application.graph_events import GraphEventService
from joyhousebot.application.graph_patches import GraphPatchService
from joyhousebot.application.input_assets import InputAssetService
from joyhousebot.application.knowledge_assets import KnowledgeAssetService
from joyhousebot.application.memory_candidates import MemoryCandidateService
from joyhousebot.application.model_providers import ModelProviderService
from joyhousebot.application.platform import PlatformService
from joyhousebot.application.reconciliations import ReconciliationService
from joyhousebot.application.remote_connections import RemoteConnectionService
from joyhousebot.application.replays import ReplayService
from joyhousebot.application.runs import RunService
from joyhousebot.application.scenarios import ScenarioStudioService
from joyhousebot.application.schedules import ScheduleService
from joyhousebot.application.sessions import SessionService
from joyhousebot.application.skills import SkillService
from joyhousebot.application.workflows import WorkflowService
from joyhousebot.application.works import WorkService
from joyhousebot.bootstrap.agent_catalog import default_agent_id
from joyhousebot.bootstrap.extension_catalog import synchronize_extension_inventory
from joyhousebot.config.access import get_config
from joyhousebot.cron.managed_monitor import (
    reconcile_agent_monitor,
    reconcile_existing_agent_monitors,
)
from joyhousebot.cron.service import CronService
from joyhousebot.domain.schedules import CronJob, schedule_run_prompt, schedule_run_session_id
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.security.admin_auth import (
    DEFAULT_DEVELOPMENT_ADMIN_PASSWORD,
    DEFAULT_DEVELOPMENT_ADMIN_USER,
    hash_development_default_password,
    hash_password,
    validate_password,
)
from joyhousebot.storage.factory import create_runtime_store


@dataclass(slots=True)
class ApplicationContainer:
    config: Any
    store: Any
    runtime: NativeAgentRuntime
    runs: RunService
    approvals: ApprovalService
    reconciliations: ReconciliationService
    knowledge_assets: KnowledgeAssetService
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
    event_triggers: EventTriggerService
    scenarios: ScenarioStudioService
    works: WorkService
    workflows: WorkflowService
    remote_connections: RemoteConnectionService
    model_providers: ModelProviderService
    skills: SkillService
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
    production = str(os.getenv("JOYHOUSEBOT_ENVIRONMENT") or "development").strip().lower() in {
        "prod",
        "production",
    }
    bootstrap_user = str(os.getenv("JOYHOUSEBOT_BOOTSTRAP_ADMIN_USER") or "").strip()
    bootstrap_password = str(os.getenv("JOYHOUSEBOT_BOOTSTRAP_ADMIN_PASSWORD") or "")
    if bool(bootstrap_user) != bool(bootstrap_password):
        raise ValueError(
            "JOYHOUSEBOT_BOOTSTRAP_ADMIN_USER and JOYHOUSEBOT_BOOTSTRAP_ADMIN_PASSWORD "
            "must be configured together"
        )
    insecure_local = bool(getattr(config.gateway, "allow_insecure_auth", False))
    if not bootstrap_user and insecure_local and not production:
        bootstrap_user = str(
            os.getenv("JOYHOUSEBOT_DEV_USER_ID") or DEFAULT_DEVELOPMENT_ADMIN_USER
        ).strip()
        bootstrap_password = str(
            os.getenv("JOYHOUSEBOT_DEV_ADMIN_PASSWORD")
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
        reconciliations=ReconciliationService(runtime, runs, store),
        knowledge_assets=KnowledgeAssetService(store, runtime),
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
        event_triggers=EventTriggerService(
            runtime,
            store,
            default_agent_id=default_agent_id(store),
        ),
        scenarios=scenarios,
        works=WorkService(store),
        workflows=WorkflowService(
            runtime,
            store,
            default_agent_id=default_agent_id(store),
        ),
        remote_connections=RemoteConnectionService(store, platform),
        model_providers=ModelProviderService(store),
        skills=SkillService(store),
        app_packs=AppPackService(store),
        app_delegation=AppDelegationService(store),
        app_callbacks=AppCallbackService(store),
        app_market=AppMarketService(store),
        agent_teams=AgentTeamService(store),
        owns_store=owns_store,
    )
