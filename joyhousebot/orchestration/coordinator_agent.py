"""Structured contract and prompt for the main request coordinator."""

from __future__ import annotations

import json
from typing import Any

COORDINATOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "summary",
        "execution_class",
        "estimated_duration_seconds",
        "selected_capabilities",
        "selected_skills",
        "planned_steps",
        "scenario_id",
        "scenario_inputs",
    ],
    "properties": {
        "intent": {"type": "string"},
        "scenario_id": {"type": ["string", "null"]},
        "scenario_inputs": {"type": "object"},
        "summary": {"type": "string"},
        "execution_class": {
            "type": "string",
            "enum": ["immediate", "interactive", "background"],
        },
        "estimated_duration_seconds": {"type": "integer", "minimum": 0},
        "selected_capabilities": {"type": "array", "items": {"type": "string"}},
        "selected_skills": {"type": "array", "items": {"type": "string"}},
        "planned_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "objective"],
                "properties": {
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "can_run_in_parallel": {"type": "boolean"},
                },
            },
        },
    },
}


def build_coordinator_prompt(
    user_prompt: str,
    *,
    scenarios: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    routing_decision: dict[str, Any] | None = None,
) -> str:
    catalog = [
        {
            "id": item.get("ref", {}).get("capability_id"),
            "kind": item.get("ref", {}).get("kind"),
            "description": item.get("description"),
            "execution_mode": item.get("execution_mode"),
            "expected_duration_seconds": item.get("expected_duration_seconds"),
        }
        for item in capabilities
    ]
    route_hint = {
        "scenario_id": (routing_decision or {}).get("scenario_id"),
        "reason_code": (routing_decision or {}).get("reason_code"),
        "candidate_capabilities": (routing_decision or {}).get("candidate_capabilities", []),
    }
    return (
        "You are the main coordinator for a multi-user Agent cloud. Classify the request "
        "and produce a concise executable plan. Select only catalog ids. Skills are prompt "
        "policies; tools and Agents are executable. A deterministic route candidate is a "
        "strong preference: keep it unless the request clearly conflicts with it. Do not execute work and do not reveal "
        "private chain-of-thought. Return only schema-valid JSON.\n\n"
        f"Deterministic route candidate:\n{json.dumps(route_hint, ensure_ascii=False)}\n\n"
        f"Published scenarios:\n{json.dumps(scenarios, ensure_ascii=False)[:20000]}\n\n"
        f"Capability catalog:\n{json.dumps(catalog, ensure_ascii=False)[:30000]}\n\n"
        f"User request:\n{user_prompt}"
    )


def normalize_coordinator_plan(
    value: dict[str, Any],
    capabilities: list[dict[str, Any]],
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    known = {
        str(item.get("ref", {}).get("capability_id")): str(item.get("ref", {}).get("kind") or "")
        for item in capabilities
    }
    selected = [
        str(item)
        for item in value.get("selected_capabilities") or []
        if known.get(str(item)) in {"tool", "connector"}
    ]
    skills = [
        str(item).removeprefix("skill.")
        for item in value.get("selected_skills") or []
        if known.get(str(item)) == "skill"
    ]
    steps = [
        {
            "name": str(item.get("name") or "step")[:128],
            "objective": str(item.get("objective") or "")[:2000],
            "can_run_in_parallel": bool(item.get("can_run_in_parallel")),
        }
        for item in value.get("planned_steps") or []
        if isinstance(item, dict) and str(item.get("objective") or "").strip()
    ][:32]
    execution_class = str(value.get("execution_class") or "interactive")
    if execution_class not in {"immediate", "interactive", "background"}:
        execution_class = "interactive"
    scenario_map = {str(item.get("scenario_id")): item for item in (scenarios or [])}
    requested_scenario = str(value.get("scenario_id") or "").strip()
    scenario = scenario_map.get(requested_scenario)
    known_fields = {str(item.get("name")) for item in (scenario or {}).get("fields") or []}
    scenario_inputs = {
        str(key): item
        for key, item in dict(value.get("scenario_inputs") or {}).items()
        if str(key) in known_fields
    }
    return {
        "intent": str(value.get("intent") or "general")[:128],
        "scenario_id": requested_scenario if scenario is not None else None,
        "scenario_inputs": scenario_inputs,
        "summary": str(value.get("summary") or "理解并处理用户请求")[:1000],
        "execution_class": execution_class,
        "estimated_duration_seconds": max(
            0, min(int(value.get("estimated_duration_seconds") or 60), 7 * 86400)
        ),
        "selected_capabilities": list(dict.fromkeys(selected)),
        "selected_skills": list(dict.fromkeys(skills)),
        "planned_steps": steps,
    }
