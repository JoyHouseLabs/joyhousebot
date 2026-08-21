"""Lease-fenced distributed task-graph finalization."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from joyhousebot.orchestration.aggregation import (
    AggregationPolicy,
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
from joyhousebot.storage.runtime_store import RuntimeRunRecord, RuntimeTaskRecord


@dataclass(slots=True)
class GraphFinalizationInputs:
    tasks: list[RuntimeTaskRecord]
    task_inputs: list[dict[str, Any]]
    top_level_inputs: list[dict[str, Any]]
    results: dict[str, Any]
    failures: list[RuntimeTaskRecord]
    prior_usage: AgentUsage


@dataclass(slots=True)
class AggregationOutcome:
    content: str
    tools: list[dict[str, Any]]
    usage: AgentUsage
    audit: dict[str, Any]
    structured_output: Any = None


class GraphFinalizationMixin:
    async def _graph_finalizer_heartbeat(
        self,
        run_id: str,
        record: RuntimeRunRecord,
        cancellation: CancellationToken,
        owner_task: asyncio.Task[Any] | None,
    ) -> None:
        while True:
            await asyncio.sleep(max(1.0, self.lease_seconds / 3))
            owned = await asyncio.to_thread(
                self.stores.runs.heartbeat_runtime_run,
                run_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                lease_version=record.lease_version,
            )
            if owned:
                continue
            cancellation.cancel("graph finalizer ownership lost")
            if owner_task is not None:
                owner_task.cancel()
            return

    async def _load_graph_finalization_inputs(
        self, run_id: str, record: RuntimeRunRecord
    ) -> GraphFinalizationInputs:
        tasks = await asyncio.to_thread(
            self.stores.tasks.list_runtime_tasks, run_id=run_id, limit=5000
        )
        task_inputs = [self._graph_task_input(task) for task in tasks]
        top_level_inputs = [
            item
            for item, task in zip(task_inputs, tasks, strict=True)
            if item["parent_task_id"] is None and not bool(task.payload.get("saga_managed"))
        ]
        results = {item["spec_id"]: item["result"] for item in top_level_inputs}
        failures = [
            task for task in tasks if task.status in {"failed", "cancelled", "timed_out"}
        ]
        prior_usage = AgentUsage()
        for task in tasks:
            if str(task.payload.get("node_type") or "agent") in {"agent", "capability"}:
                prior_usage.add(AgentUsage.from_dict(dict(task.result or {}).get("usage")))
        coordination = dict((record.options.get("metadata") or {}).get("coordination_usage") or {})
        prior_usage.add(AgentUsage.from_dict(coordination))
        return GraphFinalizationInputs(
            tasks=tasks,
            task_inputs=task_inputs,
            top_level_inputs=top_level_inputs,
            results=results,
            failures=failures,
            prior_usage=prior_usage,
        )

    @staticmethod
    def _graph_task_input(task: RuntimeTaskRecord) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "spec_id": str(task.payload.get("spec_id") or task.task_id),
            "parent_task_id": task.parent_task_id,
            "agent_id": task.agent_id,
            "status": task.status,
            "result": task.result
            or {"status": task.status, "error": (task.error or {}).get("message")},
        }

    async def _finish_failed_graph(
        self,
        run_id: str,
        record: RuntimeRunRecord,
        inputs: GraphFinalizationInputs,
        started_at: str,
    ) -> bool:
        if not inputs.failures:
            return False
        failure_mode = dict(record.options.get("failure_policy") or {}).get("mode")
        if failure_mode == "saga":
            saga = await asyncio.to_thread(self.stores.graphs.reconcile_runtime_saga, run_id)
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
                stop_reason="saga_compensated" if compensated else "saga_compensation_failed",
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            return True
        if not bool(record.options.get("fail_fast")):
            return False
        await self._finish_error(
            run_id,
            RunStatus.FAILED,
            EventType.RUN_FAILED,
            "task graph stopped after a task failure",
            started_at,
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        return True

    @staticmethod
    def _remaining_aggregation_budget(
        record: RuntimeRunRecord, name: str, consumed: float
    ) -> float | None:
        configured = record.options.get(name)
        if configured is None:
            return None
        remaining = float(configured) - consumed
        if remaining < 0:
            raise RuntimeError(f"graph {name} budget exceeded before aggregation")
        return remaining

    def _aggregation_budgets(
        self, record: RuntimeRunRecord, usage: AgentUsage
    ) -> tuple[float | None, float | None, float | None]:
        if record.options.get("max_cost_usd") is not None and usage.missing_billing_invocations:
            raise RuntimeError("graph max_cost_usd cannot be enforced because model billing is missing")
        return (
            self._remaining_aggregation_budget(record, "max_input_tokens", float(usage.input_tokens)),
            self._remaining_aggregation_budget(record, "max_output_tokens", float(usage.output_tokens)),
            self._remaining_aggregation_budget(record, "max_cost_usd", float(usage.cost_usd or 0.0)),
        )

    async def _aggregate_graph_outputs(
        self,
        run_id: str,
        record: RuntimeRunRecord,
        inputs: GraphFinalizationInputs,
        policy: AggregationPolicy,
        cancellation: CancellationToken,
    ) -> AggregationOutcome:
        completed = [item for item in inputs.top_level_inputs if item["status"] == "completed"]
        input_budget, output_budget, cost_budget = self._aggregation_budgets(
            record, inputs.prior_usage
        )
        if policy.mode != "llm_synthesis" or not completed:
            deterministic = aggregate_task_results(inputs.top_level_inputs, policy)
            return AggregationOutcome(
                content=deterministic.content,
                tools=[],
                usage=AgentUsage(),
                audit=deterministic.audit,
                structured_output=deterministic.structured_output,
            )
        if any(value is not None and value <= 0 for value in (input_budget, output_budget, cost_budget)):
            raise RuntimeError("graph budget exhausted before LLM aggregation")
        content, tools, usage = await self._call_agent(
            run_id=run_id,
            task_id=None,
            prompt=synthesis_prompt(goal=record.prompt, tasks=inputs.top_level_inputs, policy=policy),
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
            max_input_tokens=int(input_budget) if input_budget is not None else None,
            max_output_tokens=int(output_budget) if output_budget is not None else None,
            max_cost_usd=cost_budget,
            permission_mode="default",
            allowed_tools=[],
            disallowed_tools=[],
            cancellation=cancellation,
            run_lease_version=record.lease_version,
        )
        return AggregationOutcome(
            content=content,
            tools=tools,
            usage=usage,
            audit={
                "policy": policy.to_dict(),
                "source_task_ids": [item["task_id"] for item in completed],
                "source_count": len(completed),
                "conflicts": [],
                "discarded": [],
                "execution": "llm_synthesis",
            },
        )

    @staticmethod
    def _validated_graph_usage(record: RuntimeRunRecord, usage: AgentUsage) -> None:
        if record.options.get("max_cost_usd") is not None and usage.missing_billing_invocations:
            raise RuntimeError("graph max_cost_usd cannot be enforced because model billing is missing")
        for name, actual in (
            ("max_input_tokens", usage.input_tokens),
            ("max_output_tokens", usage.output_tokens),
            ("max_cost_usd", float(usage.cost_usd or 0.0)),
        ):
            configured = record.options.get(name)
            if configured is not None and actual > float(configured):
                raise RuntimeError(f"graph {name} budget exceeded")

    async def _commit_completed_graph(
        self,
        run_id: str,
        record: RuntimeRunRecord,
        inputs: GraphFinalizationInputs,
        policy: AggregationPolicy,
        outcome: AggregationOutcome,
        started_at: str,
    ) -> None:
        usage = inputs.prior_usage
        usage.add(outcome.usage)
        self._validated_graph_usage(record, usage)
        completed = [item for item in inputs.top_level_inputs if item["status"] == "completed"]
        result = AgentResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            content=outcome.content,
            structured_output={
                "tasks": inputs.results,
                "aggregation": {
                    **outcome.audit,
                    "result": outcome.structured_output,
                },
            },
            stop_reason="completed",
            usage=usage,
            tools_used=outcome.tools,
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
                    "source_count": len(completed),
                    "conflict_count": len(outcome.audit.get("conflicts") or []),
                },
            )
        )
        artifacts = self._graph_final_artifacts(run_id, record, outcome)
        saved = await self._commit_terminal(
            run_id,
            status=RunStatus.COMPLETED,
            event_type=EventType.RUN_COMPLETED,
            result=result.to_dict(),
            artifacts=artifacts,
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        if saved:
            await self._log(
                run_id,
                "graph.completed",
                "Distributed task graph completed",
                data={"task_count": len(inputs.tasks), "usage": usage.to_dict()},
            )

    def _graph_final_artifacts(
        self, run_id: str, record: RuntimeRunRecord, outcome: AggregationOutcome
    ) -> list[dict[str, Any]]:
        provenance = {
            "worker_id": self.worker_id,
            "lease_version": record.lease_version,
            "terminal": True,
        }
        return [
            {
                "artifact_id": f"{run_id}:final",
                "name": "final-output",
                "media_type": "text/plain",
                "content": outcome.content,
                "provenance": provenance,
            },
            {
                "artifact_id": f"{run_id}:aggregation-audit",
                "name": "aggregation-audit",
                "media_type": "application/json",
                "content": json.dumps(outcome.audit, ensure_ascii=False, sort_keys=True, default=str),
                "provenance": provenance,
            },
        ]

    async def _try_finalize_graph(self, run_id: str) -> None:
        record = await asyncio.to_thread(
            self.stores.runs.claim_runtime_run,
            run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if record is None:
            return
        cancellation = CancellationToken()
        started_at = record.started_at or utc_now()
        heartbeat = asyncio.create_task(
            self._graph_finalizer_heartbeat(run_id, record, cancellation, asyncio.current_task()),
            name=f"graph-finalizer-heartbeat:{run_id}",
        )
        try:
            inputs = await self._load_graph_finalization_inputs(run_id, record)
            if await self._finish_failed_graph(run_id, record, inputs, started_at):
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
                        "task_count": len(inputs.top_level_inputs),
                        "runtime_task_count": len(inputs.task_inputs),
                    },
                )
            )
            outcome = await self._aggregate_graph_outputs(
                run_id, record, inputs, policy, cancellation
            )
            await self._commit_completed_graph(
                run_id, record, inputs, policy, outcome, started_at
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
