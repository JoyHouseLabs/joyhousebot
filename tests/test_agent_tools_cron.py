from pathlib import Path

import pytest

from joyhousebot.agent.tools.cron import CronTool
from joyhousebot.agent.tools.monitor_scratch import MonitorScratchTool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.cron.service import CronService
from joyhousebot.cron.types import CronSchedule
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.postgres_store import PostgresTestStore


def _context(user_id: str, agent_id: str = "joy") -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=f"run-{user_id}",
        session_key=f"session-{user_id}",
        channel="api",
        chat_id=f"chat-{user_id}",
        user_id=user_id,
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_cron_tool_is_user_scoped(tmp_path: Path) -> None:
    service = CronService(PostgresTestStore(tmp_path / "runtime.db"))
    tool = CronTool(service)

    created_a = await tool.execute(
        action="add",
        message="private-a",
        every_seconds=60,
        tool_context=_context("user-a", "agent-a"),
    )
    created_b = await tool.execute(
        action="add",
        message="private-b",
        every_seconds=60,
        tool_context=_context("user-b", "agent-b"),
    )

    assert "Created job" in created_a
    assert "Created job" in created_b
    assert "private-a" in await tool.execute(action="list", tool_context=_context("user-a"))
    assert "private-b" not in await tool.execute(action="list", tool_context=_context("user-a"))
    jobs_a = service.list_jobs(user_id="user-a")
    assert jobs_a[0].agent_id == "agent-a"
    denied = await tool.execute(
        action="remove",
        job_id=jobs_a[0].id,
        tool_context=_context("user-b"),
    )
    assert "not found" in denied.lower()
    assert service.list_jobs(user_id="user-a")


@pytest.mark.asyncio
async def test_cron_tool_requires_run_context(tmp_path: Path) -> None:
    tool = CronTool(CronService(PostgresTestStore(tmp_path / "runtime.db")))
    with pytest.raises(ToolInvocationError, match="run context"):
        await tool.execute(action="list")


@pytest.mark.asyncio
async def test_cron_tool_rejects_interval_below_one_minute(tmp_path: Path) -> None:
    tool = CronTool(CronService(PostgresTestStore(tmp_path / "runtime.db")))
    with pytest.raises(ToolInvocationError, match="at least 60"):
        await tool.execute(
            action="add",
            message="too fast",
            every_seconds=30,
            tool_context=_context("user-a"),
        )
    ok = await tool.execute(
        action="add",
        message="fine",
        every_seconds=60,
        tool_context=_context("user-a"),
    )
    assert "Created job" in ok


@pytest.mark.asyncio
async def test_cron_tool_creates_context_aware_agent_monitor(tmp_path: Path) -> None:
    service = CronService(PostgresTestStore(tmp_path / "monitor.db"))
    tool = CronTool(service)

    created = await tool.execute(
        action="add",
        message="Check for important changes",
        every_seconds=300,
        monitor=True,
        session_mode="main",
        context_mode="light",
        active_hours_start="08:00",
        active_hours_end="22:00",
        active_hours_timezone="Asia/Shanghai",
        tool_context=_context("user-a", "agent-a"),
    )

    assert "Created monitor" in created
    monitor = service.list_jobs(user_id="user-a")[0]
    assert monitor.payload.kind == "agent_monitor"
    assert monitor.payload.session_mode == "main"
    assert monitor.payload.session_id == "session-user-a"
    assert monitor.payload.context_mode == "light"
    assert monitor.payload.active_hours == {
        "start": "08:00",
        "end": "22:00",
        "timezone": "Asia/Shanghai",
    }
    assert monitor.policy.misfire_policy == "skip"
    assert monitor.policy.overlap_policy == "skip"


@pytest.mark.asyncio
async def test_monitor_scratch_tool_is_bound_to_current_monitor_and_user(tmp_path: Path) -> None:
    service = CronService(PostgresTestStore(tmp_path / "monitor-scratch-tool.db"))
    job = service.add_job(
        name="private monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="check",
        payload_kind="agent_monitor",
        user_id="user-a",
    )
    tool = MonitorScratchTool(service)
    monitor_context = ToolExecutionContext(
        run_id="monitor-run",
        session_key="monitor-session",
        channel="schedule",
        chat_id=job.id,
        user_id="user-a",
        agent_id="agent-a",
        metadata={"schedule_id": job.id, "schedule_payload_kind": "agent_monitor"},
    )

    assert '"revision": 0' in await tool.execute(
        action="get", tool_context=monitor_context
    )
    updated = await tool.execute(
        action="update",
        content="cursor=9",
        expected_revision=0,
        tool_context=monitor_context,
    )
    assert '"revision": 1' in updated
    ordinary = _context("user-a")
    with pytest.raises(ToolInvocationError, match="only available"):
        await tool.execute(action="get", tool_context=ordinary)
