"""End-to-end schedule occurrence, Run, and Channel delivery projections."""

from __future__ import annotations

from pathlib import Path

import pytest

from joyhousebot.channels.repository import ChannelRepository
from joyhousebot.cron.service import CronService
from joyhousebot.cron.types import (
    CronPolicy,
    CronSchedule,
    schedule_run_prompt,
    schedule_run_session_id,
)
from joyhousebot.runtime.models import AgentEvent
from tests.support.postgres_store import PostgresTestStore


def _create_run(store: PostgresTestStore, job, run_id: str) -> str:  # noqa: ANN001
    store.create_runtime_run(
        run_id=run_id,
        user_id=job.user_id,
        session_id=schedule_run_session_id(job),
        agent_id=job.agent_id or "default",
        kind="agent",
        prompt=schedule_run_prompt(job),
        options={
            "metadata": {
                "schedule_occurrence_id": job.state.occurrence_id,
                "schedule_payload_kind": job.payload.kind,
                "_runtime_schedule_submission_ready": False,
            }
        },
    )
    return run_id


def _finish_run(
    store: PostgresTestStore,
    run_id: str,
    *,
    status: str,
    content: str | None = None,
) -> None:
    error = None if status == "completed" else {"message": content or status}
    result = {"content": content} if status == "completed" else None
    event = AgentEvent(
        run_id=run_id,
        type=f"run.{status}",
        status=status,
        summary=content,
        data={"content": content} if status == "completed" else {"error": content},
    )
    assert store.finish_runtime_run(
        run_id,
        status=status,
        event=event,
        result=result,
        error=error,
    ) is not None


def _make_retry_due(store: PostgresTestStore, occurrence_id: str) -> None:
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE schedule_occurrences
               SET next_attempt_at_ms=(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint-1
               WHERE occurrence_id=%s""",
            (occurrence_id,),
        )


@pytest.mark.asyncio
async def test_agent_monitor_defers_while_target_session_is_busy(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "monitor-busy.db")
    store.create_runtime_run(
        run_id="foreground-run",
        user_id="user-1",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt="foreground",
        options={},
    )
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        submitted.append(_create_run(store, job, "monitor-run"))
        return "monitor-run"

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="main session monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Check whether anything needs attention.",
        payload_kind="agent_monitor",
        session_mode="main",
        session_id="main",
        busy_backoff_ms=1_000,
        user_id="user-1",
    )

    assert job.policy.misfire_policy == "skip"
    assert job.policy.overlap_policy == "skip"
    assert await cron.run_job(job.id, user_id="user-1")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "retry_wait"
    assert occurrence["submitAttempt"] == 0
    assert occurrence["error"] == "target monitor session is busy"
    assert submitted == []

    assert store.update_runtime_run("foreground-run", status="completed")
    _make_retry_due(store, occurrence["id"])
    claimed = cron._claim_due_retries()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "submitted"
    assert occurrence["attempt"] == 1
    assert occurrence["submitAttempt"] == 1
    assert submitted == ["monitor-run"]
    assert store.get_runtime_run("monitor-run").session_id == "main"


@pytest.mark.asyncio
async def test_monitor_active_hours_skip_automatic_tick_but_not_manual_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PostgresTestStore(tmp_path / "monitor-active-hours.db")
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = f"active-hours-run-{len(submitted) + 1}"
        submitted.append(_create_run(store, job, run_id))
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="working-hours monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Check within active hours.",
        payload_kind="agent_monitor",
        active_hours={"start": "09:00", "end": "17:00", "timezone": "UTC"},
        user_id="user-1",
    )
    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            "UPDATE schedules SET next_run_at_ms=0 WHERE schedule_id=%s", (job.id,)
        )
    monkeypatch.setattr(
        "joyhousebot.cron.service.is_within_active_hours", lambda *_args: False
    )

    claimed = cron._claim_due_jobs()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "skipped_inactive_hours"
    assert submitted == []

    assert await cron.run_job(job.id, user_id="user-1")
    assert submitted == ["active-hours-run-1"]


@pytest.mark.asyncio
async def test_agent_monitor_quiet_result_suppresses_channel_delivery(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "monitor-quiet.db")
    outbox = ChannelRepository(store)

    async def on_job(job) -> str:  # noqa: ANN001
        return _create_run(store, job, "quiet-monitor-run")

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="quiet monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Check my pending tasks.",
        payload_kind="agent_monitor",
        deliver=True,
        channel="test",
        to="chat-1",
        user_id="user-1",
    )

    assert await cron.run_job(job.id, user_id="user-1")
    run = store.get_runtime_run("quiet-monitor-run")
    assert run is not None
    assert run.session_id == f"monitor:{job.id}"
    assert "respond exactly with: NO_ACTION" in run.prompt
    _finish_run(store, run.run_id, status="completed", content="NO_ACTION")

    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "completed"
    assert occurrence["deliveryStatus"] == "suppressed"
    assert occurrence["deliveryOutboundId"] is None
    assert outbox.outbox_size() == 0


@pytest.mark.asyncio
async def test_monitor_scratch_is_versioned_private_and_frozen_per_occurrence(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "monitor-scratch.db")
    captured: list[dict[str, object]] = []

    async def on_job(job) -> str:  # noqa: ANN001
        captured.append(cron.monitor_run_context(job))
        return _create_run(store, job, f"scratch-monitor-run-{len(captured)}")

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="stateful monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Track the last checked item.",
        payload_kind="agent_monitor",
        user_id="user-1",
        policy=CronPolicy(max_run_retries=1, retry_backoff_ms=1_000),
    )
    initial = cron.get_monitor_scratch(job.id, user_id="user-1")
    assert initial is not None
    assert initial["revision"] == 0
    assert initial["content"] == ""
    assert cron.get_monitor_scratch(job.id, user_id="user-2") is None

    first = cron.update_monitor_scratch(
        job.id,
        user_id="user-1",
        content="last item: 41",
        expected_revision=0,
        actor_type="api",
        actor_id="user-1",
    )
    assert first is not None and first["revision"] == 1
    with pytest.raises(ValueError, match="revision changed"):
        cron.update_monitor_scratch(
            job.id,
            user_id="user-1",
            content="lost update",
            expected_revision=0,
            actor_type="api",
            actor_id="user-1",
        )

    assert await cron.run_job(job.id, user_id="user-1")
    assert captured == [
        {
            "scratch": "last item: 41",
            "scratch_revision": 1,
            "observation_hash": None,
            "observation": {},
        }
    ]
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["monitorScratchRevision"] == 1
    second = cron.update_monitor_scratch(
        job.id,
        user_id="user-1",
        content="last item: 42",
        expected_revision=1,
        actor_type="api",
        actor_id="user-1",
    )
    assert second is not None and second["revision"] == 2
    _finish_run(store, "scratch-monitor-run-1", status="failed", content="retry me")
    _make_retry_due(store, occurrence["id"])
    claimed = cron._claim_due_retries()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    assert captured[1]["scratch"] == "last item: 41"
    assert captured[1]["scratch_revision"] == 1
    revisions = cron.list_monitor_scratch_revisions(job.id, user_id="user-1")
    assert revisions is not None
    assert [(row["revision"], row["content"]) for row in revisions] == [
        (2, "last item: 42"),
        (1, "last item: 41")
    ]


@pytest.mark.asyncio
async def test_runtime_attention_preflight_skips_unchanged_without_model_run(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "monitor-preflight.db")
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = f"attention-monitor-{len(submitted) + 1}"
        submitted.append(_create_run(store, job, run_id))
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="runtime attention",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="Explain new Runtime issues.",
        payload_kind="agent_monitor",
        preflight_mode="runtime_attention",
        user_id="user-1",
    )

    assert await cron.run_job(job.id, user_id="user-1")
    first = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert first["status"] == "skipped_unchanged"
    assert first["monitorObservationHash"]
    assert first["monitorObservation"]["recent_run_failures"]["total"] == 0
    assert submitted == []

    store.create_runtime_run(
        run_id="foreground-failure",
        user_id="user-1",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt="foreground",
        options={},
    )
    _finish_run(store, "foreground-failure", status="failed", content="provider failed")
    assert await cron.run_job(job.id, user_id="user-1")
    changed = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert changed["status"] == "submitted"
    assert changed["monitorObservation"]["recent_run_failures"]["total"] == 1
    assert submitted == ["attention-monitor-1"]

    _finish_run(store, submitted[0], status="completed", content="warning delivered")
    assert await cron.run_job(job.id, user_id="user-1")
    unchanged = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert unchanged["status"] == "skipped_unchanged"
    assert submitted == ["attention-monitor-1"]


def test_runtime_attention_preflight_decision_is_frozen_before_submission(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "monitor-preflight-fence.db")
    store.create_runtime_run(
        run_id="failure-before-monitor",
        user_id="user-1",
        session_id="main",
        agent_id="default",
        kind="agent",
        prompt="foreground",
        options={},
    )
    _finish_run(store, "failure-before-monitor", status="failed", content="failed")
    cron = CronService(store, worker_id="scheduler")
    job = cron.add_job(
        name="fenced attention",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="explain failures",
        payload_kind="agent_monitor",
        preflight_mode="runtime_attention",
        user_id="user-1",
    )
    claimed = cron.repository.claim_one(
        job.id,
        worker_id=cron.worker_id,
        lease_ms=cron.lease_ms,
        manual=True,
    )
    assert claimed is not None and claimed.state.occurrence_id
    values = {
        "schedule_id": claimed.id,
        "occurrence_id": claimed.state.occurrence_id,
        "user_id": claimed.user_id,
        "worker_id": cron.worker_id,
        "lease_version": claimed.lease_version,
    }
    first = cron.monitors.evaluate_runtime_attention(**values)
    second = cron.monitors.evaluate_runtime_attention(**values)
    assert first is not None and first["should_run"] is True
    assert second is not None and second["should_run"] is True
    assert second["reason"] == "reused frozen runtime attention preflight"


@pytest.mark.asyncio
async def test_scheduled_run_claim_waits_for_occurrence_link(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-submit-fence.db")
    blocked_claims: list[object | None] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = _create_run(store, job, "fenced-run")
        blocked_claims.append(
            store.claim_runtime_run(run_id, worker_id="agent", lease_seconds=30)
        )
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="fenced submit",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-1",
    )

    assert await cron.run_job(job.id, user_id="user-1")
    assert blocked_claims == [None]
    claimed = store.claim_runtime_run("fenced-run", worker_id="agent", lease_seconds=30)
    assert claimed is not None
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "submitted"
    assert occurrence["runId"] == "fenced-run"


@pytest.mark.asyncio
async def test_terminal_run_is_reconciled_when_submission_returns_late(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-submit-reconcile.db")

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = _create_run(store, job, "already-terminal")
        # Simulate an idempotent Runtime response that is already terminal.
        # Normal Workers are held by the submission-ready fence above.
        _finish_run(store, run_id, status="completed", content="already done")
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="reconcile terminal",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-1",
    )

    assert await cron.run_job(job.id, user_id="user-1")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "completed"
    assert occurrence["runIds"] == ["already-terminal"]
    assert cron.list_jobs(user_id="user-1")[0].state.last_status == "completed"


@pytest.mark.asyncio
async def test_terminal_run_projects_occurrence_and_channel_delivery(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-terminal.db")
    outbox = ChannelRepository(store)
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = f"run-{len(submitted) + 1}"
        submitted.append(_create_run(store, job, run_id))
        return run_id

    cron = CronService(
        store,
        on_job=on_job,
        worker_id="scheduler",
    )
    job = cron.add_job(
        name="daily report",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="report",
        deliver=True,
        channel="test",
        to="chat-1",
        user_id="user-1",
    )

    assert await cron.run_job(job.id, user_id="user-1")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "submitted"

    _finish_run(store, submitted[0], status="completed", content="report ready")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "completed"
    assert occurrence["runIds"] == submitted
    assert occurrence["deliveryStatus"] == "pending"

    first = outbox.claim(["test"], worker_id="channel", lease_ms=30_000)
    assert len(first) == 1
    failed = outbox.finish(
        first[0]["id"],
        worker_id="channel",
        lease_version=first[0]["lease_version"],
        success=False,
        error="temporary channel failure",
        max_attempts=3,
    )
    assert failed == ("pending", 1)
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["deliveryStatus"] == "pending"
    assert occurrence["deliveryError"] == "temporary channel failure"

    with store._pool.connection() as connection, connection.transaction():
        connection.execute(
            """UPDATE channel_outbox
               SET available_at_ms=(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint-1
               WHERE outbound_id=%s""",
            (first[0]["id"],),
        )
    second = outbox.claim(["test"], worker_id="channel", lease_ms=30_000)
    assert len(second) == 1
    assert outbox.finish(
        second[0]["id"],
        worker_id="channel",
        lease_version=second[0]["lease_version"],
        success=True,
        error=None,
        max_attempts=3,
    ) == ("sent", 1)
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["deliveryStatus"] == "sent"
    assert occurrence["deliveryError"] is None
    assert occurrence["deliveredAtMs"] is not None


@pytest.mark.asyncio
async def test_run_retry_is_opt_in_and_reuses_one_occurrence(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-run-retry.db")
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = f"run-{job.state.attempt}"
        submitted.append(_create_run(store, job, run_id))
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="retry run",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="retry",
        user_id="user-1",
        policy=CronPolicy(max_run_retries=1, retry_backoff_ms=1_000),
    )

    assert await cron.run_job(job.id, user_id="user-1")
    _finish_run(store, submitted[0], status="failed", content="model unavailable")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "retry_wait"
    assert occurrence["attempt"] == 1
    occurrence_id = occurrence["id"]

    _make_retry_due(store, occurrence_id)
    claimed = cron._claim_due_retries()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    _finish_run(store, submitted[1], status="completed", content="recovered")

    occurrences = cron.list_runs(user_id="user-1", job_id=job.id)
    assert len(occurrences) == 1
    assert occurrences[0]["id"] == occurrence_id
    assert occurrences[0]["status"] == "completed"
    assert occurrences[0]["attempt"] == 2
    assert occurrences[0]["runIds"] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_submission_retry_does_not_increment_run_attempt(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-submit-retry.db")
    calls = 0

    async def on_job(job) -> str:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("runtime API unavailable")
        return _create_run(store, job, "run-after-submit-retry")

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="retry submit",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-1",
        policy=CronPolicy(max_submit_attempts=2, retry_backoff_ms=1_000),
    )

    assert await cron.run_job(job.id, user_id="user-1")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "retry_wait"
    assert occurrence["attempt"] == 1
    assert occurrence["submitAttempt"] == 1

    _make_retry_due(store, occurrence["id"])
    claimed = cron._claim_due_retries()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "submitted"
    assert occurrence["attempt"] == 1
    assert occurrence["submitAttempt"] == 2
    assert occurrence["runIds"] == ["run-after-submit-retry"]


@pytest.mark.asyncio
async def test_terminal_submission_failure_is_enqueued_atomically(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-submit-failure.db")
    outbox = ChannelRepository(store)

    async def on_job(_job) -> str:  # noqa: ANN001
        raise RuntimeError("runtime API unavailable")

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    job = cron.add_job(
        name="failed submit",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        deliver=True,
        channel="test",
        to="chat-1",
        user_id="user-1",
        policy=CronPolicy(max_submit_attempts=1),
    )

    assert await cron.run_job(job.id, user_id="user-1")
    occurrence = cron.list_runs(user_id="user-1", job_id=job.id)[0]
    assert occurrence["status"] == "error"
    assert occurrence["runId"] is None
    assert occurrence["deliveryStatus"] == "pending"
    queued = outbox.claim(["test"], worker_id="channel", lease_ms=30_000)
    assert len(queued) == 1
    assert queued[0]["metadata"]["schedule_occurrence_id"] == occurrence["id"]
    assert "提交失败" in queued[0]["content"]


@pytest.mark.asyncio
async def test_misfire_and_overlap_policies_prevent_accidental_runs(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "schedule-trigger-policy.db")
    submitted: list[str] = []

    async def on_job(job) -> str:  # noqa: ANN001
        run_id = f"run-{len(submitted) + 1}"
        submitted.append(_create_run(store, job, run_id))
        return run_id

    cron = CronService(store, on_job=on_job, worker_id="scheduler")
    misfire = cron.add_job(
        name="skip stale",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-1",
        policy=CronPolicy(misfire_policy="skip", misfire_grace_ms=0),
    )
    now_ms = cron.repository.db_now_ms()
    cron.repository.set_enabled(
        misfire.id,
        True,
        user_id="user-1",
        next_run_at_ms=now_ms - 10_000,
        now_ms=now_ms,
    )
    claimed = cron._claim_due_jobs()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    assert cron.list_runs(user_id="user-1", job_id=misfire.id)[0]["status"] == (
        "skipped_misfire"
    )
    assert submitted == []

    overlap = cron.add_job(
        name="skip overlap",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        user_id="user-1",
        policy=CronPolicy(overlap_policy="skip"),
    )
    assert await cron.run_job(overlap.id, user_id="user-1")
    now_ms = cron.repository.db_now_ms()
    cron.repository.set_enabled(
        overlap.id,
        True,
        user_id="user-1",
        next_run_at_ms=now_ms - 10_000,
        now_ms=now_ms,
    )
    claimed = cron._claim_due_jobs()
    assert len(claimed) == 1
    await cron._execute_claimed_job(claimed[0])
    statuses = {
        item["status"] for item in cron.list_runs(user_id="user-1", job_id=overlap.id)
    }
    assert statuses == {"submitted", "skipped_overlap"}
    assert submitted == ["run-1"]
