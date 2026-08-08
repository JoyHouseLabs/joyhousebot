from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.agent.tools.base import Tool
from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.capabilities.tool_adapter import ToolCapabilityAdapter, ToolOutput
from joyhousebot.config.schema import Config
from joyhousebot.contracts import OperationReconciliationResult
from joyhousebot.domain.capabilities import InvocationStatus
from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.runtime.context import ActionOutcomeUnknownError, ToolExecutionContext
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore


class _AsyncOperationTool(Tool):
    name = "async_operation"
    description = "Start a queryable asynchronous operation"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    side_effect = "internal"
    idempotent = True
    retryable = True

    def __init__(self, *, crash: bool = False, pending_checks: int = 0) -> None:
        self.execute_calls = 0
        self.reconcile_calls = 0
        self.crash = crash
        self.pending_checks = pending_checks

    async def execute(self, value: str, **kwargs: Any) -> ToolOutput:
        self.execute_calls += 1
        if self.crash:
            raise asyncio.CancelledError("worker disappeared after provider submission")
        context = kwargs["tool_context"]
        return ToolOutput(
            content="accepted",
            summary="operation accepted",
            data={"submitted": value},
            operation={
                "provider_operation_id": "provider-42",
                "idempotency_key": context.idempotency_key,
            },
            status=InvocationStatus.ACCEPTED,
        )

    async def reconcile_operation(
        self, operation: dict[str, Any], **_kwargs: Any
    ) -> OperationReconciliationResult:
        self.reconcile_calls += 1
        assert operation["idempotency_key"].startswith("action:")
        if self.reconcile_calls <= self.pending_checks:
            return OperationReconciliationResult(
                status="pending",
                summary="provider is still running",
                operation={**operation, "status": "running"},
                retry_after_seconds=0,
            )
        return OperationReconciliationResult(
            status="succeeded",
            summary="provider confirmed completion",
            output={"value": "done"},
            operation={**operation, "status": "completed"},
        )


class _OpaqueAsyncTool(Tool):
    name = "opaque_operation"
    description = "Start an operation without a query API"
    parameters = {"type": "object", "properties": {}}
    side_effect = "internal"

    async def execute(self, **_kwargs: Any) -> ToolOutput:
        return ToolOutput(
            content="accepted",
            operation={"provider_operation_id": "opaque-1"},
            status=InvocationStatus.ACCEPTED,
        )


class _AsyncThenFinishProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/reconciliation"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="provider-operation-1",
                        name="async_operation",
                        arguments={"value": "one"},
                    )
                ],
            )
        return LLMResponse(content="workflow completed", finish_reason="stop")


def _claimed_context(
    store: PostgresTestStore,
    run_id: str,
    *,
    worker_id: str = "worker-one",
    user_id: str = "user-a",
) -> tuple[Any, ToolExecutionContext]:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="run the operation",
        options={},
    )
    run = store.claim_runtime_run(run_id, worker_id=worker_id)
    assert run is not None
    turn_id = f"turn-{run_id}"
    store.create_runtime_turn(
        turn_id=turn_id,
        run_id=run_id,
        task_id=None,
        turn_index=0,
        model="test",
        request_hash="request-hash",
        worker_id=worker_id,
    )
    return run, ToolExecutionContext(
        run_id=run_id,
        user_id=user_id,
        agent_id="default",
        session_key=f"api:{user_id}:default:{run_id}",
        session_id=f"session-{run_id}",
        channel="api",
        chat_id="test",
        worker_id=worker_id,
        turn_id=turn_id,
        turn_index=0,
        action_index=0,
    )


@pytest.mark.asyncio
async def test_accepted_operation_is_reconciled_without_reexecuting_tool(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "operation-resume.db")
    run, context = _claimed_context(store, "run-operation-resume")
    tool = _AsyncOperationTool()
    dispatcher = CapabilityDispatcher(store)
    adapter = ToolCapabilityAdapter(tool)

    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await dispatcher.invoke_tool(adapter, {"value": "one"}, context=context)
    reconciliation = store.get_action_reconciliation(raised.value.action_id)
    assert reconciliation is not None
    assert reconciliation.status == "pending"
    assert store.suspend_run_for_reconciliation(
        run_id=run.run_id,
        reconciliation_id=reconciliation.reconciliation_id,
        action_id=reconciliation.action_id,
        invocation_id=reconciliation.invocation_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )
    assert store.list_incomplete_runtime_runs()[0].run_id == run.run_id
    resumed = store.claim_runtime_run(run.run_id, worker_id="worker-two")
    assert resumed is not None

    result = await dispatcher.invoke_tool(
        adapter,
        {"value": "one"},
        context=replace(context, worker_id="worker-two"),
    )

    assert result.status == InvocationStatus.SUCCEEDED
    assert result.data == {"value": "done"}
    assert tool.execute_calls == 1
    assert tool.reconcile_calls == 1
    assert store.get_action_reconciliation(raised.value.action_id).status == "succeeded"
    assert store.get_action_observation(raised.value.action_id).status == "succeeded"


@pytest.mark.asyncio
async def test_runtime_automatically_resumes_due_operation(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "operation-runtime.db")
    provider = _AsyncThenFinishProvider()
    tool = _AsyncOperationTool(pending_checks=1)
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/reconciliation",
        max_iterations=3,
        session_manager=RuntimeSessionManager(store),
    )
    executor.capabilities.register_tool(tool)
    runtime = NativeAgentRuntime(agent=executor, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="run asynchronous operation",
            user_id="user-a",
            session_id="operation-runtime",
            max_turns=3,
        )
    )

    for _ in range(100):
        finished = store.get_runtime_run(submitted.run_id)
        if finished is not None and finished.status in {
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }:
            break
        await asyncio.sleep(0.05)

    assert finished is not None and finished.status == "completed"
    assert finished.result["content"] == "workflow completed"
    assert tool.execute_calls == 1
    assert tool.reconcile_calls == 2
    assert provider.calls == 2
    await runtime.close()
    await executor.close_mcp()


@pytest.mark.asyncio
async def test_crash_gap_reconciles_by_idempotency_key_without_replay(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "operation-crash-gap.db")
    _, context = _claimed_context(store, "run-operation-crash-gap")
    tool = _AsyncOperationTool(crash=True)
    dispatcher = CapabilityDispatcher(store)
    adapter = ToolCapabilityAdapter(tool)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.invoke_tool(adapter, {"value": "one"}, context=context)
    tool.crash = False
    result = await dispatcher.invoke_tool(adapter, {"value": "one"}, context=context)

    assert result.status == InvocationStatus.SUCCEEDED
    assert tool.execute_calls == 1
    assert tool.reconcile_calls == 1
    action = store.list_action_intents(context.run_id)[0]
    assert action.status == "observed"
    assert store.get_action_observation(action.action_id).status == "succeeded"


@pytest.mark.asyncio
async def test_reconciliation_claim_has_one_database_owner(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "operation-claim.db")
    _, context = _claimed_context(store, "run-operation-claim")
    dispatcher = CapabilityDispatcher(store)
    adapter = ToolCapabilityAdapter(_AsyncOperationTool())
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await dispatcher.invoke_tool(adapter, {"value": "one"}, context=context)
    record = store.get_action_reconciliation(raised.value.action_id)
    assert record is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: store.claim_operation_reconciliation(
                    record.reconciliation_id, worker_id=worker
                ),
                ("worker-a", "worker-b"),
            )
        )
    assert sum(item is not None for item in claims) == 1


@pytest.mark.asyncio
async def test_manual_reconciliation_api_is_owner_scoped(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "operation-api.db")
    run, context = _claimed_context(store, "run-operation-api")
    dispatcher = CapabilityDispatcher(store)
    adapter = ToolCapabilityAdapter(_OpaqueAsyncTool())
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await dispatcher.invoke_tool(adapter, {}, context=context)
    record = store.get_action_reconciliation(raised.value.action_id)
    assert record is not None and record.status == "manual_required"
    assert store.suspend_run_for_reconciliation(
        run_id=run.run_id,
        reconciliation_id=record.reconciliation_id,
        action_id=record.action_id,
        invocation_id=record.invocation_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        owner = {"Authorization": "Bearer token-a"}
        foreign = {"Authorization": "Bearer token-b"}
        path = f"/v1/runs/{run.run_id}/operations"
        assert client.get(path, headers=owner).json()["items"][0]["status"] == "manual_required"
        assert client.get(path, headers=foreign).status_code == 404
        resolved = client.post(
            f"{path}/{record.reconciliation_id}/resolve",
            headers=owner,
            json={
                "resolution": "confirm_succeeded",
                "summary": "checked in provider console",
                "data": {"receipt": "receipt-1"},
            },
        )
        assert resolved.status_code == 200
        assert resolved.json()["reconciliation"]["status"] == "succeeded"
        assert resolved.json()["run"]["status"] == "queued"
    assert store.get_action_observation(record.action_id).result["data"] == {
        "receipt": "receipt-1"
    }


@pytest.mark.asyncio
async def test_manual_retry_preserves_frozen_action(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "operation-retry.db")
    run, context = _claimed_context(store, "run-operation-retry")
    tool = _OpaqueAsyncTool()
    dispatcher = CapabilityDispatcher(store)
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await dispatcher.invoke_tool(ToolCapabilityAdapter(tool), {}, context=context)
    record = store.get_action_reconciliation(raised.value.action_id)
    assert record is not None
    assert store.suspend_run_for_reconciliation(
        run_id=run.run_id,
        reconciliation_id=record.reconciliation_id,
        action_id=record.action_id,
        invocation_id=record.invocation_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )

    retried = store.retry_operation_reconciliation(
        record.reconciliation_id,
        run_id=run.run_id,
        user_id="user-a",
        actor_id="user-a",
    )
    assert retried is not None and retried.status == "pending"
    action = store.get_action_intent(record.action_id)
    assert action.status == "waiting_external"
    assert action.invocation_id == record.invocation_id
    assert store.get_runtime_run(run.run_id).status == "queued"
    resumed = store.claim_runtime_run(run.run_id, worker_id="worker-two")
    assert resumed is not None
    with pytest.raises(ActionOutcomeUnknownError):
        await dispatcher.invoke_tool(
            ToolCapabilityAdapter(tool),
            {},
            context=replace(context, worker_id="worker-two"),
        )
    assert store.get_action_reconciliation(record.action_id).status == "manual_required"


@pytest.mark.asyncio
async def test_operator_reconciliation_requires_control_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PostgresTestStore(tmp_path / "operation-operator.db")
    run, context = _claimed_context(store, "run-operation-operator")
    dispatcher = CapabilityDispatcher(store)
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await dispatcher.invoke_tool(
            ToolCapabilityAdapter(_OpaqueAsyncTool()), {}, context=context
        )
    record = store.get_action_reconciliation(raised.value.action_id)
    assert record is not None
    with store._pool.connection() as connection:
        connection.execute(
            "UPDATE operation_reconciliations SET required_role='operator' "
            "WHERE reconciliation_id=%s",
            (record.reconciliation_id,),
        )
    assert store.suspend_run_for_reconciliation(
        run_id=run.run_id,
        reconciliation_id=record.reconciliation_id,
        action_id=record.action_id,
        invocation_id=record.invocation_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "operator-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    path = f"/v1/runs/{run.run_id}/operations/{record.reconciliation_id}/resolve"

    with client:
        denied = client.post(
            path,
            headers={"Authorization": "Bearer token-a"},
            json={"resolution": "confirm_failed", "note": "provider rejected it"},
        )
        assert denied.status_code == 403
        resolved = client.post(
            path,
            headers={
                "Authorization": "Bearer operator-token",
                "X-Impersonate-User-ID": "user-a",
            },
            json={"resolution": "confirm_failed", "note": "provider rejected it"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["reconciliation"]["status"] == "failed"
