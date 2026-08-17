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
from porthouse.application.run_plans import RunPlanMixin
from porthouse.application.run_target_resolution import resolve_run_target
from porthouse.orchestration import ClarificationEngine, ScenarioPlanner, ScenarioRouter
from porthouse.orchestration.failure_policy import validate_saga_declarations
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.models import (
    AgentEvent,
    AgentOptions,
    EventType,
    GraphTaskSpec,
    TaskGraphSpec,
)


class RunService(RunPlanMixin):
    def __init__(self, runtime: Any, store: Any) -> None:
        self.runtime = runtime
        self.store = store
        self.router = ScenarioRouter(store)
        self.clarifications = ClarificationEngine(store)
        self.planner = ScenarioPlanner(store)

    async def create(self, context: RequestContext, command: CreateRunCommand) -> Any:
        if not command.input.strip():
            raise ValidationError("input is required")
        session_id = command.session_id or self._new_session_id()
        resolved = await resolve_run_target(
            self.store, self.router, command.execution, prompt=command.input
        )
        team = resolved.team
        scenario = resolved.scenario
        decision = resolved.decision
        orchestration = resolved.orchestration
        effective_agent_id = resolved.agent_id
        effective_agent_revision_id = (
            team.coordinator.agent_revision_id if team is not None else resolved.agent_revision_id
        )
        experiment_assignment: dict[str, Any] | None = None
        if command.experiment_id:
            if command.execution.mode != "agent":
                raise ValidationError("online experiments currently support direct Agent Runs only")
            try:
                experiment_assignment = await asyncio.to_thread(
                    self.store.select_experiment_variant,
                    experiment_id=command.experiment_id,
                    subject_id=context.user_id,
                    target_id=effective_agent_id,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if experiment_assignment is not None:
                effective_agent_id = str(experiment_assignment["target_id"])
                effective_agent_revision_id = str(
                    experiment_assignment["target_revision_id"]
                )
        if scenario is not None:
            try:
                self.clarifications.validate_inputs(scenario, decision.extracted_inputs)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        agent_definition = await asyncio.to_thread(
            self.store.get_agent_definition, effective_agent_id
        )
        # A fixed Scenario owns its graph and bypasses open Agent planning.
        # Dynamic Scenarios may still use their selected Agent, but remain
        # constrained by the one explicitly frozen Scenario revision.
        explicit_fixed_scenario = bool(
            scenario is not None and scenario.planning_mode == "fixed"
        )
        coordinator_required = not explicit_fixed_scenario and (
            team is not None
            or getattr(agent_definition, "role", None) == "coordinator"
            or bool(command.metadata.get("coordinator_required"))
        )
        metadata = {
            **command.metadata,
            "orchestration": orchestration,
            "routing_decision": decision.to_dict(),
            "scenario_inputs": decision.extracted_inputs,
            "interaction_mode": command.interaction_mode,
            # An explicit Agent execution is a direct execution boundary. Team,
            # coordinator-role and caller-requested runs still plan first; a
            # normal executor Agent must not pay for or depend on a second
            # structured coordinator turn merely because no Scenario matched.
            "coordinator_required": coordinator_required,
        }
        if experiment_assignment is not None:
            metadata["experiment_assignment"] = experiment_assignment
        if team is not None:
            metadata.update(
                {
                    "team_ref": {
                        "team_id": team.team_id,
                        "revision_id": team.revision_id,
                        "version": team.version,
                        "coordinator_member_id": team.coordinator_member_id,
                    },
                    "team_members": [item.to_dict() for item in team.members],
                    "team_member_id": team.coordinator_member_id,
                    "team_context_policy": dict(team.context_policy),
                    "team_budget_policy": dict(team.budget_policy),
                    "team_approval_policy": dict(team.approval_policy),
                    "team_collaboration_blueprint": dict(team.effective_blueprint),
                }
            )
        scenario_tools = (
            [item.capability_id for item in scenario.allowed_capabilities]
            if scenario
            else []
        )
        if command.allowed_tools is not None and scenario is not None:
            outside_scenario = sorted(set(command.allowed_tools) - set(scenario_tools))
            if outside_scenario:
                raise ValidationError(
                    "caller tool allowlist exceeds the selected Scenario: "
                    + ", ".join(outside_scenario)
                )
        allowed_tools = (
            list(dict.fromkeys(command.allowed_tools))
            if command.allowed_tools is not None
            else scenario_tools
        )
        metadata["caller_tool_allowlist_enforced"] = command.allowed_tools is not None
        skill_names = (
            [item.skill_id.removeprefix("skill.") for item in scenario.required_skills]
            if scenario
            else []
        )
        metadata["skill_names"] = skill_names
        metadata["skill_refs"] = [
            item.to_dict()
            for item in (scenario.required_skills if scenario else ())
        ]
        options = AgentOptions(
            prompt=command.input,
            user_id=context.user_id,
            agent_id=effective_agent_id,
            agent_revision_id=(
                effective_agent_revision_id
            ),
            session_id=session_id,
            model=command.model,
            system_prompt=command.system_prompt,
            output_schema=command.output_schema,
            verification_policy=command.verification_policy,
            timeout_seconds=command.timeout_seconds,
            max_turns=command.max_turns,
            max_repairs=command.max_repairs,
            max_replans=command.max_replans,
            input_asset_ids=list(command.input_asset_ids),
            metadata=metadata,
            allowed_tools=allowed_tools,
            idempotency_key=context.idempotency_key,
            request_id=context.request_id,
            tracker_id=context.tracker_id,
            traceparent=context.traceparent,
            tracestate=context.tracestate,
        )
        # Scenario clarification is a durable submission concern, not an
        # optional side effect of the coordinator model.  In particular, a
        # coordinator-owned dynamic scenario must never be queued for model
        # execution while required fields are missing: the former behaviour
        # let the model write a prose follow-up and complete the Run instead
        # of creating a resumable input request.
        if scenario is not None:
            step = self.clarifications.evaluate(scenario, decision.extracted_inputs)
            if not step.complete:
                record = await self.runtime.submit_run(options, initial_status="waiting_input")
                await self.runtime.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        type=EventType.DECISION_RECORDED.value,
                        status="completed",
                        data={
                            "source": "runtime_decision",
                            "kind": "execution_selected",
                            "decision": decision.to_dict(),
                        },
                    )
                )
                await asyncio.to_thread(
                    self.store.save_run_scenario_state,
                    run_id=record.run_id,
                    user_id=context.user_id,
                    scenario_id=scenario.scenario_id,
                    scenario_version=scenario.version,
                    status="waiting_input",
                    collected_inputs=step.collected_inputs,
                    missing_inputs=list(step.missing_inputs),
                    current_node_id=step.node.node_id if step.node else None,
                    routing_decision=decision.to_dict(),
                )
                request = await asyncio.to_thread(
                    self.clarifications.create_request,
                    run_id=record.run_id,
                    user_id=context.user_id,
                    scenario=scenario,
                    step=step,
                )
                await self.runtime.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        type=EventType.DECISION_RECORDED.value,
                        phase="clarifying",
                        status="waiting_input",
                        summary="搜索条件不完整，正在等待补充",
                        data={"missing_fields": list(step.missing_inputs)},
                    )
                )
                await self.runtime.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        type=EventType.USER_INPUT_REQUESTED.value,
                        phase="clarifying",
                        status="waiting_input",
                        summary=request.question,
                        data={
                            "input_request_id": request.input_request_id,
                            "scenario_id": scenario.scenario_id,
                            "question": request.question,
                            "fields": request.fields,
                            "presentation": request.presentation,
                        },
                    )
                )
                return await self.get(context, record.run_id)
        # A run without a Scenario is submitted directly to the frozen Agent.
        # This branch also owns coordinator and dynamic-Scenario planning, but
        # only ``coordinator_required`` tells the Worker to perform that extra
        # structured planning turn.
        if scenario is None or coordinator_required or decision.next_action == "plan":
            graph = None
            if scenario is not None and not coordinator_required:
                try:
                    graph = await asyncio.to_thread(
                        self.planner.build_graph,
                        scenario,
                        goal=command.input,
                        inputs=decision.extracted_inputs,
                        user_id=context.user_id,
                        session_id=session_id,
                        agent_id=effective_agent_id,
                        agent_revision_id=effective_agent_revision_id,
                        idempotency_key=context.idempotency_key,
                        request_id=context.request_id,
                        tracker_id=context.tracker_id,
                        traceparent=context.traceparent,
                        tracestate=context.tracestate,
                    )
                except ValueError as exc:
                    raise ValidationError(str(exc)) from exc
                if graph is not None:
                    graph.metadata["orchestration"] = orchestration
                    graph.input_asset_ids = list(command.input_asset_ids)
            record = (
                await self.runtime.submit_graph(graph)
                if graph is not None
                else await self.runtime.submit_run(options)
            )
            await self.runtime.events.publish(
                AgentEvent(
                    run_id=record.run_id,
                    type=EventType.DECISION_RECORDED.value,
                    status="completed",
                    data={
                        "source": "runtime_decision",
                        "kind": "execution_selected",
                        "decision": decision.to_dict(),
                    },
                )
            )
            if scenario is not None:
                await asyncio.to_thread(
                    self.store.save_run_scenario_state,
                    run_id=record.run_id,
                    user_id=context.user_id,
                    scenario_id=scenario.scenario_id,
                    scenario_version=scenario.version,
                    status="ready",
                    collected_inputs=decision.extracted_inputs,
                    missing_inputs=[],
                    current_node_id=None,
                    routing_decision=decision.to_dict(),
                )
                await self.runtime.events.publish(
                    AgentEvent(
                        run_id=record.run_id,
                        type=EventType.PLAN_CREATED.value,
                        status="completed",
                        data={
                            "planning_mode": scenario.planning_mode,
                            "task_count": len(graph.tasks) if graph else 1,
                            "scenario_id": scenario.scenario_id,
                        },
                    )
                )
            return record

        record = await self.runtime.submit_run(options, initial_status="waiting_input")
        await self.runtime.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.DECISION_RECORDED.value,
                status="completed",
                data={
                    "source": "runtime_decision",
                    "kind": "execution_selected",
                    "decision": decision.to_dict(),
                },
            )
        )
        step = self.clarifications.evaluate(scenario, decision.extracted_inputs)
        await asyncio.to_thread(
            self.store.save_run_scenario_state,
            run_id=record.run_id,
            user_id=context.user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            status="waiting_input",
            collected_inputs=step.collected_inputs,
            missing_inputs=list(step.missing_inputs),
            current_node_id=step.node.node_id if step.node else None,
            routing_decision=decision.to_dict(),
        )
        request = await asyncio.to_thread(
            self.clarifications.create_request,
            run_id=record.run_id,
            user_id=context.user_id,
            scenario=scenario,
            step=step,
        )
        await self.runtime.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.USER_INPUT_REQUESTED.value,
                status="waiting_input",
                data={
                    "input_request_id": request.input_request_id,
                    "scenario_id": scenario.scenario_id,
                    "question": request.question,
                    "fields": request.fields,
                    "presentation": request.presentation,
                },
            )
        )
        return await self.get(context, record.run_id)

    async def pending_inputs(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_pending_input_requests,
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
            self.store.get_input_request,
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
                self.store.resolve_dynamic_input_request,
                input_request_id=input_request_id,
                run_id=run_id,
                user_id=context.user_id,
                answers=validated,
            )
            if not resolved:
                raise ValidationError("input request was already resolved")
            await asyncio.to_thread(self.store.notify_work, run_id)
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
                self.store.get_run_scenario_state,
                run_id,
                expected_user_id=context.user_id,
            )
            record = await self.get(context, run_id)
            scenario = (
                await asyncio.to_thread(
                    self.store.get_scenario_version,
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
                    self.store.update_runtime_run, run_id, status="queued"
                )
                if not queued:
                    raise ValidationError("resolved run could not be queued")
                await asyncio.to_thread(self.store.notify_work, run_id)
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
            catalog = await asyncio.to_thread(validate_graph_catalog, self.store, tasks)
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
            self.store.get_runtime_run, run_id, expected_user_id=context.user_id
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
            self.store.list_runtime_runs,
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
        return await asyncio.to_thread(self.store.list_runtime_tasks, run_id=run_id, limit=5000)

    async def graph_revisions(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_graph_revisions,
            run_id,
            expected_user_id=context.user_id,
        )

    async def artifacts(self, context: RequestContext, run_id: str) -> list[dict[str, Any]]:
        await self.get(context, run_id)
        return await asyncio.to_thread(self.store.list_runtime_artifacts, run_id)

    async def invocations(self, context: RequestContext, run_id: str) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_capability_invocations,
            run_id,
            expected_user_id=context.user_id,
        )

    async def logs(
        self, context: RequestContext, run_id: str, *, after_sequence: int = 0
    ) -> list[Any]:
        await self.get(context, run_id)
        return await asyncio.to_thread(
            self.store.list_runtime_logs, run_id, after_sequence=after_sequence
        )
