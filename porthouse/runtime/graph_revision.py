"""Canonical immutable Graph snapshots and Runtime Task materialization inputs."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from porthouse.orchestration.failure_policy import normalize_failure_policy
from porthouse.runtime.models import GraphTaskSpec, TaskGraphSpec


def _task_snapshot(task: GraphTaskSpec, default_agent_id: str) -> dict[str, Any]:
    return {
        "node_id": task.id,
        "node_type": str(task.node_type),
        "name": task.name or task.id,
        "agent_id": task.agent_id or default_agent_id,
        "prompt": task.prompt,
        "dependencies": list(task.dependencies),
        "timeout_seconds": task.timeout_seconds,
        "max_attempts": task.max_attempts,
        "max_input_tokens": task.max_input_tokens,
        "max_output_tokens": task.max_output_tokens,
        "max_cost_usd": task.max_cost_usd,
        "metadata": dict(task.metadata),
        "capability": task.capability.to_dict() if task.capability else None,
        "capability_input": dict(task.capability_input),
        "output_schema": dict(task.output_schema) if task.output_schema else None,
        "verification_policy": dict(task.verification_policy),
        "max_repairs": task.max_repairs,
        "allowed_tools": list(task.allowed_tools),
        "skill_names": list(task.skill_names),
        "branch": dict(task.branch),
        "foreach": dict(task.foreach),
        "wait_event": dict(task.wait_event),
        "approval": dict(task.approval),
        "verify": dict(task.verify),
        "compensation": dict(task.compensation),
        "bounded_loop": dict(task.bounded_loop),
        "aggregate": dict(task.aggregate),
        "subrun": dict(task.subrun),
    }


def freeze_graph_revision(
    run_id: str,
    spec: TaskGraphSpec,
    ordered: list[GraphTaskSpec],
    *,
    source: str,
) -> dict[str, Any]:
    nodes = [_task_snapshot(task, spec.agent_id) for task in ordered]
    edges = [
        {
            "edge_id": f"{dependency}->{task.id}",
            "source_node_id": dependency,
            "target_node_id": task.id,
            "edge_type": "dependency",
            "condition": {},
        }
        for task in ordered
        for dependency in task.dependencies
    ]
    failure_policy = normalize_failure_policy(spec.failure_policy, fail_fast=spec.fail_fast)
    settings = {
        "goal": spec.goal,
        "agent_id": spec.agent_id,
        "agent_revision_id": spec.agent_revision_id,
        "max_concurrent": max(1, spec.max_concurrent),
        "fail_fast": bool(spec.fail_fast),
        "failure_policy": failure_policy,
        "aggregate": bool(spec.aggregate),
        "aggregation_policy": dict(spec.aggregation_policy),
        "max_input_tokens": spec.max_input_tokens,
        "max_output_tokens": spec.max_output_tokens,
        "max_cost_usd": spec.max_cost_usd,
        "input_asset_ids": list(spec.input_asset_ids),
        "authority_permissions": list(spec.authority_permissions),
        "metadata": dict(spec.metadata),
    }
    snapshot = {
        "schema_version": 1,
        "revision_number": 1,
        "settings": settings,
        "nodes": nodes,
        "edges": edges,
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    spec_hash = sha256(encoded).hexdigest()
    revision_identity = f"{run_id}:{spec_hash}".encode("utf-8")
    revision_id = f"graphrev_{sha256(revision_identity).hexdigest()}"
    return {
        "schema_version": 1,
        "revision_id": revision_id,
        "revision_number": 1,
        "parent_revision_id": None,
        "source": source,
        "spec_hash": spec_hash,
        "settings": settings,
        "nodes": nodes,
        "edges": edges,
    }


def freeze_graph_patch_revision(
    run_id: str,
    parent_revision_id: str,
    revision_number: int,
    spec: TaskGraphSpec,
    ordered: list[GraphTaskSpec],
    *,
    source: str = "owner_patch",
) -> dict[str, Any]:
    """Freeze the next immutable revision produced by a validated GraphPatch."""
    if revision_number < 2 or not parent_revision_id:
        raise ValueError("patched Graph revision requires a parent and revision number >= 2")
    nodes = [_task_snapshot(task, spec.agent_id) for task in ordered]
    edges = [
        {
            "edge_id": f"{dependency}->{task.id}",
            "source_node_id": dependency,
            "target_node_id": task.id,
            "edge_type": "dependency",
            "condition": {},
        }
        for task in ordered
        for dependency in task.dependencies
    ]
    failure_policy = normalize_failure_policy(spec.failure_policy, fail_fast=spec.fail_fast)
    settings = {
        "goal": spec.goal,
        "agent_id": spec.agent_id,
        "agent_revision_id": spec.agent_revision_id,
        "max_concurrent": max(1, spec.max_concurrent),
        "fail_fast": bool(spec.fail_fast),
        "failure_policy": failure_policy,
        "aggregate": bool(spec.aggregate),
        "aggregation_policy": dict(spec.aggregation_policy),
        "max_input_tokens": spec.max_input_tokens,
        "max_output_tokens": spec.max_output_tokens,
        "max_cost_usd": spec.max_cost_usd,
        "input_asset_ids": list(spec.input_asset_ids),
        "authority_permissions": list(spec.authority_permissions),
        "metadata": dict(spec.metadata),
    }
    snapshot = {
        "schema_version": 1,
        "revision_number": revision_number,
        "settings": settings,
        "nodes": nodes,
        "edges": edges,
    }
    encoded = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    spec_hash = sha256(encoded).hexdigest()
    identity = f"{run_id}:{parent_revision_id}:{spec_hash}".encode("utf-8")
    return {
        "schema_version": 1,
        "revision_id": f"graphrev_{sha256(identity).hexdigest()}",
        "revision_number": revision_number,
        "parent_revision_id": parent_revision_id,
        "source": source,
        "spec_hash": spec_hash,
        "settings": settings,
        "nodes": nodes,
        "edges": edges,
    }


def graph_options(
    base: dict[str, Any],
    spec: TaskGraphSpec,
    revision: dict[str, Any],
    *,
    initial_events_required: bool = False,
) -> dict[str, Any]:
    metadata = {
        **dict(base.get("metadata") or {}),
        **dict(spec.metadata),
    }
    if initial_events_required:
        metadata["_runtime_initial_events_required"] = True
    return {
        **dict(base),
        "goal": spec.goal,
        "user_id": spec.user_id,
        "session_id": spec.session_id,
        "agent_id": spec.agent_id,
        "agent_revision_id": spec.agent_revision_id,
        "max_concurrent": max(1, spec.max_concurrent),
        "fail_fast": spec.fail_fast,
        "failure_policy": dict(revision["settings"]["failure_policy"]),
        "aggregate": spec.aggregate,
        "aggregation_policy": dict(spec.aggregation_policy),
        "max_input_tokens": spec.max_input_tokens,
        "max_output_tokens": spec.max_output_tokens,
        "max_cost_usd": spec.max_cost_usd,
        "idempotency_key": spec.idempotency_key,
        "request_id": spec.request_id,
        "tracker_id": spec.tracker_id,
        "parent_request_id": spec.parent_request_id,
        "traceparent": spec.traceparent,
        "tracestate": spec.tracestate,
        "root_run_id": spec.root_run_id,
        "parent_run_id": spec.parent_run_id,
        "parent_task_id": spec.parent_task_id,
        "max_children_per_root": spec.max_children_per_root,
        "input_asset_ids": list(spec.input_asset_ids),
        "authority_permissions": list(spec.authority_permissions),
        "metadata": metadata,
        "graph_revision_id": revision["revision_id"],
        "tasks": [dict(node) for node in revision["nodes"]],
    }


def graph_task_rows(run_id: str, revision: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    saga = dict(revision["settings"].get("failure_policy") or {}).get("mode") == "saga"
    for index, node in enumerate(revision["nodes"]):
        saga_managed = bool(saga and node["node_type"] == "compensation")
        rows.append(
            {
                "task_id": f"{run_id}:{node['node_id']}",
                "agent_id": node["agent_id"],
                "name": node["name"],
                "payload": {
                    "spec_id": node["node_id"],
                    "graph_revision_id": revision["revision_id"],
                    "node_type": node["node_type"],
                    "agent_id": node["agent_id"],
                    "prompt": node["prompt"],
                    "metadata": node["metadata"],
                    "timeout_seconds": node["timeout_seconds"],
                    "max_input_tokens": node["max_input_tokens"],
                    "max_output_tokens": node["max_output_tokens"],
                    "max_cost_usd": node["max_cost_usd"],
                    "capability": node["capability"],
                    "capability_input": node["capability_input"],
                    "output_schema": node["output_schema"],
                    "verification_policy": node["verification_policy"],
                    "max_repairs": node["max_repairs"],
                    "allowed_tools": node["allowed_tools"],
                    "skill_names": node["skill_names"],
                    "branch": node["branch"],
                    "foreach": node["foreach"],
                    "wait_event": node["wait_event"],
                    "approval": node["approval"],
                    "verify": node["verify"],
                    "compensation": node["compensation"],
                    "bounded_loop": node["bounded_loop"],
                    "aggregate": node["aggregate"],
                    "subrun": node["subrun"],
                    "saga_managed": saga_managed,
                },
                "dependencies": [f"{run_id}:{item}" for item in node["dependencies"]],
                "priority": index,
                "max_attempts": node["max_attempts"],
                "initial_status": "dormant" if saga_managed else None,
            }
        )
    return rows
