"""Run command and query use cases."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.application.graph_validation import validate_graph_catalog
from porthouse.application.run_commands import CreateRunCommand, GraphTaskCommand
from porthouse.application.run_creation import RunCreationService
from porthouse.application.run_plans import RunPlanMixin
from porthouse.orchestration import ClarificationEngine, ScenarioPlanner, ScenarioRouter
from porthouse.orchestration.failure_policy import validate_saga_declarations
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.models import (
    AgentEvent,
    EventType,
    GraphTaskSpec,
    TaskGraphSpec,
)
from porthouse.storage.contracts import RuntimeStores


class RunService(RunPlanMixin):
    def __init__(self, runtime: Any, store: object) -> None:
        self.runtime = runtime
        self.stores = RuntimeStores.from_backend(store)
        self.router = ScenarioRouter(self.stores.scenarios)
        self.clarifications = ClarificationEngine(self.stores.clarifications)
        self.planner = ScenarioPlanner(self.stores.catalog)

    async def create(self, context: RequestContext, command: CreateRunCommand) -> Any:
        service = RunCreationService(
            runtime=self.runtime,
            stores=self.stores,
            router=self.router,
            clarifications=self.clarifications,
            planner=self.planner,
            get_run=self.get,
            new_session_id=self._new_session_id,
        )
        return await service.create(context, command)

    async def pending_inputs(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.clarifications.list_pending_input_requests,
            run_id,
            expected_user_id=context.user_id,
        )

    async def resolve_input(
        self,
        context: RequestContext,
        run_id: str,
        *,
        input_request_id: str,
        answers: dict[str, Any],
    ) -> tuple[Any, list[Any]]:
        await self.get(context, run_id)
        request = await asyncio.to_thread(
            self.stores.clarifications.get_input_request,
            input_request_id,
            expected_user_id=context.user_id,
        )
        if request is None or request.run_id != run_id:
            raise ValidationError("input request is not pending")
        if request.source == "agent":
            try:
                validated = self.clarifications.validate_request_answers(request.fields, answers)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            resolved = await asyncio.to_thread(
                self.stores.clarifications.resolve_dynamic_input_request,
                input_request_id=input_request_id,
                run_id=run_id,
                user_id=context.user_id,
                answers=validated,
            )
            if not resolved:
                raise ValidationError("input request was already resolved")
            await asyncio.to_thread(self.stores.workers.notify_work, run_id)
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.USER_INPUT_RESOLVED.value,
                    status="completed",
                    data={
                        "input_request_id": input_request_id,
                        "source": "agent",
                        "fields": sorted(validated),
                    },
                )
            )
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.RUN_QUEUED.value,
                    status="queued",
                    data={"reason": "dynamic_clarification_completed"},
                )
            )
            return await self.get(context, run_id), await self.pending_inputs(context, run_id)
        try:
            step, next_request = await asyncio.to_thread(
                self.clarifications.resolve,
                run_id=run_id,
                user_id=context.user_id,
                input_request_id=input_request_id,
                answers=answers,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.USER_INPUT_RESOLVED.value,
                status="completed",
                data={"input_request_id": input_request_id, "fields": sorted(answers)},
            )
        )
        if next_request:
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.USER_INPUT_REQUESTED.value,
                    status="waiting_input",
                    data={
                        "input_request_id": next_request.input_request_id,
                        "question": next_request.question,
                        "fields": next_request.fields,
                        "presentation": next_request.presentation,
                    },
                )
            )
        elif step.complete:
            state = await asyncio.to_thread(
                self.stores.clarifications.get_run_scenario_state,
                run_id,
                expected_user_id=context.user_id,
            )
            record = await self.get(context, run_id)
            scenario = (
                await asyncio.to_thread(
                    self.stores.clarifications.get_scenario_version,
                    state.scenario_id,
                    state.scenario_version,
                )
                if state is not None
                else None
            )
            if state is None or scenario is None:
                raise ValidationError("resolved scenario state is unavailable")
            try:
                graph = await asyncio.to_thread(
                    self.planner.build_graph,
                    scenario,
                    goal=record.prompt,
                    inputs=state.collected_inputs,
                    user_id=context.user_id,
                    session_id=record.session_id,
                    agent_id=record.agent_id,
                    idempotency_key=record.idempotency_key,
                    request_id=str(record.options.get("request_id") or context.request_id),
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if graph is not None:
                await self.runtime.materialize_graph(run_id, graph)
            else:
                queued = await asyncio.to_thread(
                    self.stores.runs.update_runtime_run, run_id, status="queued"
                )
                if not queued:
                    raise ValidationError("resolved run could not be queued")
                await asyncio.to_thread(self.stores.workers.notify_work, run_id)
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.RUN_QUEUED.value,
                    status="queued",
                    data={"reason": "clarification_completed"},
                )
            )
        return await self.get(context, run_id), await self.pending_inputs(context, run_id)

    @staticmethod
    def _new_session_id() -> str:
        return f"sess_{int(time.time() * 1000):x}{uuid4().hex[:16]}"

    async def create_graph(
        self,
        context: RequestContext,
        *,
        goal: str,
        agent_id: str,
        session_id: str,
        tasks: list[GraphTaskCommand],
        max_concurrent: int = 4,
        fail_fast: bool = True,
        failure_policy: dict[str, Any] | None = None,
        aggregate: bool = True,
        aggregation_policy: dict[str, Any] | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_usd: float | None = None,
        input_asset_ids: list[str] | None = None,
    ) -> Any:
        if not goal.strip() or not tasks:
            raise ValidationError("goal and tasks are required")
        try:
            catalog = await asyncio.to_thread(
                validate_graph_catalog, self.stores.catalog, tasks
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        spec = TaskGraphSpec(
            goal=goal,
            user_id=context.user_id,
            session_id=session_id,
            agent_id=agent_id,
            max_concurrent=max_concurrent,
            fail_fast=fail_fast,
            failure_policy=dict(failure_policy or {}),
            aggregate=aggregate,
            aggregation_policy=dict(aggregation_policy or {}),
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_cost_usd=max_cost_usd,
            input_asset_ids=list(input_asset_ids or []),
            idempotency_key=context.idempotency_key,
            request_id=context.request_id,
            tracker_id=context.tracker_id,
            traceparent=context.traceparent,
            tracestate=context.tracestate,
            tasks=[
                GraphTaskSpec(
                    id=item.id,
                    prompt=item.prompt,
                    agent_id=item.agent_id,
                    dependencies=item.dependencies,
                    name=item.name,
                    timeout_seconds=item.timeout_seconds,
                    max_attempts=item.max_attempts,
                    max_input_tokens=item.max_input_tokens,
                    max_output_tokens=item.max_output_tokens,
                    max_cost_usd=item.max_cost_usd,
                    metadata=item.metadata,
                    capability=item.capability,
                    capability_input=item.capability_input,
                    output_schema=item.output_schema,
                    verification_policy=item.verification_policy,
                    max_repairs=item.max_repairs,
                    allowed_tools=item.allowed_tools,
                    skill_names=item.skill_names,
                    node_type=item.node_type,
                    branch=item.branch,
                    foreach=item.foreach,
                    wait_event=item.wait_event,
                    approval=item.approval,
                    verify=item.verify,
                    compensation=item.compensation,
                    bounded_loop=item.bounded_loop,
                    aggregate=item.aggregate,
                    subrun=item.subrun,
                )
                for item in tasks
            ],
        )
        try:
            validate_and_order_graph(spec.tasks)
            validate_saga_declarations(
                spec.tasks,
                catalog,
                spec.failure_policy,
                max_concurrent=spec.max_concurrent,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            return await self.runtime.submit_graph(spec)
        except ValueError as exc:
            if "Graph Idempotency-Key" in str(exc):
                raise ConflictError(str(exc)) from exc
            raise

    async def get(self, context: RequestContext, run_id: str) -> Any:
        # SQL-level user isolation: the store filters by expected_user_id so a
        # foreign run never leaves the database layer.
        record = await asyncio.to_thread(
            self.stores.runs.get_runtime_run,
            run_id,
            expected_user_id=context.user_id,
        )
        if record is None:
            raise NotFoundError("run not found")
        if not self._visible_to_app_principal(context, record):
            raise NotFoundError("run not found")
        return record

    async def list(
        self,
        context: RequestContext,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        rows = await asyncio.to_thread(
            self.stores.runs.list_runtime_runs,
            user_id=context.user_id,
            session_id=session_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
        return [row for row in rows if self._visible_to_app_principal(context, row)]

    @staticmethod
    def _visible_to_app_principal(context: RequestContext, record: Any) -> bool:
        installation_id = context.principal.app_installation_id
        if not installation_id:
            return True
        metadata = dict((record.options or {}).get("metadata") or {})
        app = dict(metadata.get("app") or {})
        return str(app.get("installation_id") or "") == installation_id

    async def cancel(self, context: RequestContext, run_id: str) -> Any:
        await self.get(context, run_id)
        if not await self.runtime.cancel(run_id, "cancelled by user"):
            raise NotFoundError("run is not cancellable")
        return await self.get(context, run_id)

    async def resume(self, context: RequestContext, run_id: str) -> Any:
        await self.get(context, run_id)
        record = await self.runtime.resume(run_id)
        if record is None:
            raise ValidationError("run is not resumable")
        return record

    async def tasks(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.tasks.list_runtime_tasks, run_id=run_id, limit=5000
        )

    async def graph_revisions(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.graphs.list_graph_revisions,
            run_id,
            expected_user_id=context.user_id,
        )

    async def artifacts(self, context: RequestContext, run_id: str) -> list[dict[str, Any]]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.execution.list_runtime_artifacts, run_id
        )

    async def invocations(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.invocations.list_capability_invocations,
            run_id,
            expected_user_id=context.user_id,
        )

    async def logs(
        self, context: RequestContext, run_id: str, *, after_sequence: int = 0
    ) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.stores.logs.list_runtime_logs,
            run_id,
            after_sequence=after_sequence,
        )
