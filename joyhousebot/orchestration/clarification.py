"""Durable, configuration-driven clarification graph engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from joyhousebot.domain.scenarios import ClarificationNode, ScenarioField, ScenarioVersion
from joyhousebot.domain.scenarios.clarification_conditions import condition_matches


@dataclass(frozen=True, slots=True)
class ClarificationStep:
    node: ClarificationNode | None
    missing_inputs: tuple[str, ...]
    collected_inputs: dict[str, Any]
    progress: dict[str, int]

    @property
    def complete(self) -> bool:
        return not self.missing_inputs and self.node is None


class ClarificationEngine:
    def __init__(self, store: Any) -> None:
        self.store = store

    def evaluate(
        self,
        scenario: ScenarioVersion,
        collected_inputs: dict[str, Any],
        *,
        from_node_id: str | None = None,
    ) -> ClarificationStep:
        normalized = self._apply_defaults(scenario, collected_inputs)
        missing = tuple(
            field.name
            for field in scenario.fields
            if field.required and self._missing(normalized.get(field.name))
        )
        node = self._next_node(scenario, normalized, missing, from_node_id=from_node_id)
        if missing and node is None:
            raise ValueError(f"scenario has no clarification node for fields: {list(missing)}")
        return ClarificationStep(
            node=node,
            missing_inputs=missing,
            collected_inputs=normalized,
            progress=self._progress(scenario, node),
        )

    @staticmethod
    def _progress(scenario: ScenarioVersion, node: ClarificationNode | None) -> dict[str, int]:
        # Scenario nodes are persisted independently and are not guaranteed to be
        # read back in authoring order.  Derive the visible question sequence from
        # the graph so a root question is always shown as "1 / N".
        by_id = {item.node_id: item for item in scenario.nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
        incoming: set[str] = set()
        for edge in scenario.edges:
            if edge.source_node_id in outgoing and edge.target_node_id in by_id:
                outgoing[edge.source_node_id].append(edge.target_node_id)
                incoming.add(edge.target_node_id)

        questions = [item for item in by_id.values() if item.kind != "terminal"]
        if node is None:
            return {"current": len(questions), "total": len(questions)}

        roots = sorted(node_id for node_id in by_id if node_id not in incoming)
        # Use graph distance rather than persistence order.  Branches at the same
        # decision point therefore both display the same step number.
        distances = {root: 1 for root in roots}
        frontier = list(roots)
        while frontier:
            source = frontier.pop(0)
            for target in sorted(outgoing[source]):
                distance = distances[source] + 1
                if target not in distances or distance < distances[target]:
                    distances[target] = distance
                    frontier.append(target)
        return {
            "current": distances.get(node.node_id, 1),
            "total": len(questions),
        }

    def _next_node(
        self,
        scenario: ScenarioVersion,
        values: dict[str, Any],
        missing: tuple[str, ...],
        *,
        from_node_id: str | None,
    ) -> ClarificationNode | None:
        by_id = {item.node_id: item for item in scenario.nodes}
        if from_node_id:
            source = by_id.get(from_node_id)
            if source is None:
                raise ValueError("clarification node not found")
            target = self._matching_target(scenario, source.node_id, values)
            if target is not None:
                return self._advance(scenario, by_id[target], values, missing)
        if scenario.edges:
            incoming = {edge.target_node_id for edge in scenario.edges}
            roots = [item for item in scenario.nodes if item.node_id not in incoming]
            for root in roots:
                candidate = self._advance(scenario, root, values, missing)
                if candidate is not None:
                    return candidate
        return next(
            (
                item
                for item in scenario.nodes
                if item.kind != "terminal" and any(name in missing for name in item.field_names)
            ),
            None,
        )

    def _advance(
        self,
        scenario: ScenarioVersion,
        node: ClarificationNode,
        values: dict[str, Any],
        missing: tuple[str, ...],
    ) -> ClarificationNode | None:
        visited: set[str] = set()
        by_id = {item.node_id: item for item in scenario.nodes}
        current = node
        while current.node_id not in visited:
            visited.add(current.node_id)
            if current.kind == "terminal":
                return None
            if any(name in missing for name in current.field_names):
                return current
            target = self._matching_target(scenario, current.node_id, values)
            if target is None:
                return None
            current = by_id[target]
        raise ValueError("clarification graph contains a cycle")

    @staticmethod
    def _matching_target(
        scenario: ScenarioVersion, source_node_id: str, values: dict[str, Any]) -> str | None:
        edges = sorted(
            (item for item in scenario.edges if item.source_node_id == source_node_id),
            key=lambda item: item.priority,
            reverse=True,
        )
        for edge in edges:
            if condition_matches(edge.condition, values):
                return edge.target_node_id
        return None

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

    @classmethod
    def validate_request_answers(
        cls, fields: list[dict[str, Any]], answers: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate a persisted generic input schema without a Scenario object."""
        definitions = {
            str(item["name"]): ScenarioField(
                name=str(item["name"]),
                value_type=str(item["value_type"]),
                required=bool(item.get("required")),
                label=str(item.get("label") or ""),
                description=str(item.get("description") or ""),
                placeholder=str(item.get("placeholder") or ""),
                default=item.get("default"),
                enum=tuple(item.get("enum") or ()),
                input_mode=str(item.get("input_mode") or "auto"),
                options=tuple(dict(option) for option in item.get("options") or ()),
                allow_other=bool(item.get("allow_other")),
                min_selections=(int(item["min_selections"]) if item.get("min_selections") is not None else None),
                max_selections=(int(item["max_selections"]) if item.get("max_selections") is not None else None),
                validation=dict(item.get("validation") or {}),
                sensitive=bool(item.get("sensitive")),
                suggestion_provider=dict(item.get("suggestion_provider") or {}),
                normalization=dict(item.get("normalization") or {}),
                visibility=dict(item.get("visibility") or {}),
                constraint_policy=dict(item.get("constraint_policy") or {}),
                confirmation_policy=str(item.get("confirmation_policy") or "none"),
                examples=tuple(str(example) for example in item.get("examples") or ()),
                group=str(item.get("group") or ""),
                order=int(item.get("order") or 0),
            )
            for item in fields
        }
        unknown = set(answers) - set(definitions)
        if unknown:
            raise ValueError(f"unexpected input fields: {sorted(unknown)}")
        if not answers:
            raise ValueError("at least one answer is required")
        validated: dict[str, Any] = {}
        for name, value in answers.items():
            cls._validate_value(definitions[name], value)
            validated[name] = value
        for field in definitions.values():
            if field.required and cls._missing(validated.get(field.name)):
                raise ValueError(f"{field.name} is required")
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
        presentation = {**step.node.configuration, "progress": step.progress}
        if presentation.get("show_collected_inputs"):
            presentation["collected_inputs"] = dict(step.collected_inputs)
        return self.store.create_input_request(
            input_request_id=f"input_{uuid4().hex}",
            run_id=run_id,
            user_id=user_id,
            scenario_id=scenario.scenario_id,
            scenario_version=scenario.version,
            node_id=step.node.node_id,
            question=step.node.question,
            fields=field_payloads,
            presentation=presentation,
        )

    def create_dynamic_request(
        self,
        *,
        run_id: str,
        user_id: str,
        question: str,
        fields: list[dict[str, Any]],
        presentation: dict[str, Any] | None = None,
    ) -> Any:
        if not question.strip():
            raise ValueError("dynamic input request requires a question")
        # Validate the schema itself before it enters the durable queue.
        for item in fields:
            self.validate_request_answers([item], {str(item["name"]): self._schema_sample(item)})
        request_id = f"input_{uuid4().hex}"
        return self.store.create_input_request(
            input_request_id=request_id,
            run_id=run_id,
            user_id=user_id,
            scenario_id="__dynamic__",
            scenario_version=1,
            node_id=f"agent:{request_id}",
            question=question,
            fields=fields,
            presentation={"progress": {"current": 1, "total": 1}, **(presentation or {})},
            source="agent",
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
        step = self.evaluate(scenario, merged, from_node_id=node.node_id)
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
    def _schema_sample(field: dict[str, Any]) -> Any:
        """Construct a harmless value to validate a coordinator-produced field schema."""
        mode = str(field.get("input_mode") or "auto")
        options = field.get("options") or field.get("enum") or ()
        option_values = [
            option.get("value") if isinstance(option, dict) else option
            for option in options
        ]
        option_value = option_values[0] if option_values else None
        if mode == "multi_choice" or str(field.get("value_type")) == "array":
            count = max(1, int(field.get("min_selections") or 0))
            values = [item for item in option_values if item is not None][:count]
            if len(values) < count and bool(field.get("allow_other")):
                values.extend(f"sample-{index}" for index in range(len(values), count))
            return values or ["sample"]
        if str(field.get("value_type")) == "boolean":
            return True
        if str(field.get("value_type")) == "integer":
            return max(1, int((field.get("validation") or {}).get("minimum") or 1))
        if str(field.get("value_type")) == "number":
            return max(1, float((field.get("validation") or {}).get("minimum") or 1))
        if str(field.get("value_type")) == "object":
            return {}
        return option_value or "sample"

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
        choice_values = [str(item.get("value")) for item in field.options] or list(field.enum)
        if field.value_type == "array":
            if field.min_selections is not None and len(value) < field.min_selections:
                raise ValueError(f"{field.name} requires at least {field.min_selections} selections")
            if field.max_selections is not None and len(value) > field.max_selections:
                raise ValueError(f"{field.name} allows at most {field.max_selections} selections")
            invalid = [item for item in value if item not in choice_values]
            if choice_values and invalid and not field.allow_other:
                raise ValueError(f"{field.name} contains unsupported options: {invalid}")
        elif choice_values and value not in choice_values and not field.allow_other:
            raise ValueError(f"{field.name} must be one of {choice_values}")
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
