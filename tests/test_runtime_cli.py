"""Lifecycle tests for Runtime process entrypoints."""

from __future__ import annotations

import asyncio

import pytest

from joyhousebot.cli.runtime import _release_worker_presence, _run_service_until_stopped


@pytest.mark.asyncio
async def test_service_cancellation_runs_worker_shutdown() -> None:
    stopped = asyncio.Event()

    class BlockingService:
        async def run(self) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    task = asyncio.create_task(_run_service_until_stopped(BlockingService()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_signal_path_releases_worker_presence_before_cancellation() -> None:
    released: list[str] = []

    class Store:
        def unregister_runtime_worker(self, worker_id: str) -> None:
            released.append(worker_id)

    class Runtime:
        worker_id = "worker-a"
        store = Store()

    class Service:
        runtime = Runtime()

    await _release_worker_presence(Service())
    assert released == ["worker-a"]
