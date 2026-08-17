"""Run creation orchestration split from the Run query/control service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from porthouse.application.context import RequestContext
from porthouse.application.errors import ValidationError
from porthouse.application.run_commands import CreateRunCommand
from porthouse.application.run_target_resolution import ResolvedRunTarget, resolve_run_target
from porthouse.runtime.models import AgentEvent, AgentOptions, EventType
from porthouse.storage.contracts import RuntimeStores


@dataclass(slots=True)
class RunCreationState:
    context: RequestContext
    command: CreateRunCommand
    resolved: ResolvedRunTarget
    session_id: str
    agent_id: str
    agent_revision_id: str | None
    coordinator_required: bool
    options: AgentOptions


class RunCreationService:
    """Resolve, validate and submit one top-level Run command."""

    def __init__(
        self,
        *,
        runtime: Any,
        stores: RuntimeStores,
        router: Any,
        clarifications: Any,
        planner: Any,
        get_run: Callable[[RequestContext, str], Awaitable[Any]],
        new_session_id: Callable[[], str],
    ) -> None:
        self.runtime = runtime
        self.stores = stores
        self.router = router
        self.clarifications = clarifications
        self.planner = planner
        self.get_run = get_run
        self.new_session_id = new_session_id

    async def create(self, context: RequestContext, command: CreateRunCommand) -> Any:
        state = await self._prepare(context, command)
        waiting = await self._submit_waiting_scenario(state)
        if waiting is not None:
            return waiting
        return await self._submit_ready(state)

    async def _prepare(
        self, context: RequestContext, command: CreateRunCommand
    ) -> RunCreationState:
        if not command.input.strip():
            raise ValidationError("input is required")
        session_id = command.session_id or self.new_session_id()
        resolved = await resolve_run_target(
            self.stores.catalog,
            self.router,
            command.execution,
            prompt=command.input,
        )
        agent_id = resolved.agent_id
        revision_id = (
            resolved.team.coordinator.agent_revision_id
            if resolved.team is not None
            else resolved.agent_revision_id
        )
        assignment = await self._select_experiment(
            context, command, target_id=agent_id
        )
        if assignment is not None:
            agent_id = str(assignment["target_id"])
            revision_id = str(assignment["target_revision_id"])
        scenario = resolved.scenario
        if scenario is not None:
            try:
                self.clarifications.validate_inputs(
                    scenario, resolved.decision.extracted_inputs
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        definition = await asyncio.to_thread(
            self.stores.catalog.get_agent_definition, agent_id
        )
        fixed_scenario = bool(
            scenario is not None and scenario.planning_mode == "fixed"
        )
        coordinator_required = not fixed_scenario and (
            resolved.team is not None
            or getattr(definition, "role", None) == "coordinator"
            or bool(command.metadata.get("coordinator_required"))
        )
        options = self._build_options(
            context,
            command,
            resolved,
            session_id=session_id,
            agent_id=agent_id,
            revision_id=revision_id,
            coordinator_required=coordinator_required,
            experiment_assignment=assignment,
        )
        return RunCreationState(
            context=context,
            command=command,
            resolved=resolved,
            session_id=session_id,
            agent_id=agent_id,
            agent_revision_id=revision_id,
            coordinator_required=coordinator_required,
            options=options,
        )

    async def _select_experiment(
        self,
        context: RequestContext,
        command: CreateRunCommand,
        *,
        target_id: str,
    ) -> dict[str, Any] | None:
        if not command.experiment_id:
            return None
        if command.execution.mode != "agent":
            raise ValidationError(
                "online experiments currently support direct Agent Runs only"
            )
        try:
            return await asyncio.to_thread(
                self.stores.experiments.select_experiment_variant,
                experiment_id=command.experiment_id,
                subject_id=context.user_id,
                target_id=target_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def _build_options(
        self,
        context: RequestContext,
        command: CreateRunCommand,
        resolved: ResolvedRunTarget,
        *,
        session_id: str,
        agent_id: str,
        revision_id: str | None,
        coordinator_required: bool,
        experiment_assignment: dict[str, Any] | None,
    ) -> AgentOptions:
        scenario = resolved.scenario
        metadata = self._build_metadata(
            command,
            resolved,
            coordinator_required=coordinator_required,
            experiment_assignment=experiment_assignment,
        )
        scenario_tools = (
            [item.capability_id for item in scenario.allowed_capabilities]
            if scenario is not None
            else []
        )
        if command.allowed_tools is not None and scenario is not None:
            outside = sorted(set(command.allowed_tools) - set(scenario_tools))
            if outside:
                raise ValidationError(
                    "caller tool allowlist exceeds the selected Scenario: "
                    + ", ".join(outside)
                )
        allowed_tools = (
            list(dict.fromkeys(command.allowed_tools))
            if command.allowed_tools is not None
            else scenario_tools
        )
        required_skills = scenario.required_skills if scenario is not None else ()
        metadata.update(
            {
                "caller_tool_allowlist_enforced": command.allowed_tools is not None,
                "skill_names": [
                    item.skill_id.removeprefix("skill.") for item in required_skills
                ],
                "skill_refs": [item.to_dict() for item in required_skills],
            }
        )
        return AgentOptions(
            prompt=command.input,
            user_id=context.user_id,
            agent_id=agent_id,
            agent_revision_id=revision_id,
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

    @staticmethod
    def _build_metadata(
        command: CreateRunCommand,
        resolved: ResolvedRunTarget,
        *,
        coordinator_required: bool,
        experiment_assignment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = {
            **command.metadata,
            "orchestration": resolved.orchestration,
            "routing_decision": resolved.decision.to_dict(),
            "scenario_inputs": resolved.decision.extracted_inputs,
            "interaction_mode": command.interaction_mode,
            "coordinator_required": coordinator_required,
        }
        if experiment_assignment is not None:
            metadata["experiment_assignment"] = experiment_assignment
        team = resolved.team
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
        return metadata

    async def _submit_waiting_scenario(self, state: RunCreationState) -> Any | None:
        scenario = state.resolved.scenario
        if scenario is None:
            return None
        step = self.clarifications.evaluate(
            scenario, state.resolved.decision.extracted_inputs
        )
        if step.complete:
            return None
        record = await self.runtime.submit_run(
            state.options, initial_status="waiting_input"
        )
        await self._publish_selection(record.run_id, state.resolved.decision)
        await asyncio.to_thread(
            self.stores.clarifications.save_run_scenario_state,
            run_id=record.run_id,
            user_id=state.context.user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            status="waiting_input",
            collected_inputs=step.collected_inputs,
            missing_inputs=list(step.missing_inputs),
            current_node_id=step.node.node_id if step.node else None,
            routing_decision=state.resolved.decision.to_dict(),
        )
        request = await asyncio.to_thread(
            self.clarifications.create_request,
            run_id=record.run_id,
            user_id=state.context.user_id,
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
        return await self.get_run(state.context, record.run_id)

    async def _submit_ready(self, state: RunCreationState) -> Any:
        scenario = state.resolved.scenario
        graph = None
        if scenario is not None and not state.coordinator_required:
            graph = await self._build_fixed_graph(state)
        record = (
            await self.runtime.submit_graph(graph)
            if graph is not None
            else await self.runtime.submit_run(state.options)
        )
        await self._publish_selection(record.run_id, state.resolved.decision)
        if scenario is None:
            return record
        await asyncio.to_thread(
            self.stores.clarifications.save_run_scenario_state,
            run_id=record.run_id,
            user_id=state.context.user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            status="ready",
            collected_inputs=state.resolved.decision.extracted_inputs,
            missing_inputs=[],
            current_node_id=None,
            routing_decision=state.resolved.decision.to_dict(),
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

    async def _build_fixed_graph(self, state: RunCreationState) -> Any:
        scenario = state.resolved.scenario
        try:
            graph = await asyncio.to_thread(
                self.planner.build_graph,
                scenario,
                goal=state.command.input,
                inputs=state.resolved.decision.extracted_inputs,
                user_id=state.context.user_id,
                session_id=state.session_id,
                agent_id=state.agent_id,
                agent_revision_id=state.agent_revision_id,
                idempotency_key=state.context.idempotency_key,
                request_id=state.context.request_id,
                tracker_id=state.context.tracker_id,
                traceparent=state.context.traceparent,
                tracestate=state.context.tracestate,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if graph is not None:
            graph.metadata["orchestration"] = state.resolved.orchestration
            graph.input_asset_ids = list(state.command.input_asset_ids)
        return graph

    async def _publish_selection(self, run_id: str, decision: Any) -> None:
        await self.runtime.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.DECISION_RECORDED.value,
                status="completed",
                data={
                    "source": "runtime_decision",
                    "kind": "execution_selected",
                    "decision": decision.to_dict(),
                },
            )
        )
