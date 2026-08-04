"""Durable, configuration-driven clarification graph engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from joyhousebot.domain.scenarios import ClarificationNode, ScenarioField, ScenarioVersion


@dataclass(frozen=True, slots=True)
class ClarificationStep:
    node: ClarificationNode | None
    missing_inputs: tuple[str, ...]
    collected_inputs: dict[str, Any]

    @property
    def complete(self) -> bool:
        return not self.missing_inputs and self.node is None


class ClarificationEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def evaluate(
        self, scenario: ScenarioVersion, collected_inputs: dict[str, Any]
    ) -> ClarificationStep:
        normalized = self._apply_defaults(scenario, collected_inputs)
        missing = tuple(
            field.name
            for field in scenario.fields
            if field.required and self._missing(normalized.get(field.name))
        )
        node = next(
            (
                item
                for item in scenario.nodes
                if item.kind != "terminal" and any(name in missing for name in item.field_names)
            ),
            None,
        )
        if missing and node is None:
            raise ValueError(f"scenario has no clarification node for fields: {list(missing)}")
        return ClarificationStep(node=node, missing_inputs=missing, collected_inputs=normalized)

    def validate_answers(
        self,
        scenario: ScenarioVersion,
        node: ClarificationNode,
        answers: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {item.name: item for item in scenario.fields if item.name in node.field_names}
        unknown = set(answers) - set(allowed)
        if unknown:
            raise ValueError(f"unexpected input fields: {sorted(unknown)}")
        validated: dict[str, Any] = {}
        for name, value in answers.items():
            self._validate_value(allowed[name], value)
            validated[name] = value
        if not validated:
            raise ValueError("at least one answer is required")
        return validated

    def validate_inputs(self, scenario: ScenarioVersion, values: dict[str, Any]) -> dict[str, Any]:
        fields = {item.name: item for item in scenario.fields}
        unknown = set(values) - set(fields)
        if unknown:
            raise ValueError(f"unexpected scenario fields: {sorted(unknown)}")
        for name, value in values.items():
            if value is not None:
                self._validate_value(fields[name], value)
        return dict(values)

    def create_request(
        self,
        *,
        run_id: str,
        user_id: str,
        scenario: ScenarioVersion,
        step: ClarificationStep,
    ) -> Any:
        if step.node is None:
            raise ValueError("completed clarification has no input request")
        fields_by_name = {item.name: item for item in scenario.fields}
        field_payloads = [
            fields_by_name[name].to_dict()
            for name in step.node.field_names
            if name in step.missing_inputs
        ]
        return self.store.create_input_request(
            input_request_id=f"input_{uuid4().hex}",
            run_id=run_id,
            user_id=user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            node_id=step.node.node_id,
            question=step.node.question,
            fields=field_payloads,
        )

    def resolve(
        self,
        *,
        run_id: str,
        user_id: str,
        input_request_id: str,
        answers: dict[str, Any],
    ) -> tuple[ClarificationStep, Any | None]:
        state = self.store.get_run_scenario_state(run_id, expected_user_id=user_id)
        if state is None:
            raise ValueError("run scenario state not found")
        scenario = self.store.get_scenario_version(state.scenario_id, state.scenario_version)
        if scenario is None:
            raise ValueError("scenario version not found")
        requests = self.store.list_pending_input_requests(run_id, expected_user_id=user_id)
        request = next(
            (item for item in requests if item.input_request_id == input_request_id), None
        )
        if request is None:
            raise ValueError("input request is not pending")
        node = next((item for item in scenario.nodes if item.node_id == request.node_id), None)
        if node is None:
            raise ValueError("clarification node not found")
        validated = self.validate_answers(scenario, node, answers)
        merged = {**state.collected_inputs, **validated}
        step = self.evaluate(scenario, merged)
        resolved = self.store.resolve_input_request(
            input_request_id=input_request_id,
            run_id=run_id,
            user_id=user_id,
            answers=validated,
            collected_inputs=step.collected_inputs,
            missing_inputs=list(step.missing_inputs),
            current_node_id=step.node.node_id if step.node else None,
            scenario_status="ready" if step.complete else "waiting_input",
            # Planning is a durable hand-off state. The application or any
            # coordinator replica will either materialize a fixed graph or
            # queue the existing Agent run after this transaction commits.
            # A completed clarification is executable immediately.  Queue it
            # after the transaction so every worker role can claim it without
            # requiring the resolver to know which coordinator owns planning.
            run_status="queued" if step.complete else "waiting_input",
        )
        if not resolved:
            raise ValueError("input request was already resolved")
        # Wake any coordinator replica immediately.  The run status is durable
        # (planning/queued), but relying only on the polling interval makes a
        # resolved clarification appear stuck to callers of resolve().
        notifier = getattr(self.store, "notify_work", None)
        if callable(notifier):
            notifier(run_id)
        next_request = (
            self.create_request(run_id=run_id, user_id=user_id, scenario=scenario, step=step)
            if step.node
            else None
        )
        return step, next_request

    @staticmethod
    def _apply_defaults(scenario: ScenarioVersion, values: dict[str, Any]) -> dict[str, Any]:
        result = dict(values)
        for field in scenario.fields:
            if field.name not in result and field.default is not None:
                result[field.name] = field.default
        return result

    @staticmethod
    def _missing(value: Any) -> bool:
        return value is None or value == "" or value == []

    @staticmethod
    def _validate_value(field: ScenarioField, value: Any) -> None:
        types: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = types.get(field.value_type)
        if expected and (
            not isinstance(value, expected)
            or field.value_type in {"integer", "number"}
            and isinstance(value, bool)
        ):
            raise ValueError(f"{field.name} must be {field.value_type}")
        if field.enum and value not in field.enum:
            raise ValueError(f"{field.name} must be one of {list(field.enum)}")
        validation = field.validation
        if isinstance(value, str):
            if len(value) < int(validation.get("min_length") or 0):
                raise ValueError(f"{field.name} is too short")
            maximum = validation.get("max_length")
            if maximum is not None and len(value) > int(maximum):
                raise ValueError(f"{field.name} is too long")
        if field.value_type in {"integer", "number"}:
            minimum = validation.get("minimum")
            maximum = validation.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{field.name} must be at least {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{field.name} must be at most {maximum}")
