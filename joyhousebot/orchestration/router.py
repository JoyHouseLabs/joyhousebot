"""Deterministic scenario routing before model-based planning."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.scenarios import RoutingDecision, ScenarioVersion
from joyhousebot.storage.contracts import ScenarioStorePort


class ScenarioRouter:
    def __init__(self, store: ScenarioStorePort) -> None:
        self.store = store

    def route(
        self,
        prompt: str,
        *,
        explicit_scenario_id: str | None = None,
        explicit_scenario_version: int | None = None,
        supplied_inputs: dict[str, Any] | None = None,
    ) -> tuple[RoutingDecision, ScenarioVersion | None]:
        inputs = dict(supplied_inputs or {})
        if explicit_scenario_id:
            scenario = self.store.get_scenario_version(
                explicit_scenario_id, explicit_scenario_version
            )
            if scenario is None or scenario.status != "published":
                raise ValueError(f"published scenario not found: {explicit_scenario_id}")
            return self.decision_for(scenario, inputs, 1.0, "EXPLICIT_SCENARIO"), scenario

        normalized = prompt.casefold()
        matches: list[tuple[int, int, ScenarioVersion]] = []
        for scenario in self.store.list_scenario_versions(published_only=True):
            for rule in scenario.routing_rules:
                any_terms = [str(item).casefold() for item in rule.get("contains_any") or ()]
                all_terms = [str(item).casefold() for item in rule.get("contains_all") or ()]
                excluded_terms = [str(item).casefold() for item in rule.get("excludes_any") or ()]
                has_match_terms = bool(any_terms or all_terms)
                matches_any = not any_terms or any(term in normalized for term in any_terms)
                matches_all = all(term in normalized for term in all_terms)
                has_excluded = any(term in normalized for term in excluded_terms)
                if has_match_terms and matches_any and matches_all and not has_excluded:
                    priority = int(rule.get("priority") or 0)
                    specificity = len(any_terms) + (2 * len(all_terms))
                    matches.append((priority, specificity, scenario))

        if matches:
            _, _, scenario = max(matches, key=lambda item: (item[0], item[1]))
            return self.decision_for(scenario, inputs, 0.9, "RULE_CONTAINS"), scenario

        return self.open_decision(inputs), None

    @staticmethod
    def open_decision(
        inputs: dict[str, Any] | None = None,
        *,
        reason_code: str = "EXPLICIT_AGENT_MODE",
    ) -> RoutingDecision:
        return RoutingDecision(
            scenario_id=None,
            scenario_version=None,
            confidence=1.0,
            execution_class="interactive",
            estimated_duration_seconds=60,
            extracted_inputs=dict(inputs or {}),
            missing_inputs=(),
            candidate_capabilities=(),
            next_action="plan",
            reason_code=reason_code,
        )

    @staticmethod
    def explain_match(scenario: ScenarioVersion, prompt: str) -> list[dict[str, Any]]:
        """Explain deterministic rule evaluation for Studio and audit views.

        This intentionally contains no model judgement. It gives operators a
        reproducible answer to "would this request select this scenario?".
        """
        normalized = prompt.casefold()
        results = []
        for index, rule in enumerate(scenario.routing_rules):
            any_terms = [str(item).casefold() for item in rule.get("contains_any") or ()]
            all_terms = [str(item).casefold() for item in rule.get("contains_all") or ()]
            excluded_terms = [str(item).casefold() for item in rule.get("excludes_any") or ()]
            has_match_terms = bool(any_terms or all_terms)
            matched_any = [term for term in any_terms if term in normalized]
            matched_all = [term for term in all_terms if term in normalized]
            matched_excluded = [term for term in excluded_terms if term in normalized]
            matched = (
                has_match_terms
                and (not any_terms or bool(matched_any))
                and len(matched_all) == len(all_terms)
                and not matched_excluded
            )
            results.append(
                {
                    "index": index,
                    "matched": matched,
                    "priority": int(rule.get("priority") or 0),
                    "specificity": len(any_terms) + (2 * len(all_terms)),
                    "matched_any": matched_any,
                    "missing_all": [term for term in all_terms if term not in matched_all],
                    "matched_excluded": matched_excluded,
                }
            )
        return results

    @staticmethod
    def decision_for(
        scenario: ScenarioVersion,
        inputs: dict[str, Any],
        confidence: float,
        reason_code: str,
    ) -> RoutingDecision:
        normalized_inputs = dict(inputs)
        for item in scenario.fields:
            if item.name not in normalized_inputs and item.default is not None:
                normalized_inputs[item.name] = item.default
        missing = tuple(
            item.name
            for item in scenario.fields
            if item.required
            and (item.name not in normalized_inputs or normalized_inputs[item.name] in (None, ""))
        )
        policy = scenario.execution_policy
        return RoutingDecision(
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            confidence=confidence,
            execution_class=str(policy.get("execution_class") or "interactive"),
            estimated_duration_seconds=int(policy.get("estimated_duration_seconds") or 60),
            extracted_inputs=normalized_inputs,
            missing_inputs=missing,
            candidate_capabilities=tuple(
                {**item.to_dict(), "reason_code": "SCENARIO_ALLOWED"}
                for item in scenario.allowed_capabilities
            ),
            next_action="clarify" if missing else "plan",
            reason_code=reason_code,
        )
