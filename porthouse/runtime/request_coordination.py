"""Worker-side structured coordination before open-ended Agent execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from porthouse.orchestration.blueprint_compiler import enforce_final_plan_boundary
from porthouse.orchestration.clarification import ClarificationEngine
from porthouse.orchestration.planner import ScenarioPlanner, build_coordinator_graph
from porthouse.runtime.context import CancellationToken
from porthouse.runtime.coordination_preparation import (
    CoordinationPreparation,
    apply_plan_selection,
    binding_ref,
    build_planning_prompt,
    initialize_coordination_preparation,
    load_coordination_catalog,
    normalize_plan,
)
from porthouse.runtime.models import AgentEvent, AgentOptions, AgentUsage, EventType
from porthouse.runtime.plan_confirmation import PlanConfirmationMixin
from porthouse.runtime.planning_loop import run_coordinator_planning


class RequestCoordinationMixin(PlanConfirmationMixin):
    async def _publish_coordination_progress(
        self,
        run_id: str,
        summary: str,
        *,
        stage: str,
        status: str = "running",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Publish a safe, structured narrative for the coordinator UI.

        These events explain the decision path without exposing the provider's
        private chain-of-thought or dumping the full user prompt/catalog.
        """
        await self.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.DECISION_RECORDED.value,
                phase="planning",
                status=status,
                summary=summary,
                data={"stage": stage, **(data or {})},
            )
        )

    async def _prepare_execution(
        self,
        record: Any,
        options: AgentOptions,
        cancellation: CancellationToken,
    ) -> tuple[str | None, list[str], dict[str, Any], AgentUsage]:
        state = await initialize_coordination_preparation(
            self.stores,
            record=record,
            options=options,
        )
        prompt = state.prompt
        tools = state.tools
        metadata = state.metadata
        coordination_usage = state.usage
        scenario_state = state.scenario_state
        if scenario_state is not None:
            prompt = (
                f"{options.prompt}\n\n## Validated scenario context\n"
                f"scenario_id: {scenario_state.scenario_id}\n"
                f"inputs: {json.dumps(scenario_state.collected_inputs, ensure_ascii=False)}"
            )
            return prompt, tools, metadata, coordination_usage
        if not bool(options.metadata.get("coordinator_required")):
            return prompt, tools, metadata, coordination_usage
        await load_coordination_catalog(self.stores, state)
        scenarios = state.scenarios
        capability_catalog = state.capability_catalog
        planning_catalog = state.planning_catalog
        await self._publish_coordination_progress(
            record.run_id,
            "正在读取场景与能力目录",
            stage="catalog_loaded",
            data={"scenario_count": len(scenarios), "capability_count": len(capability_catalog)},
        )
        await self._publish_coordination_progress(
            record.run_id,
            "正在识别请求意图",
            stage="intent_classification",
        )
        scenario_values = [item.to_dict() for item in scenarios]
        routing_decision = dict(options.metadata.get("routing_decision") or {})
        planning = await run_coordinator_planning(
            self,
            record=record,
            options=options,
            cancellation=cancellation,
            user_prompt=build_planning_prompt(state),
            scenarios=scenario_values,
            capabilities=planning_catalog,
            routing_decision=routing_decision,
            normalize=lambda raw_plan: normalize_plan(state, raw_plan),
        )
        plan = planning.plan
        coordination_usage = planning.usage
        state.usage = coordination_usage
        await self._publish_coordination_progress(
            record.run_id,
            f"已识别意图：{plan['intent']}",
            stage="intent_classified",
            status="completed",
            data={"intent": plan["intent"], "execution_class": plan["execution_class"]},
        )
        selected_tool_ids = apply_plan_selection(state, plan)
        if plan["selected_capabilities"]:
            await self._publish_coordination_progress(
                record.run_id,
                "已选择能力：" + "、".join(selected_tool_ids),
                stage="capabilities_selected",
                status="completed",
                data={"capabilities": plan["selected_capabilities"]},
            )
        await asyncio.to_thread(
            self.stores.execution.add_runtime_artifact,
            artifact_id=f"{record.run_id}:plan:{record.lease_version}",
            run_id=record.run_id,
            name="coordinator-plan",
            media_type="application/json",
            content=plan,
            provenance={
                "worker_id": self.worker_id,
                "lease_version": record.lease_version,
                "phase": "planning",
            },
        )
        if await self._prepare_selected_scenario(state):
            return None, state.tools, state.metadata, state.usage
        if await self._request_dynamic_clarification(state):
            return None, state.tools, state.metadata, state.usage
        return await self._finish_coordination(state)

    async def _prepare_selected_scenario(self, state: CoordinationPreparation) -> bool:
        record = state.record
        plan = _required_plan(state)
        if plan["selected_skills"]:
            await self._publish_coordination_progress(
                record.run_id,
                "已选择 Skill：" + "、".join(plan["selected_skills"]),
                stage="skills_selected",
                status="completed",
                data={"skills": plan["selected_skills"]},
            )
        scenario = state.selected_scenario
        if scenario is None:
            await self._publish_coordination_progress(
                record.run_id,
                "未匹配固定场景，交由通用协调流程处理",
                stage="scenario_not_matched",
                status="completed",
            )
            return False
        await self._publish_coordination_progress(
            record.run_id,
            f"已匹配场景：{scenario.name}",
            stage="scenario_selected",
            status="completed",
            data={"scenario_id": scenario.scenario_id, "scenario_version": scenario.version},
        )
        clarifications = ClarificationEngine(self.stores.clarifications)
        inputs = clarifications.validate_inputs(
            scenario, dict(plan.get("scenario_inputs") or {})
        )
        step = clarifications.evaluate(scenario, inputs)
        if not step.complete:
            await self._request_scenario_clarification(state, clarifications, step)
            return True
        await asyncio.to_thread(
            self.stores.scenarios.save_run_scenario_state,
            run_id=record.run_id,
            user_id=record.user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            status="ready",
            collected_inputs=step.collected_inputs,
            missing_inputs=[],
            current_node_id=None,
            routing_decision={**plan, "next_action": "plan"},
        )
        await self._publish_coordination_progress(
            record.run_id,
            "搜索条件完整，开始生成执行计划",
            stage="scenario_inputs_validated",
            status="completed",
            data={"scenario_id": scenario.scenario_id},
        )
        state.tools = [item.capability_id for item in scenario.allowed_capabilities]
        await self._publish_coordination_progress(
            record.run_id,
            "已锁定场景允许的执行能力",
            stage="scenario_capabilities_bound",
            status="completed",
            data={"capability_count": len(state.tools)},
        )
        scenario_skill_names = [
            item.skill_id.removeprefix("skill.") for item in scenario.required_skills
        ]
        state.metadata["skill_names"] = list(
            dict.fromkeys([*state.always_skill_names, *scenario_skill_names])
        )
        state.metadata["skill_refs"] = [
            binding_ref(state.bound_skills[name])
            for name in state.metadata["skill_names"]
            if name in state.bound_skills
        ]
        state.prompt += (
            "\n\n## Validated scenario context\n"
            f"scenario_id: {scenario.scenario_id}\n"
            f"inputs: {json.dumps(step.collected_inputs, ensure_ascii=False)}"
        )
        state.graph = await asyncio.to_thread(
            ScenarioPlanner(self.stores.catalog).build_graph,
            scenario,
            goal=state.options.prompt,
            inputs=step.collected_inputs,
            user_id=record.user_id,
            session_id=record.session_id,
            agent_id=record.agent_id,
            idempotency_key=record.idempotency_key,
            request_id=str(state.options.request_id or f"req_{record.run_id}"),
        )
        return False

    async def _request_scenario_clarification(
        self,
        state: CoordinationPreparation,
        clarifications: ClarificationEngine,
        step: Any,
    ) -> None:
        record = state.record
        plan = _required_plan(state)
        scenario = state.selected_scenario
        await self._publish_coordination_progress(
            record.run_id,
            "搜索条件不完整，正在准备追问",
            stage="clarification_required",
            status="waiting_input",
            data={"missing_fields": list(step.missing_inputs)},
        )
        await asyncio.to_thread(
            self.stores.scenarios.save_run_scenario_state,
            run_id=record.run_id,
            user_id=record.user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            status="waiting_input",
            collected_inputs=step.collected_inputs,
            missing_inputs=list(step.missing_inputs),
            current_node_id=step.node.node_id if step.node else None,
            routing_decision={**plan, "next_action": "clarify"},
        )
        request = await asyncio.to_thread(
            clarifications.create_request,
            run_id=record.run_id,
            user_id=record.user_id,
            scenario=scenario,
            step=step,
        )
        transitioned = await asyncio.to_thread(
            self.stores.runs.update_runtime_run,
            record.run_id,
            status="waiting_input",
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        if not transitioned:
            raise asyncio.CancelledError("run ownership lost before clarification")
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PLAN_CREATED.value,
                phase="planning",
                status="completed",
                summary=plan["summary"],
                data={**plan, "next_action": "clarify"},
            )
        )
        await self.events.publish(
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

    async def _request_dynamic_clarification(
        self, state: CoordinationPreparation
    ) -> bool:
        plan = _required_plan(state)
        clarification = plan.get("clarification")
        if state.selected_scenario is not None or clarification is None:
            return False
        record = state.record
        request = await asyncio.to_thread(
            ClarificationEngine(self.stores.clarifications).create_dynamic_request,
            run_id=record.run_id,
            user_id=record.user_id,
            question=str(clarification["question"]),
            fields=list(clarification["fields"]),
            presentation={"help_text": clarification.get("help_text") or ""},
        )
        transitioned = await asyncio.to_thread(
            self.stores.runs.update_runtime_run,
            record.run_id,
            status="waiting_input",
            worker_id=self.worker_id,
            lease_version=record.lease_version,
        )
        if not transitioned:
            raise asyncio.CancelledError("run ownership lost before dynamic clarification")
        await self._publish_coordination_progress(
            record.run_id,
            "需要补充关键信息后继续执行",
            stage="dynamic_clarification_required",
            status="waiting_input",
            data={"field_count": len(request.fields)},
        )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.USER_INPUT_REQUESTED.value,
                phase="clarifying",
                status="waiting_input",
                summary=request.question,
                data={
                    "input_request_id": request.input_request_id,
                    "source": request.source,
                    "question": request.question,
                    "fields": request.fields,
                    "presentation": request.presentation,
                },
            )
        )
        return True

    async def _finish_coordination(
        self, state: CoordinationPreparation
    ) -> tuple[str | None, list[str], dict[str, Any], AgentUsage]:
        record = state.record
        options = state.options
        plan = _required_plan(state)
        team_scope = state.team_scope
        team = team_scope.team
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PLAN_CREATED.value,
                phase="planning",
                status="completed",
                summary=plan["summary"],
                data=plan,
            )
        )
        graph = state.graph
        if graph is None:
            graph = build_coordinator_graph(
                plan,
                goal=options.prompt,
                user_id=record.user_id,
                session_id=record.session_id,
                agent_id=record.agent_id,
                request_id=str(options.request_id or f"req_{record.run_id}"),
                team=team,
                member_capabilities=team_scope.member_capabilities,
                member_skills=team_scope.member_skills,
                member_skill_refs=team_scope.member_skill_refs,
                shared_inputs=state.dynamic_inputs,
                team_workspace_run_id=record.run_id if team is not None else None,
            )
        if graph is not None:
            if team is not None and state.frozen_blueprint:
                # Final gate: only compiler-approved plans reach the scheduler.
                enforce_final_plan_boundary(plan, state.frozen_blueprint, team=team)
                if state.frozen_blueprint.get("guardrails", {}).get(
                    "require_plan_confirmation"
                ):
                    generation = int(options.metadata.get("plan_generation") or 0) + 1
                    confirmation = await asyncio.to_thread(
                        self.stores.plan_confirmations.get_plan_confirmation,
                        record.run_id,
                    )
                    confirmed_for_generation = (
                        confirmation is not None
                        and confirmation["status"] == "confirmed"
                        and int(confirmation["plan_version"]) == generation
                    )
                    if not confirmed_for_generation:
                        await self._await_plan_confirmation(
                            record,
                            options=options,
                            team=team,
                            plan=plan,
                            graph=graph,
                            generation=generation,
                        )
                        return None, state.tools, state.metadata, state.usage
            graph.metadata["coordination_usage"] = state.usage.to_dict()
            await self.materialize_graph(
                record.run_id,
                graph,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            return None, state.tools, state.metadata, state.usage
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PHASE_COMPLETED.value,
                phase="planning",
                status="completed",
                summary=plan["summary"],
                data={
                    "execution_class": plan["execution_class"],
                    "estimated_duration_seconds": plan["estimated_duration_seconds"],
                },
            )
        )
        await self.events.publish(
            AgentEvent(
                run_id=record.run_id,
                type=EventType.PHASE_STARTED.value,
                phase="executing",
                status="running",
                summary="正在执行计划",
                data={"name": "executing"},
            )
        )
        return state.prompt, state.tools, state.metadata, state.usage


def _required_plan(state: CoordinationPreparation) -> dict[str, Any]:
    if state.plan is None:
        raise RuntimeError("coordination plan is not prepared")
    return state.plan
