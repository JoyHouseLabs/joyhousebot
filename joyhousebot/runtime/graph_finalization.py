"""Lease-fenced distributed task-graph finalization."""

from __future__ import annotations

import asyncio
import json

from joyhousebot.orchestration.aggregation import (
    aggregate_task_results,
    normalize_aggregation_policy,
    synthesis_prompt,
)
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.models import (
    AgentEvent,
    AgentResult,
    AgentUsage,
    EventType,
    RunStatus,
    utc_now,
)


class GraphFinalizationMixin:
    async def _try_finalize_graph(self, run_id: str) -> None:
        record = await asyncio.to_thread(
            self.store.claim_runtime_run,
            run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if record is None:
            return
        cancellation = CancellationToken()
        started_at = record.started_at or utc_now()
        owner_task = asyncio.current_task()

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1.0, self.lease_seconds / 3))
                owned = await asyncio.to_thread(
                    self.store.heartbeat_runtime_run,
                    run_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    lease_version=record.lease_version,
                )
                if not owned:
                    cancellation.cancel("graph finalizer ownership lost")
                    if owner_task is not None:
                        owner_task.cancel()
                    return

        heartbeat = asyncio.create_task(_heartbeat(), name=f"graph-finalizer-heartbeat:{run_id}")
        try:
            tasks = await asyncio.to_thread(
                self.store.list_runtime_tasks, run_id=run_id, limit=5000
            )
            task_inputs = [
                {
                    "task_id": task.task_id,
                    "spec_id": str(task.payload.get("spec_id") or task.task_id),
                    "parent_task_id": task.parent_task_id,
                    "agent_id": task.agent_id,
                    "status": task.status,
                    "result": task.result
                    or {
                        "status": task.status,
                        "error": (task.error or {}).get("message"),
                    },
                }
                for task in tasks
            ]
            top_level_inputs = [
                item
                for item, task in zip(task_inputs, tasks, strict=True)
                if item["parent_task_id"] is None
                and not bool(task.payload.get("saga_managed"))
            ]
            results = {item["spec_id"]: item["result"] for item in top_level_inputs}
            usage_results = [
                dict(task.result or {})
                for task in tasks
                if str(task.payload.get("node_type") or "agent") in {"agent", "capability"}
            ]
            failures = [
                task for task in tasks if task.status in {"failed", "cancelled", "timed_out"}
            ]
            failure_mode = dict(record.options.get("failure_policy") or {}).get("mode")
            if failure_mode == "saga" and failures:
                saga = await asyncio.to_thread(self.store.reconcile_runtime_saga, run_id)
                if saga is None or saga["status"] == "running":
                    raise RuntimeError("Saga state is incomplete while Graph has no active Tasks")
                compensated = saga["status"] == "completed"
                await self._finish_error(
                    run_id,
                    RunStatus.FAILED,
                    EventType.RUN_FAILED,
                    (
                        "task graph failed and declared side effects were compensated"
                        if compensated
                        else "task graph failed and declared compensation also failed"
                    ),
                    started_at,
                    stop_reason=(
                        "saga_compensated" if compensated else "saga_compensation_failed"
                    ),
                    worker_id=self.worker_id,
                    lease_version=record.lease_version,
                )
                return
            if bool(record.options.get("fail_fast")) and failures:
                await self._finish_error(
                    run_id,
                    RunStatus.FAILED,
                    EventType.RUN_FAILED,
                    "task graph stopped after a task failure",
                    started_at,
                    worker_id=self.worker_id,
                    lease_version=record.lease_version,
                )
                return
            policy = normalize_aggregation_policy(
                dict(record.options.get("aggregation_policy") or {}),
                aggregate=bool(record.options.get("aggregate", True)),
            )
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.AGGREGATION_STARTED.value,
                    phase="finalizing",
                    status="running",
                    data={
                        "policy": policy.to_dict(),
                        "task_count": len(top_level_inputs),
                        "runtime_task_count": len(task_inputs),
                    },
                )
            )
            completed_outputs = [item for item in top_level_inputs if item["status"] == "completed"]
            coordination = dict(
                (record.options.get("metadata") or {}).get("coordination_usage") or {}
            )
            prior_usage = AgentUsage()
            for value in usage_results:
                prior_usage.add(AgentUsage.from_dict(value.get("usage")))
            prior_usage.add(AgentUsage.from_dict(coordination))
            prior_input_tokens = prior_usage.input_tokens
            prior_output_tokens = prior_usage.output_tokens
            prior_cost_usd = float(prior_usage.cost_usd or 0.0)
            if record.options.get("max_cost_usd") is not None and (
                prior_usage.missing_billing_invocations
            ):
                raise RuntimeError(
                    "graph max_cost_usd cannot be enforced because model billing is missing"
                )

            def remaining_budget(name: str, consumed: float) -> float | None:
                configured = record.options.get(name)
                if configured is None:
                    return None
                remaining = float(configured) - consumed
                if remaining < 0:
                    raise RuntimeError(f"graph {name} budget exceeded before aggregation")
                return remaining

            aggregate_input_budget = remaining_budget(
                "max_input_tokens", float(prior_input_tokens)
            )
            aggregate_output_budget = remaining_budget(
                "max_output_tokens", float(prior_output_tokens)
            )
            aggregate_cost_budget = remaining_budget("max_cost_usd", prior_cost_usd)
            if policy.mode == "llm_synthesis" and completed_outputs:
                if any(
                    value is not None and value <= 0
                    for value in (
                        aggregate_input_budget,
                        aggregate_output_budget,
                        aggregate_cost_budget,
                    )
                ):
                    raise RuntimeError("graph budget exhausted before LLM aggregation")
                content, tools, aggregate_usage = await self._call_agent(
                    run_id=run_id,
                    task_id=None,
                    prompt=synthesis_prompt(
                        goal=record.prompt, tasks=top_level_inputs, policy=policy
                    ),
                    user_id=record.user_id,
                    session_id=f"{record.session_id}:aggregate",
                    agent_id=record.agent_id,
                    channel="runtime",
                    chat_id="aggregate",
                    model=None,
                    system_prompt=None,
                    output_schema=None,
                    timeout_seconds=300,
                    max_turns=None,
                    max_input_tokens=(
                        int(aggregate_input_budget)
                        if aggregate_input_budget is not None
                        else None
                    ),
                    max_output_tokens=(
                        int(aggregate_output_budget)
                        if aggregate_output_budget is not None
                        else None
                    ),
                    max_cost_usd=aggregate_cost_budget,
                    permission_mode="default",
                    allowed_tools=[],
                    disallowed_tools=[],
                    cancellation=cancellation,
                    run_lease_version=record.lease_version,
                )
                aggregation = {
                    "policy": policy.to_dict(),
                    "source_task_ids": [item["task_id"] for item in completed_outputs],
                    "source_count": len(completed_outputs),
                    "conflicts": [],
                    "discarded": [],
                    "execution": "llm_synthesis",
                }
            else:
                deterministic = aggregate_task_results(top_level_inputs, policy)
                content = deterministic.content
                tools = []
                aggregate_usage = AgentUsage()
                aggregation = deterministic.audit
            usage = prior_usage
            usage.add(aggregate_usage)
            if record.options.get("max_cost_usd") is not None and (
                usage.missing_billing_invocations
            ):
                raise RuntimeError(
                    "graph max_cost_usd cannot be enforced because model billing is missing"
                )
            for name, actual in (
                ("max_input_tokens", usage.input_tokens),
                ("max_output_tokens", usage.output_tokens),
                ("max_cost_usd", float(usage.cost_usd or 0.0)),
            ):
                configured = record.options.get(name)
                if configured is not None and actual > float(configured):
                    raise RuntimeError(f"graph {name} budget exceeded")
            result = AgentResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                content=content,
                structured_output={
                    "tasks": results,
                    "aggregation": {
                        **aggregation,
                        "result": deterministic.structured_output
                        if policy.mode != "llm_synthesis"
                        else None,
                    },
                },
                stop_reason="completed",
                usage=usage,
                tools_used=tools,
                started_at=started_at,
                finished_at=utc_now(),
            )
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.AGGREGATION_COMPLETED.value,
                    phase="finalizing",
                    status="completed",
                    data={
                        "policy": policy.to_dict(),
                        "source_count": len(completed_outputs),
                        "conflict_count": len(aggregation.get("conflicts") or []),
                    },
                )
            )
            saved = await self._commit_terminal(
                run_id,
                status=RunStatus.COMPLETED,
                event_type=EventType.RUN_COMPLETED,
                result=result.to_dict(),
                artifacts=[
                    {
                        "artifact_id": f"{run_id}:final",
                        "name": "final-output",
                        "media_type": "text/plain",
                        "content": content,
                        "provenance": {
                            "worker_id": self.worker_id,
                            "lease_version": record.lease_version,
                            "terminal": True,
                        },
                    },
                    {
                        "artifact_id": f"{run_id}:aggregation-audit",
                        "name": "aggregation-audit",
                        "media_type": "application/json",
                        "content": json.dumps(
                            aggregation,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                        "provenance": {
                            "worker_id": self.worker_id,
                            "lease_version": record.lease_version,
                            "terminal": True,
                        },
                    },
                ],
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            if saved:
                await self._log(
                    run_id,
                    "graph.completed",
                    "Distributed task graph completed",
                    data={"task_count": len(tasks), "usage": usage.to_dict()},
                )
        except Exception as exc:
            await self._finish_error(
                run_id,
                RunStatus.FAILED,
                EventType.RUN_FAILED,
                str(exc),
                started_at,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
