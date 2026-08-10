"""Contracts for optional model-facing Runtime controls."""

from types import SimpleNamespace

import pytest
from joyhousebot_capability_runtime_control import plugin as runtime_control

from joyhousebot.capabilities import CapabilityPluginRegistry
from joyhousebot.capabilities.services import CapabilityServiceBroker
from joyhousebot.cron.service import CronService
from joyhousebot.domain.capabilities import InvocationStatus
from joyhousebot.domain.schedules import CronSchedule
from joyhousebot.extension_sdk import CapabilityContext
from joyhousebot.extension_sdk.tools import ToolOutput
from tests.support.postgres_store import PostgresTestStore


class _FakeRuntimeServices:
    def __init__(self) -> None:
        self.delivery = self
        self.child_runs = self
        self.messages = []
        self.spawned = []

    async def send(self, context, **kwargs):  # noqa: ANN001
        self.messages.append({"user_id": context.user_id, **kwargs})
        return {
            "channel": kwargs["channel"],
            "chat_id": kwargs["chat_id"],
            "outbound_id": f"action:{context.action_id}",
        }

    async def spawn(self, context, **kwargs):  # noqa: ANN001
        self.spawned.append({"user_id": context.user_id, **kwargs})
        return ToolOutput(
            content="queued",
            summary="child queued",
            data={"child_run_id": "child-a"},
            operation={"run_id": "child-a", "status": "queued"},
            status=InvocationStatus.ACCEPTED,
        )

    async def reconcile(self, context, operation):  # noqa: ANN001
        return SimpleNamespace(status="pending", operation=operation)


def _context(services, **overrides):
    values = {
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "agent-a",
        "action_id": "action-a",
        "idempotency_key": "action:action-a",
        "services": services,
        "metadata": {
            "permissions": [
                "channel.send",
                "runs.spawn",
                "schedule.manage",
                "monitor.scratch",
            ],
            "channel": "email",
            "chat_id": "person@example.com",
        },
    }
    values.update(overrides)
    return CapabilityContext(**values)


def test_runtime_control_extension_registers_four_governed_capabilities() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(runtime_control.RuntimeControlPlugin())
    definitions = {item.ref.capability_id: item for item in registry.list_capabilities()}
    assert set(definitions) == {"cron", "message", "monitor_scratch", "spawn"}
    assert definitions["message"].side_effect == "external"
    assert definitions["message"].idempotent is False
    assert definitions["spawn"].retryable is True
    assert definitions["cron"].ref.plugin_id == (
        "capability-runtime-control"
    )


@pytest.mark.asyncio
async def test_message_handler_uses_frozen_origin_and_action() -> None:
    services = _FakeRuntimeServices()
    result = await runtime_control.MessageHandler().execute(
        _context(services),
        {"content": "hello"},
    )
    assert result.success is True
    assert result.write_receipt.action_id == "action-a"
    assert services.messages[0]["channel"] == "email"
    assert services.messages[0]["chat_id"] == "person@example.com"


@pytest.mark.asyncio
async def test_message_handler_requires_action_before_delivery() -> None:
    services = _FakeRuntimeServices()
    result = await runtime_control.MessageHandler().execute(
        _context(services, action_id=None, idempotency_key=None),
        {"content": "must-not-send"},
    )
    assert result.success is False
    assert result.error["code"] == "ACTION_IDENTITY_REQUIRED"
    assert services.messages == []


@pytest.mark.asyncio
async def test_spawn_handler_returns_reconcilable_accepted_operation() -> None:
    services = _FakeRuntimeServices()
    result = await runtime_control.SpawnHandler().execute(
        _context(services),
        {"task": "research market"},
    )
    assert result.success is True
    assert result.status == "accepted"
    assert result.operation["run_id"] == "child-a"
    assert result.write_receipt.provider_operation_id == "child-a"
    assert services.spawned[0]["task"] == "research market"


@pytest.mark.asyncio
async def test_core_delivery_service_freezes_outbox_identity() -> None:
    delivered = []

    async def sink(message):  # noqa: ANN001
        delivered.append(message)

    services = CapabilityServiceBroker(None, outbound_sink=sink)
    context = _context(services)
    target = await services.delivery.send(
        context,
        content="hello",
        channel="email",
        chat_id="person@example.com",
    )
    assert target["outbound_id"] == "action:action-a"
    assert delivered[0].metadata["_runtime_outbound_id"] == "action:action-a"
    assert delivered[0].metadata["idempotency_key"] == "action:action-a"


@pytest.mark.asyncio
async def test_schedule_handler_is_user_scoped_and_idempotent(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "runtime-control-schedules.db")
    schedule_service = CronService(store)
    services = CapabilityServiceBroker(store, schedule_service=schedule_service)
    handler = runtime_control.ScheduleHandler()
    owner = _context(services)
    created = await handler.execute(
        owner,
        {"action": "add", "message": "private-a", "every_seconds": 60},
    )
    repeated = await handler.execute(
        owner,
        {"action": "add", "message": "private-a", "every_seconds": 60},
    )
    assert created.success is True
    assert repeated.output["job_id"] == created.output["job_id"]
    assert len(schedule_service.list_jobs(user_id="user-a")) == 1

    other = _context(
        services,
        user_id="user-b",
        action_id="action-b",
        idempotency_key="action:action-b",
    )
    listed = await handler.execute(other, {"action": "list"})
    assert listed.success is True
    assert listed.output["jobs"] == []
    removed = await handler.execute(
        other,
        {"action": "remove", "job_id": created.output["job_id"]},
    )
    assert removed.success is True
    assert removed.output["removed"] is False


@pytest.mark.asyncio
async def test_schedule_handler_rejects_subminute_interval(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "runtime-control-short-schedule.db")
    services = CapabilityServiceBroker(store, schedule_service=CronService(store))
    result = await runtime_control.ScheduleHandler().execute(
        _context(services),
        {"action": "add", "message": "too fast", "every_seconds": 30},
    )
    assert result.success is False
    assert result.error["code"] == "INVALID_PARAMETERS"


@pytest.mark.asyncio
async def test_monitor_scratch_handler_is_bound_to_monitor_run(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "runtime-control-monitor.db")
    schedule_service = CronService(store)
    job = schedule_service.add_job(
        name="private monitor",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="check",
        payload_kind="agent_monitor",
        user_id="user-a",
    )
    services = CapabilityServiceBroker(store, schedule_service=schedule_service)
    context = _context(
        services,
        metadata={
            "permissions": ["monitor.scratch"],
            "schedule_id": job.id,
            "schedule_payload_kind": "agent_monitor",
        },
    )
    handler = runtime_control.MonitorScratchHandler()
    current = await handler.execute(context, {"action": "get"})
    assert current.success is True
    assert current.output["revision"] == 0
    updated = await handler.execute(
        context,
        {"action": "update", "content": "cursor=9", "expected_revision": 0},
    )
    assert updated.success is True
    assert updated.output["revision"] == 1

    ordinary = _context(services)
    denied = await handler.execute(ordinary, {"action": "get"})
    assert denied.success is False
    assert denied.error["code"] == "MONITOR_CONTEXT_REQUIRED"
