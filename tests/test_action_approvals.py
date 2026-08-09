from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.agent.executor import NativeAgentExecutor
from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.filesystem import WriteFileTool
from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.capabilities.tool_adapter import ToolCapabilityAdapter
from joyhousebot.config.schema import Config
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from joyhousebot.runtime.action_identity import payload_hash
from joyhousebot.runtime.approval_policy import approval_input_preview
from joyhousebot.runtime.context import ActionApprovalRequiredError, ToolExecutionContext
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.runner import NativeAgentRuntime
from joyhousebot.session.runtime_manager import RuntimeSessionManager
from tests.support.postgres_store import PostgresTestStore


class _WriteTool(Tool):
    name = "approval_write"
    description = "Write a value"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, value: str, **_kwargs: Any) -> str:
        self.calls += 1
        return f"saved:{value}"


class _ApproveThenFinishProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test")
        self.calls = 0

    def get_default_model(self) -> str:
        return "test/approvals"

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id="provider-write-1",
                        name="approval_write",
                        arguments={"value": "one"},
                    )
                ],
            )
        return LLMResponse(content="approved work completed", finish_reason="stop")


def _ref() -> CapabilityRef:
    return CapabilityRef(
        "approval_write",
        "1.0.0",
        CapabilityKind.TOOL,
        "test.approvals",
        "1.0.0",
        "sha256:test",
    )


def _definition(*, required_operator: bool = False) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=_ref(),
        name="Approval write",
        description="Write a value",
        input_schema=_WriteTool.parameters,
        output_schema={"type": "object"},
        adapter="test.approval_write",
        side_effect="write",
        tags=("approval:operator",) if required_operator else (),
    )


def _claimed_run(store: PostgresTestStore, run_id: str, *, user_id: str = "user-a"):
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="write the value",
        options={},
        initial_status="queued",
    )
    record = store.claim_runtime_run(run_id, worker_id="worker-one")
    assert record is not None
    return record


def _pending_request(
    store: PostgresTestStore,
    run_id: str,
    *,
    required_role: str = "owner",
    user_id: str = "user-a",
):
    run = _claimed_run(store, run_id, user_id=user_id)
    turn_id = f"turn-{run_id}"
    action_id = f"act-{run_id}"
    inputs = {"value": "one"}
    ref = _ref().to_dict()
    store.create_runtime_turn(
        turn_id=turn_id,
        run_id=run_id,
        task_id=None,
        turn_index=0,
        model="test",
        request_hash="request-hash",
        worker_id="worker-one",
    )
    store.create_action_intent(
        action_id=action_id,
        turn_id=turn_id,
        run_id=run_id,
        task_id=None,
        turn_index=0,
        action_index=0,
        capability_ref=ref,
        input=inputs,
        input_hash=payload_hash(inputs),
        side_effect="write",
        idempotent=True,
        retryable=True,
        risk="medium",
        approval_policy={"required": True, "required_role": required_role},
        idempotency_key=f"action:{action_id}",
        invocation_id=f"inv_{action_id}",
    )
    request, _ = store.create_approval_request(
        approval_id=f"apr-{run_id}",
        run_id=run_id,
        action_id=action_id,
        user_id=user_id,
        capability_ref=ref,
        input_hash=payload_hash(inputs),
        input_preview=inputs,
        risk="medium",
        data_classification="internal",
        required_role=required_role,
        requested_by="default",
        expires_in_seconds=3600,
    )
    assert store.suspend_run_for_approval(
        run_id=run_id,
        approval_id=request.approval_id,
        action_id=action_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )
    return request


@pytest.mark.asyncio
async def test_dispatcher_freezes_write_action_until_approved(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "approval-dispatch.db")
    run = _claimed_run(store, "run-approval-dispatch")
    store.create_runtime_turn(
        turn_id="turn-approval-dispatch",
        run_id=run.run_id,
        task_id=None,
        turn_index=0,
        model="test",
        request_hash="hash",
        worker_id="worker-one",
    )
    tool = _WriteTool()
    adapter = ToolCapabilityAdapter(tool, definition=_definition())
    dispatcher = CapabilityDispatcher(store)
    context = ToolExecutionContext(
        run_id=run.run_id,
        user_id="user-a",
        agent_id="default",
        session_key="api:user-a:default:test",
        session_id=f"session-{run.run_id}",
        channel="api",
        chat_id="test",
        worker_id="worker-one",
        turn_id="turn-approval-dispatch",
        turn_index=0,
        action_index=0,
    )

    with pytest.raises(ActionApprovalRequiredError) as raised:
        await dispatcher.invoke_tool(adapter, {"value": "one"}, context=context)

    approval = store.get_approval_request(raised.value.approval_id)
    assert approval is not None
    assert approval.input_hash == payload_hash({"value": "one"})
    assert tool.calls == 0
    assert store.suspend_run_for_approval(
        run_id=run.run_id,
        approval_id=approval.approval_id,
        action_id=approval.action_id,
        worker_id="worker-one",
        lease_version=run.lease_version,
    )
    assert store.resolve_approval_request(
        approval_id=approval.approval_id,
        run_id=run.run_id,
        user_id="user-a",
        resolution="approve",
        note=None,
        actor_id="user-a",
    )
    resumed = store.claim_runtime_run(run.run_id, worker_id="worker-two")
    assert resumed is not None
    result = await dispatcher.invoke_tool(
        adapter,
        {"value": "one"},
        context=replace(context, worker_id="worker-two"),
    )
    assert result.data == {"content": "saved:one"}
    assert tool.calls == 1
    assert store.get_approval_request(approval.approval_id).status == "consumed"


@pytest.mark.asyncio
async def test_dispatcher_fails_closed_when_write_action_identity_is_missing(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "approval-missing-action.db")
    run = _claimed_run(store, "run-missing-action")
    tool = _WriteTool()
    dispatcher = CapabilityDispatcher(store)
    result = await dispatcher.invoke_tool(
        ToolCapabilityAdapter(tool, definition=_definition()),
        {"value": "one"},
        context=ToolExecutionContext(
            run_id=run.run_id,
            user_id="user-a",
            agent_id="default",
            session_key="api:user-a:default:test",
            session_id=f"session-{run.run_id}",
            channel="api",
            chat_id="test",
            worker_id="worker-one",
        ),
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DURABLE_ACTION_REQUIRED"
    assert tool.calls == 0
    assert store.list_capability_invocations(run.run_id) == []


@pytest.mark.asyncio
async def test_runtime_resumes_same_action_after_approval(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "approval-runtime.db")
    provider = _ApproveThenFinishProvider()
    tool = _WriteTool()
    executor = NativeAgentExecutor(
        provider=provider,
        scratch_root=tmp_path,
        model="test/approvals",
        max_iterations=3,
        session_manager=RuntimeSessionManager(store),
    )
    executor.capabilities.register_tool(tool, definition=_definition())
    store.publish_capability(_definition(), actor_id="test:trusted-fixture")
    runtime = NativeAgentRuntime(agent=executor, store=store)

    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="write the value",
            user_id="user-a",
            session_id="approval-runtime",
            max_turns=3,
        )
    )
    waiting = await runtime.wait(submitted.run_id, timeout=3)
    assert waiting.status == "waiting_approval"
    approvals = store.list_run_approval_requests(
        submitted.run_id, expected_user_id="user-a"
    )
    assert len(approvals) == 1
    assert tool.calls == 0
    assert store.resolve_approval_request(
        approval_id=approvals[0].approval_id,
        run_id=submitted.run_id,
        user_id="user-a",
        resolution="approve",
        note="approved in test",
        actor_id="user-a",
    )
    store.notify_work(submitted.run_id)

    completed = await runtime.wait(submitted.run_id, timeout=5)
    assert completed.status == "completed"
    assert completed.result["content"] == "approved work completed"
    assert provider.calls == 2
    assert tool.calls == 1
    assert store.list_action_intents(submitted.run_id)[0].status == "observed"
    await runtime.close()
    await executor.close_mcp()


def test_approval_claim_is_single_consumer_and_action_is_immutable(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "approval-claim.db")
    request = _pending_request(store, "run-approval-claim")
    with pytest.raises(RuntimeError, match="identity conflict"):
        store.create_approval_request(
            approval_id=request.approval_id,
            run_id=request.run_id,
            action_id=request.action_id,
            user_id=request.user_id,
            capability_ref=request.capability_ref,
            input_hash="changed-hash",
            input_preview={"value": "changed"},
            risk="medium",
            required_role="owner",
            requested_by="default",
        )
    approved = store.resolve_approval_request(
        approval_id=request.approval_id,
        run_id=request.run_id,
        user_id=request.user_id,
        resolution="approve",
        note=None,
        actor_id="user-a",
    )
    assert approved is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda worker: store.claim_approved_action(
                    request.action_id, worker_id=worker
                ),
                ("worker-a", "worker-b"),
            )
        )
    assert sorted(results) == [False, True]


def test_revoke_before_action_claim_wins_and_releases_run_lease(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "approval-revoke.db")
    request = _pending_request(store, "run-approval-revoke")
    assert store.resolve_approval_request(
        approval_id=request.approval_id,
        run_id=request.run_id,
        user_id=request.user_id,
        resolution="approve",
        note=None,
        actor_id="user-a",
    )
    claimed = store.claim_runtime_run(request.run_id, worker_id="worker-after-approval")
    assert claimed is not None

    revoked = store.resolve_approval_request(
        approval_id=request.approval_id,
        run_id=request.run_id,
        user_id=request.user_id,
        resolution="revoke",
        note="changed my mind",
        actor_id="user-a",
    )
    assert revoked is not None
    assert revoked.status == "revoked"
    assert not store.claim_approved_action(request.action_id, worker_id="worker-late")
    run = store.get_runtime_run(request.run_id)
    assert run.status == "failed"
    assert run.lease_owner is None
    event_types = [event.type for event in store.list_runtime_events(request.run_id)]
    assert event_types[-2:] == ["approval.resolved", "run.failed"]


def test_expired_approval_fails_waiting_run(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "approval-expiry.db")
    request = _pending_request(store, "run-approval-expiry")
    with store._pool.connection() as connection:
        connection.execute(
            "UPDATE approval_requests SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE approval_id=%s",
            (request.approval_id,),
        )

    expired = store.expire_due_approval_requests()
    assert [item.approval_id for item in expired] == [request.approval_id]
    assert store.get_approval_request(request.approval_id).status == "expired"
    assert store.get_action_intent(request.action_id).status == "expired"
    run = store.get_runtime_run(request.run_id)
    assert run.status == "failed"
    assert run.waiting_on is None
    event_types = [event.type for event in store.list_runtime_events(request.run_id)]
    assert event_types[-2:] == ["approval.resolved", "run.failed"]


def test_approval_api_is_owner_scoped_and_operator_policy_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PostgresTestStore(tmp_path / "approval-api.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    owner_request = _pending_request(store, "run-approval-api")
    operator_request = _pending_request(
        store, "run-approval-operator", required_role="operator"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer token-a"}
    foreign = {"Authorization": "Bearer token-b"}

    with client:
        listed = client.get(f"/v1/runs/{owner_request.run_id}/approvals", headers=owner)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["input_hash"] == owner_request.input_hash
        assert client.get(
            f"/v1/runs/{owner_request.run_id}/approvals", headers=foreign
        ).status_code == 404
        approved = client.post(
            f"/v1/runs/{owner_request.run_id}/approvals/{owner_request.approval_id}/resolve",
            headers=owner,
            json={"resolution": "approve"},
        )
        assert approved.status_code == 200
        assert approved.json()["run"]["status"] == "queued"

        forbidden = client.post(
            f"/v1/runs/{operator_request.run_id}/approvals/{operator_request.approval_id}/resolve",
            headers=owner,
            json={"resolution": "approve"},
        )
        assert forbidden.status_code == 403
        monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "operator-token")
        operator = client.post(
            f"/v1/runs/{operator_request.run_id}/approvals/{operator_request.approval_id}/resolve",
            headers={
                "Authorization": "Bearer operator-token",
                "X-Impersonate-User-ID": "user-a",
            },
            json={"resolution": "approve"},
        )
        assert operator.status_code == 200
        assert operator.json()["approval"]["status"] == "approved"


def test_confidential_approval_preview_hides_values() -> None:
    preview = approval_input_preview(
        {"email": "person@example.com", "password": "secret"}, "confidential"
    )
    assert preview == {"fields": ["email", "password"], "values": "[REDACTED]"}


def test_builtin_side_effect_metadata_is_versioned_for_approval() -> None:
    write = ToolCapabilityAdapter(WriteFileTool())
    execute = ToolCapabilityAdapter(ExecTool())
    assert (write.definition.side_effect, write.definition.ref.version) == ("write", "1.1.0")
    assert execute.definition.side_effect == "external"
    assert execute.definition.idempotent is False
