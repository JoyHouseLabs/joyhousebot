"""Regression tests for runtime concurrency, quota, and availability fixes."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from porthouse.agent.model_invoker import ModelInvokerMixin
from porthouse.agent.turn_engine import TurnEngineMixin
from porthouse.api.schemas import GraphTaskRequest, ScheduleSpec
from porthouse.cron.service import CronService
from porthouse.domain.schedules import CronSchedule
from porthouse.runtime import events as runtime_events
from porthouse.runtime.coordinator import RuntimeCoordinatorMixin
from porthouse.runtime.events import EventBroker
from porthouse.runtime.models import AgentEvent, AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


class _IdleAgent:
    async def process_direct(self, content: str, **_kwargs) -> str:
        await asyncio.Event().wait()
        return content


# H1: per-user in-flight quota and submission rate limit


@pytest.mark.asyncio
async def test_submit_run_enforces_per_user_in_flight_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTHOUSE_MAX_RUNS_PER_USER", "2")
    monkeypatch.setenv("PORTHOUSE_RUN_SUBMIT_PER_MINUTE", "100")
    runtime = NativeAgentRuntime(
        agent=_IdleAgent(), store=PostgresTestStore(tmp_path / "quota.db")
    )
    options = lambda: AgentOptions(  # noqa: E731
        prompt="block", user_id="user-a", session_id="s1", agent_id="default"
    )
    first = await runtime.submit_run(options())
    second = await runtime.submit_run(options())
    assert first.status in {"queued", "running"}
    assert second.status in {"queued", "running"}
    with pytest.raises(ValueError, match="in-flight run limit"):
        await runtime.submit_run(options())
    await runtime.close()


@pytest.mark.asyncio
async def test_submit_run_enforces_submission_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORTHOUSE_MAX_RUNS_PER_USER", "100")
    monkeypatch.setenv("PORTHOUSE_RUN_SUBMIT_PER_MINUTE", "2")
    runtime = NativeAgentRuntime(
        agent=_IdleAgent(), store=PostgresTestStore(tmp_path / "rate.db")
    )
    options = lambda: AgentOptions(  # noqa: E731
        prompt="block", user_id="user-a", session_id="s1", agent_id="default"
    )
    await runtime.submit_run(options())
    await runtime.submit_run(options())
    with pytest.raises(ValueError, match="rate limit"):
        await runtime.submit_run(options())
    # Another user is unaffected.
    await runtime.submit_run(
        AgentOptions(prompt="block", user_id="user-b", session_id="s1", agent_id="default")
    )
    await runtime.close()


# H2: cron schedule limits


def test_cron_service_rejects_too_frequent_schedules(tmp_path: Path) -> None:
    service = CronService(PostgresTestStore(tmp_path / "cron.db"))
    with pytest.raises(ValueError, match="60000"):
        service.add_job(
            name="fast", schedule=CronSchedule(kind="every", every_ms=5_000), user_id="u"
        )
    with pytest.raises(ValueError, match="60s"):
        service.add_job(
            name="dense",
            schedule=CronSchedule(kind="cron", expr="*/30 * * * * *"),
            user_id="u",
        )
    job = service.add_job(
        name="ok", schedule=CronSchedule(kind="every", every_ms=60_000), user_id="u"
    )
    assert job.id


def test_cron_service_enforces_per_user_job_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import porthouse.cron.service as cron_service_module

    monkeypatch.setattr(cron_service_module, "MAX_JOBS_PER_USER", 2)
    service = CronService(PostgresTestStore(tmp_path / "cron.db"))
    for index in range(2):
        service.add_job(
            name=f"job-{index}",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            user_id="u",
        )
    with pytest.raises(ValueError, match="job limit"):
        service.add_job(
            name="overflow",
            schedule=CronSchedule(kind="every", every_ms=60_000),
            user_id="u",
        )
    # Other users are unaffected.
    service.add_job(
        name="other", schedule=CronSchedule(kind="every", every_ms=60_000), user_id="v"
    )


# M8: scheduler shutdown settles claimed occurrences


@pytest.mark.asyncio
async def test_cron_shutdown_finishes_claimed_occurrence(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "cron.db")

    async def on_job(job) -> None:
        await asyncio.Event().wait()

    service = CronService(store, on_job=on_job, worker_id="w")
    job = service.add_job(
        name="job", schedule=CronSchedule(kind="every", every_ms=60_000), user_id="u"
    )
    claimed = service.repository.claim_one(
        job.id,
        worker_id=service.worker_id,
        lease_ms=service.lease_ms,
        manual=True,
    )
    assert claimed is not None
    task = asyncio.create_task(service._execute_claimed_job(claimed))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    occurrences = service.list_runs(user_id="u", job_id=job.id)
    assert occurrences[0]["status"] == "error"
    assert occurrences[0]["error"] == "worker shutdown"


@pytest.mark.asyncio
async def test_cron_stop_and_wait_stopped(tmp_path: Path) -> None:
    service = CronService(PostgresTestStore(tmp_path / "cron.db"))
    await service.start()
    assert service._timer_task is not None and not service._timer_task.done()
    service.stop()
    await service.wait_stopped()
    assert service._timer_task is None


# H3: graph task timeout schema cap and graph-level total timeout


def test_graph_task_timeout_capped_at_one_hour() -> None:
    with pytest.raises(ValidationError):
        GraphTaskRequest(id="t", prompt="p", timeout_seconds=7201)
    assert GraphTaskRequest(id="t", prompt="p", timeout_seconds=3600)


def test_schedule_every_ms_requires_at_least_one_minute() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(kind="every", every_ms=1000)
    assert ScheduleSpec(kind="every", every_ms=60_000)


def test_graph_deadline_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator = object.__new__(RuntimeCoordinatorMixin)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    stale = SimpleNamespace(status="running", started_at=old, created_at=old)
    fresh = SimpleNamespace(status="running", started_at=recent, created_at=recent)
    done = SimpleNamespace(status="completed", started_at=old, created_at=old)
    assert coordinator._graph_deadline_exceeded(stale)
    assert not coordinator._graph_deadline_exceeded(fresh)
    assert not coordinator._graph_deadline_exceeded(done)
    monkeypatch.setenv("PORTHOUSE_GRAPH_TIMEOUT_SECONDS", "999999")
    assert not coordinator._graph_deadline_exceeded(stale)


# M1: bounded run identity cache


@pytest.mark.asyncio
async def test_run_identity_cache_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime_events, "_RUN_IDENTITY_MAX", 3)
    store = PostgresTestStore(tmp_path / "events.db")
    broker = EventBroker(store)
    for index in range(5):
        run_id = f"run-{index}"
        store.create_runtime_run(
            run_id=run_id,
            user_id="u",
            session_id="s",
            agent_id="default",
            kind="agent",
            prompt="p",
            options={},
        )
        await broker.publish(AgentEvent(run_id=run_id, type="run.queued"))
    assert len(broker._run_identity) <= 3


# M2: subscriptions end for terminal runs


@pytest.mark.asyncio
async def test_subscription_ends_after_terminal_run(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "events.db")
    store.create_runtime_run(
        run_id="run-1",
        user_id="u",
        session_id="s",
        agent_id="default",
        kind="agent",
        prompt="p",
        options={},
    )
    broker = EventBroker(store)
    await broker.publish(AgentEvent(run_id="run-1", type="run.queued"))
    store.update_runtime_run("run-1", status="completed")
    await broker.publish(AgentEvent(run_id="run-1", type="run.completed"))

    received = []

    async def collect() -> None:
        async for event in broker.subscribe("run-1"):
            received.append(event.type)

    await asyncio.wait_for(collect(), timeout=5)
    assert received == ["run.queued", "run.completed"]


@pytest.mark.asyncio
async def test_subscription_for_missing_run_ends(tmp_path: Path) -> None:
    broker = EventBroker(PostgresTestStore(tmp_path / "events.db"))

    async def collect() -> None:
        async for _ in broker.subscribe("missing-run"):
            pass

    await asyncio.wait_for(collect(), timeout=5)


# M4: model cooldown only tracks configured models


def test_model_cooldown_ignores_untracked_models() -> None:
    invoker = object.__new__(ModelInvokerMixin)
    invoker._tracked_models = {"primary", "fallback"}
    invoker._model_failure_count = {}
    invoker._model_cooldown_until = {}
    invoker._mark_model_failure("attacker-controlled-model")
    assert "attacker-controlled-model" not in invoker._model_cooldown_until
    assert "attacker-controlled-model" not in invoker._model_failure_count
    invoker._mark_model_failure("primary")
    assert "primary" in invoker._model_cooldown_until
    invoker._mark_model_success("primary")
    assert "primary" not in invoker._model_cooldown_until


# Memory scope: metadata-sourced user ids must match the authenticated identity


def _metadata_scope_config() -> SimpleNamespace:
    return SimpleNamespace(
        tools=SimpleNamespace(
            retrieval=SimpleNamespace(
                memory_scope="user",
                memory_user_id_from="metadata",
                memory_user_id_metadata_key="user_id",
            )
        )
    )


def test_memory_scope_metadata_falls_back_to_sender_without_principal() -> None:
    engine = object.__new__(TurnEngineMixin)
    engine.config = _metadata_scope_config()
    scope = engine._resolve_memory_scope_key(
        "telegram:chat-1",
        sender_id="attacker",
        metadata={"user_id": "victim"},
        run_context=None,
    )
    assert scope == "telegram:attacker"


def test_memory_scope_metadata_mismatched_principal_is_not_trusted() -> None:
    engine = object.__new__(TurnEngineMixin)
    engine.config = _metadata_scope_config()
    principal = SimpleNamespace(user_id="", agent_id="default")
    scope = engine._resolve_memory_scope_key(
        "telegram:chat-1",
        sender_id="attacker",
        metadata={"user_id": "victim"},
        run_context=principal,
    )
    assert scope == "telegram:attacker"


def test_memory_scope_shared_maps_to_cluster_shared_db_scope() -> None:
    engine = object.__new__(TurnEngineMixin)
    engine.config = SimpleNamespace(
        tools=SimpleNamespace(retrieval=SimpleNamespace(memory_scope="shared"))
    )
    scope = engine._resolve_memory_scope_key(
        "telegram:chat-1",
        sender_id="user-a",
        metadata=None,
        run_context=None,
    )
    assert scope == "shared"


def test_memory_scope_unconfigured_returns_none() -> None:
    engine = object.__new__(TurnEngineMixin)
    engine.config = SimpleNamespace(tools=SimpleNamespace(retrieval=None))
    assert engine._resolve_memory_scope_key("telegram:chat-1") is None
