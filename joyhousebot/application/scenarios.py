"""Scenario Studio publication checks and simulation use cases."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.errors import ConflictError, NotFoundError
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.orchestration import ClarificationEngine, ScenarioRouter


class ScenarioStudioService:
    def __init__(self, store: Any) -> None:
        self.store = store
        self.router = ScenarioRouter(store)
        self.clarifications = ClarificationEngine(store)

    async def publish(self, scenario_id: str, version: int, *, actor_id: str) -> dict[str, Any]:
        draft = await self._version(scenario_id, version)
        checks = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.store.get_capability_definition,
                    item.capability_id,
                    item.version,
                )
                for item in draft.allowed_capabilities
            )
        )
        unknown = [
            item.to_dict()
            for item, definition in zip(draft.allowed_capabilities, checks, strict=True)
            if definition is None
            or CapabilityRef.from_dict(dict(definition["ref"])).identity != item.identity
        ]
        if unknown:
            raise ConflictError(
                f"scenario references unpublished capabilities: {sorted(unknown)}"
            )
        try:
            await asyncio.to_thread(
                self.store.publish_scenario,
                scenario_id,
                version,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc
        scenario = await asyncio.to_thread(
            self.store.get_scenario_version, scenario_id, version
        )
        return scenario.to_dict()

    async def simulate(
        self,
        scenario_id: str,
        *,
        prompt: str,
        inputs: dict[str, Any],
        version: int | None = None,
    ) -> dict[str, Any]:
        candidates = [
            item
            for item in await asyncio.to_thread(
                self.store.list_scenario_versions, published_only=False
            )
            if item.scenario_id == scenario_id
            and (version is None or item.version == version)
        ]
        if not candidates:
            raise NotFoundError("scenario version not found")
        scenario = max(candidates, key=lambda item: item.version)
        decision = self.router.decision_for(scenario, inputs, 1.0, "STUDIO_SIMULATION")
        live_decision, live_scenario = self.router.route(prompt, supplied_inputs=inputs)
        step = self.clarifications.evaluate(scenario, decision.extracted_inputs)
        rule_evaluations = self.router.explain_match(scenario, prompt)
        return {
            "target_scenario": {
                "scenario_id": scenario.scenario_id,
                "version": scenario.version,
                "matched": any(item["matched"] for item in rule_evaluations),
                "rule_evaluations": rule_evaluations,
            },
            "live_route": {
                "scenario_id": live_scenario.scenario_id if live_scenario else None,
                "version": live_scenario.version if live_scenario else None,
                "name": live_scenario.name if live_scenario else None,
                "reason_code": live_decision.reason_code,
                "next_action": live_decision.next_action,
            },
            "routing_decision": decision.to_dict(),
            "next_question": (
                {
                    "node_id": step.node.node_id,
                    "question": step.node.question,
                    "fields": list(step.node.field_names),
                }
                if step.node
                else None
            ),
            "ready": step.complete,
        }

    async def _version(self, scenario_id: str, version: int) -> Any:
        versions = await asyncio.to_thread(
            self.store.list_scenario_versions, published_only=False
        )
        draft = next(
            (
                item
                for item in versions
                if item.scenario_id == scenario_id and item.version == version
            ),
            None,
        )
        if draft is None:
            raise NotFoundError("scenario version not found")
        return draft
