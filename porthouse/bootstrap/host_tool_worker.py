"""Worker for governed child Actions requested by an untrusted Device Host."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from loguru import logger

from porthouse.domain.capabilities import InvocationStatus
from porthouse.runtime.context import (
    ActionApprovalRequiredError,
    ActionOutcomeUnknownError,
    ToolExecutionContext,
)


@dataclass(slots=True)
class HostToolBrokerWorker:
    """Claim and execute Host Tool requests through the frozen Agent registry."""

    store: Any
    catalog: Any
    worker_id: str
    lease_seconds: int

    async def run(self) -> None:
        lease_seconds = max(30, int(self.lease_seconds))
        while True:
            try:
                request = await asyncio.to_thread(
                    self.store.claim_host_tool_request,
                    worker_id=self.worker_id,
                    lease_seconds=lease_seconds,
                )
                if request is None:
                    await asyncio.sleep(0.5)
                    continue
                await self.execute(request)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Host Tool Broker worker loop failed; retrying")
                await asyncio.sleep(1.0)

    async def execute(self, request: Any) -> None:
        error: dict[str, Any] | None = None
        result_value: dict[str, Any] | None = None
        status = "failed"
        turn_status = "failed"
        stop_reason = "host_tool_failed"
        try:
            run = await asyncio.to_thread(self.store.get_runtime_run, request.run_id)
            if run is None:
                raise RuntimeError("parent Run no longer exists")
            snapshot = await asyncio.to_thread(
                self.store.get_run_execution_snapshot, request.run_id
            )
            if snapshot is None:
                raise RuntimeError("parent Run has no frozen Agent execution snapshot")
            agent = self.catalog.resolve(snapshot.agent_revision_id)
            registry = getattr(agent, "capabilities", None) if agent is not None else None
            if registry is None:
                raise RuntimeError("frozen Agent Capability registry is unavailable")
            delivery = await asyncio.to_thread(
                self.store.get_device_operation_delivery,
                request.delivery_id,
                expected_user_id=request.user_id,
            )
            if delivery is None:
                raise RuntimeError("parent Device Host delivery no longer exists")
            authorization = dict(delivery.request.get("authorization") or {})
            ref = dict(request.capability_ref)
            await asyncio.to_thread(
                self.store.create_runtime_turn,
                turn_id=request.turn_id,
                run_id=request.run_id,
                task_id=request.task_id,
                scope=f"host_tool:{request.delivery_id}",
                turn_index=request.turn_index,
                model=None,
                request_hash=request.input_hash,
                worker_id=self.worker_id,
            )
            capability_result = await registry.invoke_tool(
                str(ref["capability_id"]),
                request.input,
                version=str(ref["version"]),
                context=ToolExecutionContext(
                    run_id=request.run_id,
                    task_id=request.task_id,
                    root_run_id=run.root_run_id or request.run_id,
                    session_key=(
                        f"{request.user_id}:{request.agent_id}:{run.session_id}"
                    ),
                    session_id=run.session_id,
                    channel="device_host",
                    chat_id=request.delivery_id,
                    user_id=request.user_id,
                    agent_id=request.agent_id,
                    request_id=request.request_id,
                    permission_mode=str(
                        authorization.get("permission_mode") or "enforced"
                    ),
                    allowed_tools=frozenset({str(ref["capability_id"])}),
                    granted_permissions=frozenset(
                        str(value)
                        for value in authorization.get("permissions") or ()
                    ),
                    worker_id=self.worker_id,
                    turn_id=request.turn_id,
                    turn_index=request.turn_index,
                    action_index=0,
                    metadata={
                        "capability_allowlist_enforced": True,
                        "device_delivery_id": request.delivery_id,
                        "host_request_id": request.host_request_id,
                    },
                ),
                tool_call_id=request.request_id,
            )
            result_value = capability_result.to_dict()
            if capability_result.status == InvocationStatus.SUCCEEDED:
                status = "succeeded"
                turn_status = "completed"
                stop_reason = "host_tool_completed"
            elif capability_result.status == InvocationStatus.ACCEPTED:
                status = "waiting_external"
                turn_status = "waiting_external"
                stop_reason = "host_tool_waiting_external"
            else:
                error = (
                    capability_result.error.to_dict()
                    if capability_result.error is not None
                    else {
                        "code": "HOST_TOOL_FAILED",
                        "message": capability_result.summary,
                    }
                )
        except ActionApprovalRequiredError as exc:
            status = "waiting_approval"
            turn_status = "waiting_approval"
            stop_reason = "host_tool_waiting_approval"
            error = {
                "code": "APPROVAL_REQUIRED",
                "message": str(exc),
                "approval_id": exc.approval_id,
                "action_id": exc.action_id,
            }
        except ActionOutcomeUnknownError as exc:
            status = "waiting_external"
            turn_status = "waiting_external"
            stop_reason = "host_tool_waiting_external"
            error = {
                "code": "ACTION_OUTCOME_UNKNOWN",
                "message": str(exc),
                "action_id": exc.action_id,
                "invocation_id": exc.invocation_id,
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = {
                "code": type(exc).__name__,
                "message": str(exc)[:1000],
            }
            logger.exception(
                "Host tool execution failed request_id={}", request.request_id
            )
        await asyncio.to_thread(
            self.store.finish_runtime_turn,
            request.turn_id,
            status=turn_status,
            stop_reason=stop_reason,
            error=error,
        )
        saved = await asyncio.to_thread(
            self.store.finish_host_tool_request,
            request.request_id,
            status=status,
            result=result_value,
            error=error,
            worker_id=self.worker_id,
            lease_version=request.lease_version,
        )
        if saved is None:
            logger.warning(
                "Host tool completion fenced request_id={} lease_version={}",
                request.request_id,
                request.lease_version,
            )
