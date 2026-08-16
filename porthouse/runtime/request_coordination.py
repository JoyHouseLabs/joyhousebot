"""Worker-side structured coordination before open-ended Agent execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from porthouse.domain.capabilities import capability_id, capability_kind
from porthouse.orchestration.clarification import ClarificationEngine
from porthouse.orchestration.coordinator_agent import normalize_coordinator_plan
from porthouse.orchestration.planner import ScenarioPlanner, build_coordinator_graph
from porthouse.runtime.context import CancellationToken
from porthouse.runtime.models import AgentEvent, AgentOptions, AgentUsage, EventType
from porthouse.runtime.planning_loop import run_coordinator_planning
from porthouse.runtime.team_coordination import resolve_team_coordination_scope


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
        capability_catalog = await asyncio.to_thread(
            self.store.list_capability_definitions
        )
        snapshot = await asyncio.to_thread(
            self.store.get_run_execution_snapshot, record.run_id
        )
        skill_bindings = list(snapshot.skill_bindings if snapshot is not None else ())
        bound_skills = {
            str(item.get("skill_id") or "").removeprefix("skill."): dict(item)
            for item in skill_bindings
            if str(item.get("skill_id") or "").startswith("skill.")
        }

        def binding_ref(binding: dict[str, Any]) -> dict[str, str]:
            return {
                "skill_id": str(binding["skill_id"]),
                "version": str(binding["skill_version"]),
                "content_sha256": str(binding.get("content_sha256") or ""),
            }

        always_skill_names = [
            name
            for name, binding in bound_skills.items()
            if binding.get("activation_mode") == "always"
        ]
        team_scope = await resolve_team_coordination_scope(
            self.store,
            record=record,
            metadata=options.metadata,
            capability_catalog=capability_catalog,
            snapshot=snapshot,
        )
        team = team_scope.team
        effective_capabilities = team_scope.effective_capabilities
        member_capabilities = team_scope.member_capabilities
        member_skills = team_scope.member_skills
        member_skill_refs = team_scope.member_skill_refs
        effective_tools = [
            capability_id(item)
            for item in capability_catalog
            if capability_id(item) in effective_capabilities
            and capability_kind(item) in {"tool", "connector"}
        ]
        scenario_state = await asyncio.to_thread(
            self.store.get_run_scenario_state,
            record.run_id,
            expected_user_id=record.user_id,
        )
        prompt = options.prompt
        requested_tools = list(options.allowed_tools)
        requested_skill_ids = [
            str(item.get("skill_id") or item.get("capability_id") or "").strip()
            for item in (options.metadata.get("skill_refs") or ())
            if isinstance(item, dict)
            and str(item.get("skill_id") or item.get("capability_id") or "").strip()
        ]
        requested_skill_names = [
            str(item).removeprefix("skill.")
            for item in (options.metadata.get("skill_names") or ())
            if str(item).strip()
        ]
        unauthorized_capabilities = [
            item
            for item in dict.fromkeys(requested_tools)
            if item not in effective_capabilities
        ]
        unauthorized_skills = [
            item
            for item in dict.fromkeys(
                [
                    *requested_skill_names,
                    *(item.removeprefix("skill.") for item in requested_skill_ids),
                ]
            )
            if item not in bound_skills
        ]
        if unauthorized_capabilities:
            raise ValueError(
                "Agent revision does not authorize requested capabilities: "
                + ", ".join(unauthorized_capabilities)
            )
        if unauthorized_skills:
            raise ValueError(
                "Agent revision does not bind requested Skills: "
                + ", ".join(f"skill.{item}" for item in unauthorized_skills)
            )
        for requested_ref in options.metadata.get("skill_refs") or ():
            if not isinstance(requested_ref, dict):
                continue
            requested_id = str(
                requested_ref.get("skill_id")
                or requested_ref.get("capability_id")
                or ""
            )
            binding = bound_skills.get(requested_id.removeprefix("skill."))
            if binding is None:
                continue
            requested_version = str(requested_ref.get("version") or "")
            requested_digest = str(requested_ref.get("content_sha256") or "")
            if requested_version and requested_version != str(binding["skill_version"]):
                raise ValueError(
                    "requested Skill version does not match the Agent binding: "
                    f"{requested_id}@{requested_version}"
                )
            if requested_digest and requested_digest != str(
                binding.get("content_sha256") or ""
            ):
                raise ValueError(
                    "requested Skill digest does not match the Agent binding: "
                    f"{requested_id}@{requested_version or binding['skill_version']}"
                )
        caller_allowlist_enforced = bool(
            options.metadata.get("caller_tool_allowlist_enforced")
        )
        tools = requested_tools if caller_allowlist_enforced else requested_tools or effective_tools
        initial_skill_names = list(
            dict.fromkeys([*always_skill_names, *requested_skill_names])
        )
        metadata = {
            **dict(options.metadata),
            "capability_allowlist_enforced": True,
            "effective_capabilities": sorted(effective_capabilities),
            "skill_names": initial_skill_names,
            "skill_refs": [
                binding_ref(bound_skills[name])
                for name in initial_skill_names
                if name in bound_skills
            ],
            "skill_binding_enforced": True,
        }
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
        dynamic_inputs = dict(options.metadata.get("dynamic_inputs") or {})

        scenarios = (
            []
            if dict(options.metadata.get("orchestration") or {}).get("mode") != "scenario"
            else await asyncio.to_thread(
                self.store.list_scenario_versions, published_only=True
            )
        )

        def scenario_is_available(item: Any) -> bool:
            if any(
                reference.capability_id not in effective_capabilities
                for reference in item.allowed_capabilities
            ):
                return False
            for reference in item.required_skills:
                binding = bound_skills.get(reference.skill_id.removeprefix("skill."))
                if binding is None:
                    return False
                if str(binding.get("skill_version") or "") != reference.version:
                    return False
                if str(binding.get("content_sha256") or "") != reference.content_sha256:
                    return False
            return True

        scenarios = [
            item for item in scenarios if scenario_is_available(item)
        ]
        capability_catalog = [
            {
                **item,
                "team_member_ids": [
                    member_id
                    for member_id, allowed in member_capabilities.items()
                    if capability_id(item) in allowed
                ],
            }
            for item in capability_catalog
            if capability_id(item) in effective_capabilities
        ]
        coordinator_skills: list[dict[str, Any]] = []
        for name, binding in bound_skills.items():
            if binding.get("activation_mode") not in {"always", "coordinator_selected"}:
                continue
            skill = await asyncio.to_thread(
                self.store.get_published_skill,
                str(binding["skill_id"]),
                str(binding["skill_version"]),
            )
            if skill is None or str(skill.get("content_sha256") or "") != str(
                binding.get("content_sha256") or ""
            ):
                raise ValueError(
                    "Agent Skill binding is no longer available with its exact digest: "
                    f"{binding['skill_id']}@{binding['skill_version']}"
                )
            coordinator_skills.append(
                {
                    "skill_id": str(binding["skill_id"]),
                    "version": str(binding["skill_version"]),
                    "content_sha256": str(binding.get("content_sha256") or ""),
                    "name": str(skill.get("name") or name),
                    "description": str(skill.get("description") or ""),
                    "activation_mode": str(binding.get("activation_mode") or ""),
                }
            )
        planning_catalog = [*capability_catalog, *coordinator_skills]
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

        def normalize(raw_plan: dict[str, Any]) -> dict[str, Any]:
            normalized = normalize_coordinator_plan(
                raw_plan, planning_catalog, scenario_values, team=team
            )
            # A deterministic route is a contract, not merely a model hint.
            return _enforce_routed_scenario(
                normalized,
                scenarios=scenarios,
                routing_decision=routing_decision,
                supplied_inputs=dict(options.metadata.get("scenario_inputs") or {}),
            )

        planning = await run_coordinator_planning(
            self,
            record=record,
            options=options,
            cancellation=cancellation,
            user_prompt=(
                f"{options.prompt}\n\n## Answers already supplied by the user\n"
                f"{json.dumps(dynamic_inputs, ensure_ascii=False)}"
                if dynamic_inputs
                else options.prompt
            ),
            scenarios=scenario_values,
            capabilities=planning_catalog,
            routing_decision=routing_decision,
            normalize=normalize,
        )
        plan = planning.plan
        coordination_usage = planning.usage
        await self._publish_coordination_progress(
            record.run_id,
            f"已识别意图：{plan['intent']}",
            stage="intent_classified",
            status="completed",
            data={"intent": plan["intent"], "execution_class": plan["execution_class"]},
        )
        selected_tool_ids = [
            str(item.get("capability_id"))
            for item in plan["selected_capabilities"]
            if isinstance(item, dict)
        ]
        tools = selected_tool_ids or tools
        selected_skill_names = list(
            dict.fromkeys([*always_skill_names, *plan["selected_skills"]])
        )
        metadata["skill_names"] = selected_skill_names
        metadata["skill_refs"] = [
            binding_ref(bound_skills[name])
            for name in selected_skill_names
            if name in bound_skills
        ]
        metadata["coordinator_plan"] = plan
        if plan["selected_capabilities"]:
            await self._publish_coordination_progress(
                record.run_id,
                "已选择能力：" + "、".join(selected_tool_ids),
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
                            "presentation": request.presentation,
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
                item.capability_id for item in selected_scenario.allowed_capabilities
            ]
            await self._publish_coordination_progress(
                record.run_id,
                "已锁定场景允许的执行能力",
                stage="scenario_capabilities_bound",
                status="completed",
                data={"capability_count": len(tools)},
            )
            scenario_skill_names = [
                item.skill_id.removeprefix("skill.")
                for item in selected_scenario.required_skills
            ]
            metadata["skill_names"] = list(
                dict.fromkeys([*always_skill_names, *scenario_skill_names])
            )
            metadata["skill_refs"] = [
                binding_ref(bound_skills[name])
                for name in metadata["skill_names"]
                if name in bound_skills
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
        clarification = plan.get("clarification")
        if selected_scenario is None and clarification is not None:
            clarifications = ClarificationEngine(self.store)
            request = await asyncio.to_thread(
                clarifications.create_dynamic_request,
                run_id=record.run_id,
                user_id=record.user_id,
                question=str(clarification["question"]),
                fields=list(clarification["fields"]),
                presentation={"help_text": clarification.get("help_text") or ""},
            )
            transitioned = await asyncio.to_thread(
                self.store.update_runtime_run,
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
            return None, tools, metadata, coordination_usage
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
                team=team,
                member_capabilities=member_capabilities,
                member_skills=member_skills,
                member_skill_refs=member_skill_refs,
                shared_inputs=dynamic_inputs,
                team_workspace_run_id=record.run_id if team is not None else None,
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


def _enforce_routed_scenario(
    plan: dict[str, Any],
    *,
    scenarios: list[Any],
    routing_decision: dict[str, Any],
    supplied_inputs: dict[str, Any],
) -> dict[str, Any]:
    """Pin a coordinator plan to a deterministic scenario route.

    The model remains responsible for extracting values such as a username
    from free text. Scenario selection itself is decided by the router and is
    therefore enforced before clarification evaluation. This keeps every
    missing field on the same durable ``waiting_input`` path.
    """

    routed_id = str(routing_decision.get("scenario_id") or "").strip()
    if not routed_id:
        return plan
    selected = next(
        (item for item in scenarios if str(getattr(item, "scenario_id", "")) == routed_id),
        None,
    )
    if selected is None:
        return plan
    known_fields = {str(item.name) for item in selected.fields}
    extracted = {
        str(key): value
        for key, value in dict(plan.get("scenario_inputs") or {}).items()
        if str(key) in known_fields
    }
    # Explicit scenario_inputs supplied by an API/plugin take precedence over
    # model extraction; model-extracted values fill the remaining fields.
    merged = {**extracted, **{key: value for key, value in supplied_inputs.items() if key in known_fields}}
    enforced = dict(plan)
    enforced["scenario_id"] = routed_id
    enforced["scenario_inputs"] = merged
    enforced["routing_enforced"] = True
    return enforced
