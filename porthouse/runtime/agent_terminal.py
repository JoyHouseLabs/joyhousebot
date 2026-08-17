"""Terminal state handling shared by durable Agent executions."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.runtime.context import CancellationToken
from porthouse.runtime.models import AgentEvent, AgentResult, EventType, RunStatus, utc_now


class AgentTerminalMixin:
    async def _finish_error(
        self,
        run_id: str,
        status: RunStatus,
        event_type: EventType,
        error: str,
        started_at: str,
        *,
        stop_reason: str | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> AgentResult:
        result = AgentResult(
            run_id=run_id,
            status=status,
            error=error,
            stop_reason=stop_reason or status.value,
            started_at=started_at,
            finished_at=utc_now(),
        )
        persisted = await self._commit_terminal(
            run_id,
            status=status,
            event_type=event_type,
            result=result.to_dict(),
            error={"message": error},
            worker_id=worker_id,
            lease_version=lease_version,
        )
        if persisted is None:
            return result
        await self._log(
            run_id,
            event_type.value,
            error,
            level="error" if status == RunStatus.FAILED else "warning",
            data={"status": status.value, "stop_reason": result.stop_reason},
        )
        return result

    async def _commit_terminal(
        self,
        run_id: str,
        *,
        status: RunStatus,
        event_type: EventType,
        result: dict[str, Any],
        error: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> AgentEvent | None:
        """Atomically persist terminal state/event, then notify live subscribers."""
        artifact_events = [
            await self.events.prepare(
                AgentEvent(
                    event_id=f"artifact:{artifact['artifact_id']}:created",
                    run_id=run_id,
                    type=EventType.ARTIFACT_CREATED.value,
                    status="completed",
                    worker_id=worker_id,
                    lease_version=lease_version,
                    data={
                        "artifact_id": artifact["artifact_id"],
                        "name": artifact["name"],
                        "media_type": artifact["media_type"],
                    },
                )
            )
            for artifact in artifacts or []
        ]
        event = await self.events.prepare(
            AgentEvent(
                run_id=run_id,
                type=event_type.value,
                status=status.value,
                worker_id=worker_id,
                lease_version=lease_version,
                data=result,
            )
        )
        record = await asyncio.to_thread(self.stores.runs.get_runtime_run, run_id)
        parent_events: list[AgentEvent] = []
        if record is not None and record.parent_run_id:
            child_event_type = (
                EventType.SUBAGENT_COMPLETED
                if status == RunStatus.COMPLETED
                else EventType.SUBAGENT_FAILED
            )
            content = str((result or {}).get("content") or "")
            parent_events.append(
                await self.events.prepare(
                    AgentEvent(
                        run_id=record.parent_run_id,
                        task_id=record.parent_task_id,
                        type=child_event_type.value,
                        status=status.value,
                        data={
                            "child_run_id": run_id,
                            "agent_id": record.agent_id,
                            "status": status.value,
                            "content_preview": content[:2000] or None,
                            "error": (error or {}).get("message") if error else None,
                        },
                    )
                )
            )
        bundle = await asyncio.to_thread(
            self.stores.runs.finish_runtime_run_bundle,
            run_id,
            status=status.value,
            event=event,
            result=result,
            error=error,
            artifacts=artifacts,
            events_before_terminal=[*artifact_events, *parent_events],
            worker_id=worker_id,
            lease_version=lease_version,
        )
        persisted = bundle[1] if bundle is not None else None
        if bundle is not None:
            for prior_event in bundle[0]:
                await self.events.fanout(prior_event)
            await self.events.fanout(persisted)
            assignment = dict(
                dict(record.options or {}).get("metadata") or {}
            ).get("experiment_assignment")
            experiment_id = (
                str(assignment.get("experiment_id") or "")
                if isinstance(assignment, dict)
                else ""
            )
            if experiment_id:
                try:
                    guardrail = await asyncio.to_thread(
                        self.stores.experiments.enforce_experiment_guardrails,
                        experiment_id,
                    )
                    if guardrail.get("paused"):
                        await self._log(
                            run_id,
                            "experiment.guardrail_paused",
                            "Experiment was automatically paused by a guardrail",
                            level="warning",
                            data={
                                "experiment_id": experiment_id,
                                "violations": guardrail.get("violations") or [],
                            },
                        )
                except Exception:
                    # A governance summary must never invalidate an already
                    # committed terminal Run. The next terminal Run or an
                    # operator summary will retry the guardrail check.
                    await self._log(
                        run_id,
                        "experiment.guardrail_check_failed",
                        "Experiment guardrail check failed after terminal commit",
                        level="warning",
                        data={"experiment_id": experiment_id},
                    )
        return persisted

    async def _ensure_run_owned(
        self,
        run_id: str,
        cancellation: CancellationToken,
        *,
        lease_version: int | None = None,
    ) -> None:
        owned = await asyncio.to_thread(
            self.stores.runs.heartbeat_runtime_run,
            run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            lease_version=lease_version,
        )
        if not owned:
            cancellation.cancel("run ownership lost or run was cancelled")
            raise asyncio.CancelledError(cancellation.reason)
