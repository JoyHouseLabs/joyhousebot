"""Deterministic capability-node and aggregation contracts for Workflows."""

from __future__ import annotations

from typing import Any

from joyhousebot.application.errors import ValidationError
from joyhousebot.domain.aggregation import normalize_aggregation_policy
from joyhousebot.runtime.models import GraphTaskSpec
from joyhousebot.storage.contracts import AgentCatalogStorePort

_MODEL_NODE_KINDS = {"agent", "team", "scenario"}


def capability_task_spec(node: dict[str, Any]) -> GraphTaskSpec:
    """Compile a normalized capability node into a zero-model graph task."""

    return GraphTaskSpec(
        id=node["id"],
        name=node["name"],
        prompt=node["objective"],
        dependencies=list(node["dependencies"]),
        node_type="capability",
        capability=dict(node["capability"]),
        capability_input=dict(node.get("capability_input") or {}),
        max_attempts=node["max_attempts"],
        output_schema=node.get("output_schema"),
        verification_policy=dict(node.get("verification_policy") or {}),
        metadata={"workflow_node": node["id"]},
    )


def resolve_capability_reference(
    store: AgentCatalogStorePort, raw_value: Any, *, node_id: str
) -> dict[str, Any]:
    """Freeze a Workflow capability node to the current published definition."""

    if isinstance(raw_value, str):
        requested_id = raw_value.strip()
        requested_version: str | None = None
    elif isinstance(raw_value, dict):
        requested_id = str(raw_value.get("capability_id") or "").strip()
        requested_version = str(raw_value.get("version") or "").strip() or None
    else:
        raise ValidationError(
            f"Workflow capability node {node_id} requires a capability reference"
        )
    if not requested_id:
        raise ValidationError(
            f"Workflow capability node {node_id} requires a capability id"
        )
    definition = next(
        (
            item
            for item in store.list_capability_definitions()
            if str(item.get("ref", {}).get("capability_id")) == requested_id
        ),
        None,
    )
    if definition is None or definition.get("ref", {}).get("kind") not in {
            "capability",
        "connector",
    }:
        raise ValidationError(
            f"Workflow capability node {node_id} is not published: {requested_id}"
        )
    reference = dict(definition["ref"])
    if requested_version is not None and str(reference.get("version")) != requested_version:
        raise ValidationError(
            f"Workflow capability node {node_id} expects version {requested_version} "
            f"but {requested_id} is published as {reference.get('version')}"
        )
    return reference


def normalize_explicit_aggregation(raw_value: Any) -> dict[str, Any] | None:
    """Validate an explicit policies.aggregation declaration, if present."""

    raw_aggregation = dict(raw_value or {})
    if not raw_aggregation:
        return None
    try:
        return normalize_aggregation_policy(raw_aggregation).to_dict()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def resolve_aggregation_policy(
    nodes: list[dict[str, Any]], explicit: dict[str, Any] | None
) -> dict[str, Any]:
    """Explicit policy wins; otherwise infer from whether any node drives a model."""

    if explicit:
        return dict(explicit)
    if any(node.get("kind") in _MODEL_NODE_KINDS for node in nodes):
        return {"mode": "llm_synthesis", "version": "v1"}
    # No model-driven node remains: aggregate deterministically instead of
    # paying a coordinator LLM call to summarize tool output.
    return {"mode": "raw", "version": "v1"}
