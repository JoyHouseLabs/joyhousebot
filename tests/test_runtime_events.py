import asyncio
from pathlib import Path

import pytest

from porthouse.runtime.events import EventBroker
from porthouse.runtime.models import AgentEvent
from tests.support.postgres_store import PostgresTestStore


@pytest.mark.asyncio
async def test_event_subscription_observes_other_worker_via_durable_log(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "events.db")
    store.create_runtime_run(
        run_id="run-1",
        user_id="user-a",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt="hello",
        options={"prompt": "hello"},
    )
    subscriber = EventBroker(store)
    publisher = EventBroker(store)
    stream = subscriber.subscribe("run-1")
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await publisher.publish(AgentEvent(run_id="run-1", type="run.completed"))

    event = await asyncio.wait_for(pending, timeout=2)
    assert event.type == "run.completed"
    await stream.aclose()
