"""Terminal state handling shared by durable Agent executions."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from loguru import logger

from joyhousebot.contracts import ProjectionContext, ScopedRunProjectionQueries
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.models import AgentEvent, AgentResult, EventType, RunStatus, utc_now


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
        bundle = await asyncio.to_thread(
            self.store.finish_runtime_run_bundle,
            run_id,
            status=status.value,
            event=event,
            result=result,
            error=error,
            artifacts=artifacts,
            events_before_terminal=artifact_events,
            worker_id=worker_id,
            lease_version=lease_version,
        )
        persisted = bundle[1] if bundle is not None else None
        if bundle is not None:
            for artifact_event in bundle[0]:
                await self.events.fanout(artifact_event)
            await self.events.fanout(persisted)
            record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
            if record is not None and record.parent_run_id:
                child_event_type = (
                    EventType.SUBAGENT_COMPLETED
                    if status == RunStatus.COMPLETED
                    else EventType.SUBAGENT_FAILED
                )
                content = str((result or {}).get("content") or "")
                await self.events.publish(
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
            await self._materialize_terminal_projections(record)
        return persisted

    async def _materialize_terminal_projections(self, record: Any | None) -> None:
        """Persist plugin read models after the fenced terminal commit.

        Projection failures are observable but cannot roll back an already
        committed Run. Providers decide whether they own the Run; the runtime
        never branches on a plugin or business-domain identifier.
        """
        registry = getattr(self, "projection_registry", None)
        list_projections = getattr(registry, "list_projections", None)
        if record is None or not callable(list_projections):
            return
        providers = tuple(list_projections())
        if not providers:
            return
        event_limit = max(
            1,
            min(
                max(int(getattr(provider, "event_limit", 1000) or 1000) for provider in providers),
                5000,
            ),
        )
        try:
            artifacts, events, invocations, scenario_state = await asyncio.gather(
                asyncio.to_thread(self.store.list_runtime_artifacts, record.run_id),
                asyncio.to_thread(
                    self.store.list_runtime_events,
                    record.run_id,
                    user_id=record.user_id,
                    limit=event_limit,
                ),
                asyncio.to_thread(
                    self.store.list_capability_invocations,
                    record.run_id,
                    expected_user_id=record.user_id,
                ),
                asyncio.to_thread(
                    self.store.get_run_scenario_state,
                    record.run_id,
                    expected_user_id=record.user_id,
                ),
            )
        except Exception:
            logger.exception("Failed to load terminal projection evidence for Run {}", record.run_id)
            return
        context = ProjectionContext(
            run=record,
            artifacts=tuple(artifacts),
            events=tuple(events),
            invocations=tuple(invocations),
            scenario_state=scenario_state,
            queries=ScopedRunProjectionQueries(
                self.store, run_id=record.run_id, user_id=record.user_id
            ),
            user_id=record.user_id,
        )
        for provider in providers:
            supports = getattr(provider, "supports", None)
            materialize = getattr(provider, "materialize", None)
            if not callable(materialize):
                continue
            try:
                if callable(supports):
                    accepted = supports(context)
                    accepted = await accepted if inspect.isawaitable(accepted) else accepted
                    if not accepted:
                        continue
                if inspect.iscoroutinefunction(materialize):
                    result = await materialize(context)
                else:
                    result = await asyncio.to_thread(materialize, context)
                await asyncio.to_thread(
                    self.store.append_runtime_log,
                    run_id=record.run_id,
                    stage="projection.materialized",
                    message=f"Projection materialized: {provider.view_id}",
                    data={
                        "view_id": provider.view_id,
                        "schema_version": provider.schema_version,
                        "result": result if isinstance(result, dict) else {},
                    },
                )
            except Exception as exc:
                logger.exception(
                    "Projection {} failed for Run {}", provider.view_id, record.run_id
                )
                await asyncio.to_thread(
                    self.store.append_runtime_log,
                    run_id=record.run_id,
                    stage="projection.failed",
                    level="error",
                    message=f"Projection failed: {provider.view_id}",
                    data={"view_id": provider.view_id, "error_type": type(exc).__name__},
                )

    async def _ensure_run_owned(
        self,
        run_id: str,
        cancellation: CancellationToken,
        *,
        lease_version: int | None = None,
    ) -> None:
        owned = await asyncio.to_thread(
            self.store.heartbeat_runtime_run,
            run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            lease_version=lease_version,
        )
        if not owned:
            cancellation.cancel("run ownership lost or run was cancelled")
            raise asyncio.CancelledError(cancellation.reason)
