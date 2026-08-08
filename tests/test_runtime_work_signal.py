import asyncio
import threading
import time

import pytest

from joyhousebot.runtime.work_signal import RuntimeWorkSignal


class _NotifyingStore:
    def __init__(self) -> None:
        self.event = threading.Event()

    def wait_for_work(self, timeout: float) -> bool:
        notified = self.event.wait(timeout)
        self.event.clear()
        return notified


class _IdleStore:
    """Never notifies; records every fallback timeout it is asked to wait."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def wait_for_work(self, timeout: float) -> bool:
        self.timeouts.append(timeout)
        time.sleep(timeout)
        return False


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


@pytest.mark.asyncio
async def test_idle_poll_waits_back_off_and_reset_on_activity() -> None:
    store = _IdleStore()
    signal = RuntimeWorkSignal(store, fallback_poll_seconds=0.1, max_poll_seconds=0.4)
    await signal.start()
    try:
        # Let several idle poll cycles elapse; the wait grows exponentially.
        await asyncio.sleep(0.75)
        assert store.timeouts[0] == pytest.approx(0.1)
        assert store.timeouts[1] == pytest.approx(0.2)
        assert store.timeouts[2] == pytest.approx(0.4)
        assert all(timeout <= 0.4 + 1e-9 for timeout in store.timeouts)

        # Observed work returns the fallback to the fast path.
        signal.note_activity()
        recorded = len(store.timeouts)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(
                timeout == pytest.approx(0.1) for timeout in store.timeouts[recorded:]
            ):
                break
            await asyncio.sleep(0.02)
        assert any(timeout == pytest.approx(0.1) for timeout in store.timeouts[recorded:])
    finally:
        await signal.close()
