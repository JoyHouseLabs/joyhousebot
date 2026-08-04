from pathlib import Path

import pytest

from joyhousebot.agent.tools.cron import CronTool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.cron.service import CronService
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
