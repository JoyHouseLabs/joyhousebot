"""Application boundary for Graph external-event waits and delivery tokens."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.runtime.models import AgentEvent, EventType


def graph_event_wait_public_dict(record: Any) -> dict[str, Any]:
    value = asdict(record)
    value.pop("user_id", None)
    value.pop("config_hash", None)
    value["token_issued"] = record.token_issued
    return value


class GraphEventService:
    def __init__(self, runtime: Any, runs: Any, store: Any) -> None:
        self.runtime = runtime
        self.runs = runs
        self.store = store

    async def list(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.runs.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_graph_event_waits,
            run_id,
            expected_user_id=context.user_id,
        )

    async def issue_token(
        self, context: RequestContext, run_id: str, wait_id: str
    ) -> tuple[Any, str]:
        await self.runs.get(context, run_id)
        wait = await asyncio.to_thread(
            self.store.get_graph_event_wait,
            wait_id,
            expected_user_id=context.user_id,
        )
        if wait is None or wait.run_id != run_id:
            raise NotFoundError("Graph event wait not found")
        issued = await asyncio.to_thread(
            self.store.issue_graph_event_token,
            wait_id,
            expected_user_id=context.user_id,
            actor_id=context.principal.subject,
        )
        if issued is None:
            raise ConflictError("Graph event wait is no longer pending")
        return issued

    async def receive(
        self, wait_id: str, *, token: str, event_type: str, payload: Any
    ) -> dict[str, Any]:
        outcome = await asyncio.to_thread(
            self.store.receive_graph_event,
            wait_id,
            token=token,
            event_type=event_type,
            payload=payload,
        )
        status = outcome["status"]
        if status == "not_found":
            raise NotFoundError("Graph event wait not found")
        if status == "event_type_mismatch":
            raise ValidationError("external event type does not match the frozen wait")
        if status == "schema_invalid":
            raise ValidationError(
                f"external event payload does not match schema: {outcome['message']}"
            )
        if status == "idempotency_conflict":
            raise ConflictError("duplicate external event payload does not match")
        if status == "expired":
            record = outcome["record"]
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    task_id=record.task_id,
                    type=EventType.EVENT_EXPIRED.value,
                    status="expired",
                    data={"wait_id": record.wait_id, "deadline_at": record.deadline_at},
                )
            )
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    task_id=record.task_id,
                    type=EventType.TASK_FAILED.value,
                    status="failed",
                    data={"reason": "event_deadline_expired"},
                )
            )
        if status in {"expired", "cancelled"}:
            raise ConflictError(f"Graph event wait is {status}")
        if status != "received":
            raise ConflictError("Graph event wait is no longer receivable")
        record = outcome["record"]
        if not outcome.get("duplicate"):
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    task_id=record.task_id,
                    type=EventType.EVENT_RECEIVED.value,
                    status="completed",
                    data={
                        "wait_id": wait_id,
                        "event_type": event_type,
                        "payload_hash": record.payload_hash,
                    },
                )
            )
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    task_id=record.task_id,
                    type=EventType.TASK_COMPLETED.value,
                    status="completed",
                    data=outcome["task_result"],
                )
            )
            await asyncio.to_thread(self.store.notify_work, record.run_id)
        return {
            "wait": graph_event_wait_public_dict(record),
            "duplicate": bool(outcome.get("duplicate")),
        }
