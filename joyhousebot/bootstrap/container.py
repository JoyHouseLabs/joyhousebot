"""Application container for the stateless HTTP API role."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from joyhousebot.application.feedback import FeedbackService
from joyhousebot.application.platform import PlatformService
from joyhousebot.application.plugins import configured_plugin_registry
from joyhousebot.application.replays import ReplayService
from joyhousebot.application.runs import RunService
from joyhousebot.application.scenarios import ScenarioStudioService
from joyhousebot.application.schedules import ScheduleService
from joyhousebot.application.sessions import SessionService
from joyhousebot.bootstrap.agent_catalog import default_agent_id
from joyhousebot.config.access import get_config
from joyhousebot.cron.service import CronService
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.storage.factory import create_runtime_store


@dataclass(slots=True)
class ApplicationContainer:
    config: Any
    store: Any
    runtime: NativeAgentRuntime
    runs: RunService
    sessions: SessionService
    schedules: ScheduleService
    platform: PlatformService
    replays: ReplayService
    feedback: FeedbackService
    scenarios: ScenarioStudioService
    plugins: Any
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
    # In insecure local mode there is one explicit test administrator. Other
    # X-User-ID values remain ordinary users and cannot call management APIs.
    if bool(getattr(config.gateway, "allow_insecure_auth", False)):
        test_user_id = str(os.getenv("JOYHOUSEBOT_DEV_USER_ID") or "local-dev").strip()
        if store.get_platform_admin(test_user_id) is None:
            store.upsert_platform_admin(
                user_id=test_user_id,
                role="admin",
                permissions=["*"],
                enabled=True,
                is_test_user=True,
                actor_id="development-bootstrap",
            )
    runtime = NativeAgentRuntime(
        agent=None,
        store=store,
        worker_enabled=False,
        scheduler_enabled=False,
        worker_name="api",
        default_agent_id=default_agent_id(store),
    )
    schedules = CronService(store, worker_id="api-submit-only")
    runs = RunService(runtime, store)
    return ApplicationContainer(
        config=config,
        store=store,
        runtime=runtime,
        runs=runs,
        sessions=SessionService(store),
        schedules=ScheduleService(schedules, config=config),
        platform=PlatformService(store),
        replays=ReplayService(runtime, store),
        feedback=FeedbackService(runs, store),
        scenarios=ScenarioStudioService(store),
        plugins=configured_plugin_registry(config),
        owns_store=owns_store,
    )
