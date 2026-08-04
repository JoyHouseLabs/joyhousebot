"""Worker-side structured coordination before open-ended Agent execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from joyhousebot.orchestration.clarification import ClarificationEngine
from joyhousebot.orchestration.coordinator_agent import (
    COORDINATOR_OUTPUT_SCHEMA,
    build_coordinator_prompt,
    normalize_coordinator_plan,
)
from joyhousebot.orchestration.planner import ScenarioPlanner, build_coordinator_graph
from joyhousebot.runtime.context import CancellationToken
from joyhousebot.runtime.models import AgentEvent, AgentOptions, AgentUsage, EventType
from joyhousebot.runtime.structured import parse_structured_output


class RequestCoordinationMixin:
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
        scenario_state = await asyncio.to_thread(
            self.store.get_run_scenario_state,
            record.run_id,
            expected_user_id=record.user_id,
        )
        prompt = options.prompt
        tools = list(options.allowed_tools)
        metadata = dict(options.metadata)
        coordination_usage = AgentUsage()
        graph_to_materialize = None
        if scenario_state is not None:
            prompt = (
                f"{options.prompt}\n\n## Validated scenario context\n"
                f"scenario_id: {scenario_state.scenario_id}\n"
                f"inputs: {json.dumps(scenario_state.collected_inputs, ensure_ascii=False)}"
            )
            return prompt, tools, metadata, coordination_usage
        if not bool(options.metadata.get("coordinator_required")):
            return prompt, tools, metadata, coordination_usage

        scenarios, capability_catalog = await asyncio.gather(
            asyncio.to_thread(self.store.list_scenario_versions, published_only=True),
            asyncio.to_thread(self.store.list_capability_definitions),
        )
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
        content, _, coordination_usage = await self._call_agent(
            run_id=record.run_id,
            task_id=None,
            prompt=build_coordinator_prompt(
                options.prompt,
                scenarios=[item.to_dict() for item in scenarios],
                capabilities=capability_catalog,
                routing_decision=dict(options.metadata.get("routing_decision") or {}),
            ),
            user_id=record.user_id,
            session_id=f"{options.session_id}:coordinator",
            agent_id=record.agent_id,
            channel="runtime",
            chat_id="coordinator",
            model=options.model,
            system_prompt=None,
            output_schema=COORDINATOR_OUTPUT_SCHEMA,
            timeout_seconds=min(options.timeout_seconds, 90),
            max_turns=1,
            max_input_tokens=options.max_input_tokens,
            max_output_tokens=min(options.max_output_tokens or 2048, 2048),
            max_cost_usd=options.max_cost_usd,
            permission_mode="coordinator",
            allowed_tools=[],
            disallowed_tools=[],
            cancellation=cancellation,
            sender_id=options.sender_id or record.user_id,
            metadata={"phase": "coordination"},
        )
        raw_plan = parse_structured_output(content, COORDINATOR_OUTPUT_SCHEMA)
        scenario_values = [item.to_dict() for item in scenarios]
        plan = normalize_coordinator_plan(raw_plan, capability_catalog, scenario_values)
        await self._publish_coordination_progress(
            record.run_id,
            f"已识别意图：{plan['intent']}",
            stage="intent_classified",
            status="completed",
            data={"intent": plan["intent"], "execution_class": plan["execution_class"]},
        )
        tools = plan["selected_capabilities"] or tools
        metadata["skill_names"] = plan["selected_skills"]
        metadata["coordinator_plan"] = plan
        if plan["selected_capabilities"]:
            await self._publish_coordination_progress(
                record.run_id,
                "已选择能力：" + "、".join(plan["selected_capabilities"]),
                stage="capabilities_selected",
                status="completed",
                data={"capabilities": plan["selected_capabilities"]},
            )
        prompt = (
            f"{options.prompt}\n\n## Coordinator plan\n"
            f"{json.dumps(plan, ensure_ascii=False)}\n\n"
            "Execute this plan. Independent substantial steps may be delegated to durable "
            "child Agents; keep the final response grounded in their results."
        )
        await asyncio.to_thread(
            self.store.add_runtime_artifact,
            artifact_id=f"{record.run_id}:plan",
            run_id=record.run_id,
            name="coordinator-plan",
            media_type="application/json",
            content=plan,
        )
        selected_scenario = next(
            (item for item in scenarios if item.scenario_id == plan.get("scenario_id")),
            None,
        )
        if selected_scenario is not None:
            await self._publish_coordination_progress(
                record.run_id,
                f"已匹配场景：{selected_scenario.name}",
                stage="scenario_selected",
                status="completed",
                data={
                    "scenario_id": selected_scenario.scenario_id,
                    "scenario_version": selected_scenario.version,
                },
            )
        else:
            await self._publish_coordination_progress(
                record.run_id,
                "未匹配固定场景，交由通用协调流程处理",
                stage="scenario_not_matched",
                status="completed",
            )
        if plan["selected_skills"]:
            await self._publish_coordination_progress(
                record.run_id,
                "已选择 Skill：" + "、".join(plan["selected_skills"]),
                stage="skills_selected",
                status="completed",
                data={"skills": plan["selected_skills"]},
            )
        if selected_scenario is not None:
            clarifications = ClarificationEngine(self.store)
            inputs = clarifications.validate_inputs(
                selected_scenario, dict(plan.get("scenario_inputs") or {})
            )
            step = clarifications.evaluate(selected_scenario, inputs)
            if not step.complete:
                await self._publish_coordination_progress(
                    record.run_id,
                    "搜索条件不完整，正在准备追问",
                    stage="clarification_required",
                    status="waiting_input",
                    data={"missing_fields": list(step.missing_inputs)},
                )
                await asyncio.to_thread(
                    self.store.save_run_scenario_state,
                    run_id=record.run_id,
                    user_id=record.user_id,
                    scenario_id=selected_scenario.scenario_id,
                    scenario_version=selected_scenario.version,
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
                    scenario=selected_scenario,
                    step=step,
                )
                transitioned = await asyncio.to_thread(
                    self.store.update_runtime_run,
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
                            "scenario_id": selected_scenario.scenario_id,
                            "question": request.question,
                            "fields": request.fields,
                        },
                    )
                )
                return None, tools, metadata, coordination_usage
            await asyncio.to_thread(
                self.store.save_run_scenario_state,
                run_id=record.run_id,
                user_id=record.user_id,
                scenario_id=selected_scenario.scenario_id,
                scenario_version=selected_scenario.version,
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
                data={"scenario_id": selected_scenario.scenario_id},
            )
            tools = [
                item
                for item in selected_scenario.allowed_capabilities
                if next(
                    (
                        definition.get("ref", {}).get("kind")
                        for definition in capability_catalog
                        if definition.get("ref", {}).get("capability_id") == item
                    ),
                    None,
                )
                in {"tool", "connector"}
            ]
            await self._publish_coordination_progress(
                record.run_id,
                "已锁定场景允许的执行能力",
                stage="scenario_capabilities_bound",
                status="completed",
                data={"capability_count": len(tools)},
            )
            metadata["skill_names"] = [
                item.removeprefix("skill.")
                for item in selected_scenario.allowed_capabilities
                if item.startswith("skill.")
            ]
            prompt += (
                "\n\n## Validated scenario context\n"
                f"scenario_id: {selected_scenario.scenario_id}\n"
                f"inputs: {json.dumps(step.collected_inputs, ensure_ascii=False)}"
            )
            graph_to_materialize = await asyncio.to_thread(
                ScenarioPlanner(self.store).build_graph,
                selected_scenario,
                goal=options.prompt,
                inputs=step.collected_inputs,
                user_id=record.user_id,
                session_id=record.session_id,
                agent_id=record.agent_id,
                idempotency_key=record.idempotency_key,
                request_id=str(options.request_id or f"req_{record.run_id}"),
            )
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
        if graph_to_materialize is None:
            graph_to_materialize = build_coordinator_graph(
                plan,
                goal=options.prompt,
                user_id=record.user_id,
                session_id=record.session_id,
                agent_id=record.agent_id,
                request_id=str(options.request_id or f"req_{record.run_id}"),
            )
        if graph_to_materialize is not None:
            graph_to_materialize.metadata["coordination_usage"] = coordination_usage.to_dict()
            await self.materialize_graph(
                record.run_id,
                graph_to_materialize,
                worker_id=self.worker_id,
                lease_version=record.lease_version,
            )
            return None, tools, metadata, coordination_usage
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
        return prompt, tools, metadata, coordination_usage
