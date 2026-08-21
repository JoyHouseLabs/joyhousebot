import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config
from joyhousebot.cron.service import CronService
from joyhousebot.domain.schedules import CronSchedule
from joyhousebot.services.memory.store import MemoryStore
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository
from tests.support.postgres_store import PostgresTestStore, require_postgres


def test_memory_append_is_atomic_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "cluster.db"
    stores = [PostgresTestStore(path) for _ in range(4)]

    def increment(index: int) -> None:
        store = stores[index % len(stores)]
        MemoryStore(store, "user:atomic:agent:default").append_history(f"entry-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(100)))

    history = MemoryStore(stores[0], "user:atomic:agent:default").read_relative("HISTORY.md")
    assert sum(1 for line in history.splitlines() if line.startswith("entry-")) == 100


@pytest.mark.asyncio
async def test_cron_is_user_scoped_and_one_gateway_claims_each_occurrence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cron.db"
    store_a = PostgresTestStore(path)
    store_b = PostgresTestStore(path)
    executed: list[tuple[str, str]] = []

    async def on_job(job):
        executed.append((job.id, job.user_id))
        return "ok"

    cron_a = CronService(store_a, on_job=on_job, worker_id="a")
    cron_b = CronService(store_b, on_job=on_job, worker_id="b")
    job_a = cron_a.add_job(
        name="a", schedule=CronSchedule(kind="every", every_ms=60_000), user_id="user-a"
    )
    job_b = cron_b.add_job(
        name="b", schedule=CronSchedule(kind="every", every_ms=60_000), user_id="user-b"
    )
    assert [job.id for job in cron_a.list_jobs(user_id="user-a")] == [job_a.id]
    assert [job.id for job in cron_b.list_jobs(user_id="user-b")] == [job_b.id]
    assert not cron_b.remove_job(job_a.id, user_id="user-b")

    # Schedule due/lease comparisons are owned by the PostgreSQL clock.  Using
    # the client clock here makes the test flaky when the database container
    # trails the host by even a few milliseconds.
    now_ms = cron_a.repository.db_now_ms()

    cron_a.repository.set_enabled(
        job_a.id,
        True,
        user_id="user-a",
        next_run_at_ms=now_ms - 1,
        now_ms=now_ms,
    )
    claimed_a, claimed_b = await asyncio.gather(
        asyncio.to_thread(cron_a._claim_due_jobs),
        asyncio.to_thread(cron_b._claim_due_jobs),
    )
    claimed = claimed_a + claimed_b
    assert len(claimed) == 1
    owner = cron_a if claimed_a else cron_b
    await owner._execute_claimed_job(claimed[0])
    assert executed == [(job_a.id, "user-a")]
    assert len(cron_a.list_runs(user_id="user-a", job_id=job_a.id)) == 1
    assert cron_b.list_runs(user_id="user-b", job_id=job_a.id) == []


@pytest.mark.asyncio
async def test_manual_run_preserves_disabled_schedule_state(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "manual-cron.db")
    executed: list[str] = []

    async def on_job(job):
        executed.append(job.id)
        return "run-manual"

    cron = CronService(store, on_job=on_job, worker_id="manual-worker")
    job = cron.add_job(
        name="disabled",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-a",
    )
    cron.enable_job(job.id, False, user_id="user-a")

    assert await cron.run_job(job.id, force=False, user_id="user-a") is False
    assert await cron.run_job(job.id, force=True, user_id="user-a") is True
    stored = cron.list_jobs(include_disabled=True, user_id="user-a")[0]
    assert executed == [job.id]
    assert stored.enabled is False
    assert stored.state.next_run_at_ms is None


@pytest.mark.asyncio
async def test_manual_run_keeps_future_one_shot_schedule(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "manual-at-cron.db")
    executed: list[str] = []

    async def on_job(job):
        executed.append(job.id)
        return "run-manual-at"

    cron = CronService(store, on_job=on_job, worker_id="manual-worker")
    at_ms = cron.repository.db_now_ms() + 3_600_000
    job = cron.add_job(
        name="future-at",
        schedule=CronSchedule(kind="at", at_ms=at_ms),
        user_id="user-a",
    )

    # A manual "run now" executes immediately but must not consume the
    # planned future occurrence of a one-shot schedule.
    assert await cron.run_job(job.id, user_id="user-a") is True
    stored = cron.list_jobs(include_disabled=True, user_id="user-a")[0]
    assert executed == [job.id]
    assert stored.enabled is True
    assert stored.state.next_run_at_ms == at_ms


def test_memory_is_shared_but_user_isolated(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store_a = PostgresTestStore(path)
    store_b = PostgresTestStore(path)
    memory_a = MemoryStore(store_a, "user:user-a:agent:default")
    memory_b = MemoryStore(store_b, "user:user-a:agent:default")
    other_user = MemoryStore(store_b, "user:user-b:agent:default")
    memory_a.write_long_term("likes distributed systems")
    assert memory_b.read_long_term() == "likes distributed systems"
    assert other_user.read_long_term() == ""


def test_knowledge_is_shared_but_user_isolated(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.db"
    repository_a = KnowledgeRepository(PostgresTestStore(path))
    repository_b = KnowledgeRepository(PostgresTestStore(path))
    repository_a.index_document(
        doc_id="doc-a",
        user_id="user-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Distributed state",
        chunks=[{"text": "PostgreSQL coordinates agent workers", "page": 1}],
    )

    assert (
        repository_b.search(user_id="user-a", query="agent workers", top_k=10)[0]["doc_id"]
        == "doc-a"
    )
    assert repository_b.search(user_id="user-b", query="agent workers", top_k=10) == []


def test_channel_leases_and_outbox_have_single_owner(tmp_path: Path) -> None:
    path = tmp_path / "leases.db"
    config = Config()
    channel_a = ChannelManager(
        config, runtime_store=PostgresTestStore(path), worker_id="a"
    )
    channel_b = ChannelManager(
        config, runtime_store=PostgresTestStore(path), worker_id="b"
    )
    acquired_a = channel_a._acquire_channel_lease("telegram")
    acquired_b = channel_b._acquire_channel_lease("telegram")
    assert [acquired_a, acquired_b].count(True) == 1
    owner = channel_a if acquired_a else channel_b
    non_owner = channel_b if acquired_a else channel_a
    owner._active_channels.add("telegram")
    non_owner._enqueue_cluster_outbound(
        OutboundMessage(channel="telegram", chat_id="chat-1", content="hello")
    )
    claimed = owner._claim_cluster_outbound()
    assert len(claimed) == 1
    assert claimed[0].content == "hello"
    assert non_owner._claim_cluster_outbound() == []
    owner._finish_cluster_outbound(claimed[0], success=True)
    assert owner.repository is not None
    assert owner.repository.outbox_size() == 0
    status = non_owner.get_status()["telegram"]
    assert status["running"] is True
    assert status["local_owner"] is False
    assert status["owner_worker_id"] == owner.worker_id


@pytest.mark.postgres
def test_postgres_memory_append_and_cron_fencing(tmp_path: Path) -> None:
    database_url = require_postgres()
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    store_a = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=2)
    store_b = PostgresRuntimeStore(database_url, min_pool_size=1, max_pool_size=2)
    scope = f"user:pytest-{time.time_ns()}:agent:default"
    try:

        def append(index_and_store):
            index, store = index_and_store
            MemoryStore(store, scope).append_history(f"entry-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, enumerate([store_a, store_b] * 25)))
        history = MemoryStore(store_a, scope).read_relative("HISTORY.md")
        assert sum(1 for line in history.splitlines() if line.startswith("entry-")) == 50

        cron_a = CronService(store_a, worker_id="pg-a")
        cron_b = CronService(store_b, worker_id="pg-b")
        job = cron_a.add_job(
            name="pg",
            schedule=CronSchedule(kind="at", at_ms=int(time.time() * 1000) + 60_000),
            user_id="pg-user",
        )
        now_ms = cron_a.repository.db_now_ms()

        cron_a.repository.set_enabled(
            job.id,
            True,
            user_id="pg-user",
            next_run_at_ms=now_ms - 1,
            now_ms=now_ms,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda cron: cron._claim_due_jobs(), [cron_a, cron_b]))
        # The shared integration database may contain unrelated due schedules;
        # this schedule itself must still be claimed exactly once.
        assert sum(item.id == job.id for items in claims for item in items) == 1
    finally:
        store_a.close()
        store_b.close()
