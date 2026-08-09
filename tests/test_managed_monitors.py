"""Managed Agent Monitor desired-state tests."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from joyhousebot.api.schemas import SaveAgentRevisionRequest
from joyhousebot.cron.active_hours import is_within_active_hours
from joyhousebot.cron.managed_monitor import (
    managed_monitor_schedule_id,
    reconcile_agent_monitor,
    reconcile_existing_agent_monitors,
)
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.scheduling.repository import ScheduleRepository
from tests.support.postgres_store import PostgresTestStore


def test_active_hours_support_daytime_overnight_and_all_day() -> None:
    zone = ZoneInfo("Asia/Shanghai")
    now_ms = int(datetime(2026, 8, 9, 23, 30, tzinfo=zone).timestamp() * 1000)
    assert is_within_active_hours(
        {"start": "22:00", "end": "07:00", "timezone": "Asia/Shanghai"}, now_ms
    )
    assert not is_within_active_hours(
        {"start": "08:00", "end": "22:00", "timezone": "Asia/Shanghai"}, now_ms
    )
    assert is_within_active_hours(
        {"start": "00:00", "end": "00:00", "timezone": "Asia/Shanghai"}, now_ms
    )


def test_agent_revision_request_rejects_invalid_managed_monitor_policy() -> None:
    with pytest.raises(ValidationError, match="at least 60000"):
        SaveAgentRevisionRequest(
            revision_id="watcher:v1",
            version=1,
            name="Watcher",
            model_policy={"primary": "test/model"},
            monitor_policy={
                "enabled": True,
                "schedule": {"kind": "every", "every_ms": 1_000},
            },
        )


def _profile(store: PostgresTestStore, revision_id: str, *, enabled: bool = True):
    store.save_agent_revision(
        AgentDefinition(agent_id="watcher", name="Watcher"),
        AgentRevision(
            revision_id=revision_id,
            agent_id="watcher",
            version=int(revision_id.rsplit("v", 1)[-1]),
            model_policy={"primary": "test/model"},
            monitor_policy={
                "enabled": enabled,
                "schedule": {"kind": "every", "every_ms": 120_000},
                "message": "Check for changes.",
                "context_mode": "light",
                "active_hours": {
                    "start": "08:00",
                    "end": "22:00",
                    "timezone": "Asia/Shanghai",
                },
            },
            status="published",
        ),
    )
    profile = store.get_agent_profile("watcher")
    assert profile is not None
    return profile


def test_managed_monitor_is_stable_user_scoped_and_revision_owned(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "managed-monitor.db")
    repository = ScheduleRepository(store)
    profile = _profile(store, "watcher:v1")

    first = reconcile_agent_monitor(
        repository,
        user_id="user-1",
        profile=profile,
        channel="telegram",
        target="chat-1",
    )
    second = reconcile_agent_monitor(repository, user_id="user-1", profile=profile)
    other = reconcile_agent_monitor(repository, user_id="user-2", profile=profile)

    assert first is not None and second is not None and other is not None
    assert first.id == second.id == managed_monitor_schedule_id("user-1", "watcher")
    assert other.id != first.id
    assert first.payload.managed_by == "agent_revision"
    assert first.payload.managed_revision_id == "watcher:v1"
    assert first.payload.context_mode == "light"
    assert len(repository.list(user_id="user-1", include_disabled=True)) == 1


def test_published_revision_reconciles_and_can_disable_existing_users(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "managed-monitor-publish.db")
    repository = ScheduleRepository(store)
    profile = _profile(store, "watcher:v1")
    assert reconcile_agent_monitor(repository, user_id="user-1", profile=profile)

    disabled = _profile(store, "watcher:v2", enabled=False)
    reconcile_existing_agent_monitors(repository, disabled)

    jobs = repository.list(user_id="user-1", include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].enabled is False
