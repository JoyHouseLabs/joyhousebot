"""Delivery, child Run, Schedule, and Monitor ports."""

from __future__ import annotations

import hashlib
from typing import Any

from porthouse.bus.events import OutboundMessage
from porthouse.contracts.capabilities import CapabilityContext


class DeliveryPort:
    def __init__(self, outbound_sink: Any) -> None:
        self._sink = outbound_sink

    async def send(
        self,
        context: CapabilityContext,
        *,
        content: str,
        channel: str,
        chat_id: str,
    ) -> dict[str, str]:
        if self._sink is None:
            raise RuntimeError("message delivery is not configured")
        if not channel or not chat_id:
            raise ValueError("Run context has no delivery target")
        outbound_id = f"action:{context.action_id}"
        await self._sink(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=content,
                metadata={
                    "_runtime_outbound_id": outbound_id,
                    "user_id": context.user_id,
                    "run_id": context.run_id,
                    "action_id": context.action_id,
                    "idempotency_key": context.idempotency_key,
                },
                request_id=context.request_id,
            )
        )
        return {"channel": channel, "chat_id": chat_id, "outbound_id": outbound_id}


class ChildRunPort:
    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def spawn(self, context: CapabilityContext, **kwargs: Any) -> Any:
        if self._manager is None:
            raise RuntimeError("distributed child Run service is unavailable")
        return await self._manager.spawn(
            task=str(kwargs.get("task") or ""),
            label=kwargs.get("label"),
            agent_id=kwargs.get("agent_id"),
            output_schema=kwargs.get("output_schema"),
            origin_channel=str(context.metadata.get("channel") or "api"),
            origin_chat_id=str(context.metadata.get("chat_id") or "direct"),
            idempotency_key=context.idempotency_key,
        )

    async def reconcile(
        self,
        context: CapabilityContext,
        operation: dict[str, Any],
    ) -> Any:
        if self._manager is None:
            raise RuntimeError("distributed child Run service is unavailable")
        return await self._manager.reconcile(operation, user_id=context.user_id)


class SchedulePort:
    def __init__(self, service: Any) -> None:
        self._service = service

    def _require_service(self) -> Any:
        if self._service is None:
            raise RuntimeError("Schedule service is unavailable")
        return self._service

    def add(
        self,
        context: CapabilityContext,
        *,
        schedule: Any,
        message: str,
        channel: str,
        chat_id: str,
        monitor: bool,
        session_mode: str,
        preflight_mode: str,
        context_mode: str,
        active_hours: dict[str, str] | None,
        session_id: str,
        delete_after_run: bool,
    ) -> dict[str, Any]:
        job = self._require_service().add_job(
            job_id=f"schedule_{hashlib.sha256(str(context.action_id).encode()).hexdigest()[:32]}",
            name=message[:30],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=channel,
            to=chat_id,
            delete_after_run=delete_after_run,
            user_id=context.user_id,
            agent_id=context.agent_id or "default",
            payload_kind="agent_monitor" if monitor else "agent_turn",
            session_mode=session_mode,
            session_id=session_id if monitor and session_mode == "main" else None,
            preflight_mode=preflight_mode if monitor else "always",
            context_mode=context_mode if monitor else "full",
            active_hours=active_hours if monitor else None,
        )
        return {
            "job_id": job.id,
            "name": job.name,
            "resource": "monitor" if monitor else "job",
        }

    def list(self, context: CapabilityContext) -> list[dict[str, Any]]:
        return [
            {
                "job_id": job.id,
                "name": job.name,
                "schedule_kind": job.schedule.kind,
                "payload_kind": job.payload.kind,
                "enabled": job.enabled,
            }
            for job in self._require_service().list_jobs(user_id=context.user_id)
        ]

    def remove(self, context: CapabilityContext, *, job_id: str) -> bool:
        return bool(self._require_service().remove_job(job_id, user_id=context.user_id))

    def get_monitor_scratch(self, context: CapabilityContext) -> dict[str, Any] | None:
        schedule_id = self._monitor_schedule_id(context)
        return self._require_service().get_monitor_scratch(
            schedule_id,
            user_id=context.user_id,
        )

    def update_monitor_scratch(
        self,
        context: CapabilityContext,
        *,
        content: str,
        expected_revision: int,
    ) -> dict[str, Any] | None:
        schedule_id = self._monitor_schedule_id(context)
        return self._require_service().update_monitor_scratch(
            schedule_id,
            user_id=context.user_id,
            content=content,
            expected_revision=expected_revision,
            actor_type="agent",
            actor_id=context.agent_id or "default",
            run_id=context.run_id,
            action_id=context.action_id,
        )

    @staticmethod
    def _monitor_schedule_id(context: CapabilityContext) -> str:
        if context.metadata.get("schedule_payload_kind") != "agent_monitor":
            raise PermissionError(
                "monitor scratch is only available inside a scheduled Agent Monitor Run"
            )
        schedule_id = str(context.metadata.get("schedule_id") or "")
        if not schedule_id:
            raise PermissionError("monitor schedule id is missing")
        return schedule_id


__all__ = ["ChildRunPort", "DeliveryPort", "SchedulePort"]
