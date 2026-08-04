import asyncio
import threading

import pytest

from joyhousebot.runtime.work_signal import RuntimeWorkSignal


class _NotifyingStore:
    def __init__(self) -> None:
        self.event = threading.Event()

    def wait_for_work(self, timeout: float) -> bool:
        notified = self.event.wait(timeout)
        self.event.clear()
        return notified


@pytest.mark.asyncio
async def test_one_process_signal_broadcasts_a_postgres_notification_to_all_dispatchers() -> None:
    store = _NotifyingStore()
    signal = RuntimeWorkSignal(store, fallback_poll_seconds=0.1)
    await signal.start()
    try:
        initial = await signal.wait(0)
        assert initial.source == "recovery"

        run_waiter = asyncio.create_task(signal.wait(initial.generation))
        task_waiter = asyncio.create_task(signal.wait(initial.generation))
        await asyncio.sleep(0)
        store.event.set()

        run_wake, task_wake = await asyncio.gather(run_waiter, task_waiter)
        assert run_wake.source == task_wake.source == "pg_notify"
        assert run_wake.generation == task_wake.generation
    finally:
        await signal.close()


@pytest.mark.asyncio
async def test_signal_marks_listener_timeout_as_durable_poll_recovery() -> None:
    signal = RuntimeWorkSignal(_NotifyingStore(), fallback_poll_seconds=0.1)
    await signal.start()
    try:
        initial = await signal.wait(0)
        wake = await signal.wait(initial.generation)
        assert wake.source == "poll"
    finally:
        await signal.close()
