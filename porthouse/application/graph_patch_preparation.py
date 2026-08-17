"""Validate and freeze one GraphPatch before its atomic storage transition."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from porthouse.application.errors import ValidationError
from porthouse.application.graph_patch_commands import ApplyGraphPatchCommand
from porthouse.application.graph_validation import (
    graph_snapshot_scope,
    task_executables,
    validate_graph_catalog,
    validate_patch_snapshot_scope,
)
from porthouse.application.run_commands import GraphTaskCommand
from porthouse.domain.capabilities import CapabilityRef
from porthouse.orchestration.failure_policy import validate_saga_declarations
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.graph_revision import freeze_graph_patch_revision, graph_task_rows
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec
from porthouse.storage.contracts import RuntimeStores


@dataclass(frozen=True, slots=True)
class GraphPatchOperationPlan:
    parent_nodes: tuple[dict[str, Any], ...]
    nodes: tuple[GraphTaskSpec, ...]
    changed: tuple[GraphTaskSpec, ...]
    append_ids: tuple[str, ...]
    replace_ids: tuple[str, ...]
    operations: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class GraphPatchPreparation:
    parent: Any
    revision: dict[str, Any]
    task_rows: tuple[dict[str, Any], ...]
    append_ids: tuple[str, ...]
    replace_ids: tuple[str, ...]
    proposer_type: str
    proposer_id: str
    risk: str
    patch_value: dict[str, Any]
    proposal_value: dict[str, Any]


async def prepare_graph_patch(
    stores: RuntimeStores,
    *,
    run: Any,
    context: Any,
    command: ApplyGraphPatchCommand,
) -> GraphPatchPreparation:
    reason = _validate_command(command)
    parent = await asyncio.to_thread(
        stores.graph_patches.get_graph_revision,
        command.base_revision_id,
        expected_user_id=context.user_id,
    )
    if parent is None or parent.run_id != run.run_id:
        raise ValidationError("GraphPatch base revision does not belong to Run")
    operations = _apply_operations(parent, command)
    scope = graph_snapshot_scope(list(operations.parent_nodes), parent.settings)
    try:
        validate_patch_snapshot_scope(list(operations.changed), scope)
        ordered = validate_and_order_graph(list(operations.nodes))
        catalog = await asyncio.to_thread(
            validate_graph_catalog, stores.catalog, ordered
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    spec = _build_graph_spec(run, parent.settings, ordered)
    try:
        validate_saga_declarations(
            ordered,
            catalog,
            spec.failure_policy,
            max_concurrent=spec.max_concurrent,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    proposer_type, proposer_id = _proposer(command, context)
    revision = freeze_graph_patch_revision(
        run.run_id,
        parent.revision_id,
        parent.revision_number + 1,
        spec,
        ordered,
        source=("owner_patch" if proposer_type == "user" else "agent_patch_proposal"),
    )
    operation_values = _freeze_operations(operations, revision)
    risk, risk_reasons = _risk_level(list(operations.changed), catalog)
    if risk == "high" and not command.approve_high_risk and not command.defer_activation:
        raise ValidationError("high-risk GraphPatch requires approve_high_risk=true")
    return _build_preparation(
        run=run,
        context=context,
        command=command,
        reason=reason,
        parent=parent,
        operations=operations,
        operation_values=operation_values,
        revision=revision,
        proposer_type=proposer_type,
        proposer_id=proposer_id,
        risk=risk,
        risk_reasons=risk_reasons,
    )


def _validate_command(command: ApplyGraphPatchCommand) -> str:
    reason = command.reason.strip()
    if not 1 <= len(reason) <= 2000:
        raise ValidationError("GraphPatch reason must contain 1-2000 characters")
    if not 1 <= len(command.operations) <= 32:
        raise ValidationError("GraphPatch requires 1-32 operations")
    return reason


def _apply_operations(
    parent: Any, command: ApplyGraphPatchCommand
) -> GraphPatchOperationPlan:
    parent_nodes = tuple(dict(item.definition) for item in parent.nodes)
    node_map = {node["node_id"]: _definition_spec(node) for node in parent_nodes}
    changed: list[GraphTaskSpec] = []
    append_ids: list[str] = []
    replace_ids: list[str] = []
    seen: set[str] = set()
    values: list[dict[str, Any]] = []
    for operation in command.operations:
        if operation.op not in {"append", "replace_pending"}:
            raise ValidationError(
                f"unsupported GraphPatch operation: {operation.op}"
            )
        task = _task_spec(operation.node)
        if task.id in seen:
            raise ValidationError(
                f"GraphPatch node appears more than once: {task.id}"
            )
        seen.add(task.id)
        if operation.op == "append":
            if task.id in node_map:
                raise ValidationError(
                    f"GraphPatch append node already exists: {task.id}"
                )
            append_ids.append(task.id)
        else:
            if task.id not in node_map:
                raise ValidationError(
                    f"GraphPatch replacement node does not exist: {task.id}"
                )
            replace_ids.append(task.id)
        if not 0 < task.timeout_seconds <= 3600 or not 1 <= task.max_attempts <= 20:
            raise ValidationError(f"GraphPatch node limits are invalid: {task.id}")
        node_map[task.id] = task
        changed.append(task)
        values.append({"op": operation.op, "node_id": task.id})
    return GraphPatchOperationPlan(
        parent_nodes,
        tuple(node_map.values()),
        tuple(changed),
        tuple(append_ids),
        tuple(replace_ids),
        tuple(values),
    )


def _build_graph_spec(
    run: Any, settings: dict[str, Any], ordered: list[GraphTaskSpec]
) -> TaskGraphSpec:
    return TaskGraphSpec(
        goal=str(settings.get("goal") or run.prompt),
        tasks=ordered,
        user_id=run.user_id,
        session_id=run.session_id,
        agent_id=str(settings.get("agent_id") or run.agent_id),
        agent_revision_id=(
            str(settings["agent_revision_id"])
            if settings.get("agent_revision_id")
            else None
        ),
        max_concurrent=max(1, int(settings.get("max_concurrent") or 1)),
        fail_fast=bool(settings.get("fail_fast", False)),
        failure_policy=dict(settings.get("failure_policy") or {}),
        aggregate=bool(settings.get("aggregate", True)),
        aggregation_policy=dict(settings.get("aggregation_policy") or {}),
        max_input_tokens=_optional_int(settings, "max_input_tokens"),
        max_output_tokens=_optional_int(settings, "max_output_tokens"),
        max_cost_usd=_optional_float(settings, "max_cost_usd"),
        metadata=dict(settings.get("metadata") or {}),
    )


def _freeze_operations(
    operations: GraphPatchOperationPlan, revision: dict[str, Any]
) -> list[dict[str, Any]]:
    previous = {node["node_id"]: node for node in operations.parent_nodes}
    current = {node["node_id"]: node for node in revision["nodes"]}
    values = [{**item, "node": current[item["node_id"]]} for item in operations.operations]
    unchanged = [
        node_id
        for node_id in operations.replace_ids
        if previous[node_id] == current[node_id]
    ]
    if unchanged:
        raise ValidationError(
            f"GraphPatch replacements must change the node: {sorted(unchanged)}"
        )
    return values


def _build_preparation(
    *,
    run: Any,
    context: Any,
    command: ApplyGraphPatchCommand,
    reason: str,
    parent: Any,
    operations: GraphPatchOperationPlan,
    operation_values: list[dict[str, Any]],
    revision: dict[str, Any],
    proposer_type: str,
    proposer_id: str,
    risk: str,
    risk_reasons: list[str],
) -> GraphPatchPreparation:
    diff = {
        "added": list(operations.append_ids),
        "replaced": list(operations.replace_ids),
        "unchanged_count": (
            len(revision["nodes"])
            - len(operations.append_ids)
            - len(operations.replace_ids)
        ),
        "base_spec_hash": parent.spec_hash,
        "result_spec_hash": revision["spec_hash"],
    }
    validation = _validation_record(
        command,
        context=context,
        node_count=len(operations.nodes),
        risk=risk,
        risk_reasons=risk_reasons,
    )
    request = {
        "run_id": run.run_id,
        "user_id": context.user_id,
        "base_revision_id": parent.revision_id,
        "reason": reason,
        "operations": operation_values,
        "approve_high_risk": command.approve_high_risk,
        "defer_activation": command.defer_activation,
        "proposer_type": proposer_type,
        "proposer_id": proposer_id,
    }
    request_hash = _canonical_hash(request)
    common = {
        "run_id": run.run_id,
        "user_id": context.user_id,
        "base_revision_id": parent.revision_id,
        "proposer_type": proposer_type,
        "proposer_id": proposer_id,
        "reason": reason,
        "operations": operation_values,
        "diff": diff,
        "validation": validation,
        "request_hash": request_hash,
    }
    task_rows = tuple(graph_task_rows(run.run_id, revision))
    return GraphPatchPreparation(
        parent=parent,
        revision=revision,
        task_rows=task_rows,
        append_ids=operations.append_ids,
        replace_ids=operations.replace_ids,
        proposer_type=proposer_type,
        proposer_id=proposer_id,
        risk=risk,
        patch_value={"patch_id": f"graphpatch_{request_hash}", **common},
        proposal_value={
            "proposal_id": f"graphpatchproposal_{request_hash}",
            **common,
            "candidate_revision": revision,
            "task_rows": list(task_rows),
            "append_ids": list(operations.append_ids),
            "replace_ids": list(operations.replace_ids),
        },
    )


def _validation_record(
    command: ApplyGraphPatchCommand,
    *,
    context: Any,
    node_count: int,
    risk: str,
    risk_reasons: list[str],
) -> dict[str, Any]:
    immediately_approved = (
        risk == "high" and command.approve_high_risk and not command.defer_activation
    )
    return {
        "acyclic": True,
        "node_count": node_count,
        "max_node_count": 128,
        "fan_out_bounded": True,
        "budget_limits_valid": True,
        "snapshot_scope_valid": True,
        "published_capabilities_valid": True,
        "data_classification_not_expanded": True,
        "mutation_scope": "append_or_replace_unstarted",
        "risk": risk,
        "risk_reasons": risk_reasons,
        "approval_required": bool(command.defer_activation),
        "high_risk_approved": immediately_approved,
        "approved_by": context.principal.subject if immediately_approved else None,
    }


def _proposer(command: ApplyGraphPatchCommand, context: Any) -> tuple[str, str]:
    proposer_type = str(command.proposer_type or "user")
    if proposer_type not in {"user", "agent", "system"}:
        raise ValidationError("GraphPatch proposer_type is invalid")
    return proposer_type, str(command.proposer_id or context.principal.subject)


def _risk_level(
    changed: list[GraphTaskSpec], catalog: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    definitions = {
        CapabilityRef.from_dict(dict(item["ref"])).identity: item for item in catalog
    }
    level = "low"
    reasons: set[str] = set()
    for task in changed:
        approval_risk = str(task.approval.get("risk") or "low")
        if approval_risk == "high":
            level = "high"
            reasons.add(f"approval:{task.id}:high")
        elif approval_risk == "medium" and level == "low":
            level = "medium"
            reasons.add(f"approval:{task.id}:medium")
        pinned, _, _ = task_executables(task)
        for reference in pinned:
            definition = definitions.get(reference.identity) or {}
            side_effect = str(definition.get("side_effect") or "none")
            classification = str(
                definition.get("data_classification") or "internal"
            )
            if side_effect not in {"none", "read"}:
                level = "high"
                reasons.add(f"capability:{reference.capability_id}:side_effect")
            if classification == "restricted":
                level = "high"
                reasons.add(f"capability:{reference.capability_id}:restricted")
            elif classification == "confidential" and level == "low":
                level = "medium"
                reasons.add(f"capability:{reference.capability_id}:confidential")
    return level, sorted(reasons)


def _task_spec(command: GraphTaskCommand) -> GraphTaskSpec:
    return GraphTaskSpec(
        id=command.id,
        prompt=command.prompt,
        agent_id=command.agent_id,
        dependencies=list(command.dependencies),
        name=command.name or "",
        timeout_seconds=command.timeout_seconds or 300.0,
        max_attempts=command.max_attempts,
        max_input_tokens=command.max_input_tokens,
        max_output_tokens=command.max_output_tokens,
        max_cost_usd=command.max_cost_usd,
        metadata=dict(command.metadata),
        capability=command.capability,
        capability_input=dict(command.capability_input),
        output_schema=dict(command.output_schema) if command.output_schema else None,
        verification_policy=dict(command.verification_policy),
        max_repairs=command.max_repairs,
        allowed_tools=list(command.allowed_tools),
        skill_names=list(command.skill_names),
        node_type=command.node_type,
        branch=dict(command.branch),
        foreach=dict(command.foreach),
        wait_event=dict(command.wait_event),
        approval=dict(command.approval),
        verify=dict(command.verify),
        compensation=dict(command.compensation),
        bounded_loop=dict(command.bounded_loop),
        aggregate=dict(command.aggregate),
        subrun=dict(command.subrun),
    )


def _definition_spec(value: dict[str, Any]) -> GraphTaskSpec:
    return GraphTaskSpec.from_dict({**value, "id": value["node_id"]})


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _optional_int(values: dict[str, Any], key: str) -> int | None:
    value = values.get(key)
    return int(value) if value is not None else None


def _optional_float(values: dict[str, Any], key: str) -> float | None:
    value = values.get(key)
    return float(value) if value is not None else None


__all__ = ["GraphPatchPreparation", "prepare_graph_patch"]
