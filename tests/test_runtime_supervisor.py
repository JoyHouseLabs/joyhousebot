import asyncio

import pytest

from porthouse.runtime.supervisor import TaskSupervisor


@pytest.mark.asyncio
async def test_supervisor_enforces_concurrency_and_waits() -> None:
    supervisor = TaskSupervisor(max_concurrent=1)
    active = 0
    max_active = 0

    async def work(_cancellation):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    await supervisor.submit("one", work)
    await supervisor.submit("two", work)
    await asyncio.sleep(0)
    assert supervisor.capacity_snapshot(fallback_slots=4) == {
        "slots": 1,
        "active": 1,
        "waiting": 1,
    }
    assert await supervisor.wait("one") == "ok"
    assert await supervisor.wait("two") == "ok"
    assert max_active == 1
    await supervisor.close()


@pytest.mark.asyncio
async def test_supervisor_cancellation_propagates_reason() -> None:
    supervisor = TaskSupervisor()
    started = asyncio.Event()
    seen = []

    async def work(cancellation):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            seen.append(cancellation.reason)

    await supervisor.submit("cancel-me", work)
    await started.wait()
    assert await supervisor.cancel("cancel-me", "requested")
    with pytest.raises(asyncio.CancelledError):
        await supervisor.wait("cancel-me")
    assert seen == ["requested"]
    await supervisor.close()
