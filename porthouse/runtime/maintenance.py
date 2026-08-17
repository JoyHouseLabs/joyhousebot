"""Retention maintenance for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import time

from loguru import logger

from porthouse.runtime.models import AgentEvent, EventType

# How often the coordinator purges expired runtime data.
_PURGE_INTERVAL_SECONDS = 600.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


class RuntimeMaintenanceMixin:
    async def _purge_old_runtime_data(self) -> None:
        """Periodically drop expired runtime rows; failures only get logged."""
        expire_approvals = getattr(self.store, "expire_due_approval_requests", None)
        if expire_approvals is not None:
            try:
                expired = await asyncio.to_thread(expire_approvals, limit=500)
                for request in expired:
                    await self.events.publish(
                        AgentEvent(
                            event_id=f"approval:{request.approval_id}:resolved:expired",
                            run_id=request.run_id,
                            task_id=request.task_id,
                            type=EventType.APPROVAL_RESOLVED.value,
                            status="expired",
                            data={
                                "approval_id": request.approval_id,
                                "action_id": request.action_id,
                                "resolution": "expired",
                            },
                        )
                    )
                    await self.events.publish(
                        AgentEvent(
                            event_id=(
                                f"approval:{request.approval_id}:task.failed"
                                if request.task_id
                                else f"approval:{request.approval_id}:run.failed"
                            ),
                            run_id=request.run_id,
                            task_id=request.task_id,
                            type=(
                                EventType.TASK_FAILED.value
                                if request.task_id
                                else EventType.RUN_FAILED.value
                            ),
                            status="failed",
                            data={"reason": "approval_expired"},
                        )
                    )
            except Exception:
                logger.exception("Approval expiry failed")
        expire_plans = getattr(self.store, "expire_plan_confirmations", None)
        if expire_plans is not None:
            try:
                expired_plans = await asyncio.to_thread(expire_plans, limit=200)
                for confirmation in expired_plans:
                    await self.events.publish(
                        AgentEvent(
                            event_id=f"plan:{confirmation['run_id']}:confirmation:expired",
                            run_id=confirmation["run_id"],
                            type=EventType.PLAN_CONFIRMATION_RESOLVED.value,
                            status="expired",
                            data={
                                "action": "expired",
                                "plan_version": confirmation["plan_version"],
                                "resolution": "expired",
                            },
                        )
                    )
                    await self.events.publish(
                        AgentEvent(
                            event_id=f"plan:{confirmation['run_id']}:run.failed",
                            run_id=confirmation["run_id"],
                            type=EventType.RUN_FAILED.value,
                            status="failed",
                            data={"reason": "plan_confirmation_expired"},
                        )
                    )
            except Exception:
                logger.exception("Plan confirmation expiry failed")
        purge = getattr(self.store, "purge_old_runtime_data", None)
        if purge is None:
            return
        retention_days = _env_int("PORTHOUSE_RETENTION_DAYS", 30)
        diagnostics_days = _env_int("PORTHOUSE_DIAGNOSTICS_RETENTION_DAYS", retention_days)
        cutoff_ms = int((time.time() - retention_days * 86400) * 1000)
        diagnostics_cutoff_ms = int((time.time() - diagnostics_days * 86400) * 1000)
        try:
            # Store implementations perform blocking PostgreSQL deletes. Run
            # both sync and async implementations off the coordinator loop so
            # maintenance cannot pause lease heartbeats or task claiming.
            if inspect.iscoroutinefunction(purge):
                await asyncio.to_thread(asyncio.run, purge(cutoff_ms, diagnostics_cutoff_ms))
            else:
                await asyncio.to_thread(purge, cutoff_ms, diagnostics_cutoff_ms)
        except Exception:
            logger.exception("Runtime data purge failed")
