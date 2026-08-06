"""Structured contract and prompt for the main request coordinator."""

from __future__ import annotations

import json
import re
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
        "clarification": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["question", "fields"],
            "properties": {
                "question": {"type": "string", "maxLength": 1000},
                "help_text": {"type": "string", "maxLength": 2000},
                "fields": {
                    "type": "array", "maxItems": 4,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["name", "label", "value_type", "required"],
                        "properties": {
                            "name": {"type": "string"},
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                            "value_type": {"type": "string", "enum": ["string", "integer", "number", "boolean", "array"]},
                            "required": {"type": "boolean"},
                            "input_mode": {"type": "string", "enum": ["text", "textarea", "single_choice", "multi_choice", "boolean", "number"]},
                            "options": {"type": "array", "items": {"type": "object"}},
                            "allow_other": {"type": "boolean"},
                            "min_selections": {"type": "integer", "minimum": 0},
                            "max_selections": {"type": "integer", "minimum": 1},
                        },
                    },
                },
            },
        },
        "summary": {"type": "string"},
        "execution_class": {
            "type": "string",
            "enum": ["immediate", "interactive", "background"],
        },
        "estimated_duration_seconds": {"type": "integer", "minimum": 0},
        "selected_capabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "capability_id", "version", "kind", "plugin_id",
                    "plugin_version", "plugin_build_digest",
                ],
                "properties": {
                    "capability_id": {"type": "string"},
                    "version": {"type": "string"},
                    "kind": {"type": "string"},
                    "plugin_id": {"type": "string"},
                    "plugin_version": {"type": "string"},
                    "plugin_build_digest": {"type": "string"},
                },
            },
        },
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
            "ref": item.get("ref", {}),
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
        "and produce a concise executable plan. Select only complete catalog ref objects; never "
        "invent or omit their version/plugin fields. Skills are prompt "
        "policies; tools and Agents are executable. A deterministic route candidate with a "
        "scenario_id is mandatory: keep that scenario_id and extract any values you can into "
        "scenario_inputs. Do not replace it with an open-agent plan. Missing scenario fields are "
        "handled by the runtime's structured waiting_input flow, so do not emit a prose question "
        "for a routed scenario. Do not execute work and do not reveal "
        "private chain-of-thought. If no published scenario fits and execution genuinely "
        "requires missing user intent, you may return one concise clarification object (at "
        "most four non-sensitive fields). Prefer choice fields for bounded decisions; never "
        "ask for secrets, credentials, or personal sensitive data. Otherwise return null. "
        "Return only schema-valid JSON.\n\n"
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
        (
            str(item.get("ref", {}).get("capability_id")),
            str(item.get("ref", {}).get("version")),
            str(item.get("ref", {}).get("kind")),
            str(item.get("ref", {}).get("plugin_id")),
            str(item.get("ref", {}).get("plugin_version")),
            str(item.get("ref", {}).get("plugin_build_digest")),
        ): dict(item.get("ref") or {})
        for item in capabilities
    }
    selected = []
    for item in value.get("selected_capabilities") or []:
        if not isinstance(item, dict):
            continue
        identity = (
            str(item.get("capability_id") or ""), str(item.get("version") or ""),
            str(item.get("kind") or ""), str(item.get("plugin_id") or ""),
            str(item.get("plugin_version") or ""), str(item.get("plugin_build_digest") or ""),
        )
        ref = known.get(identity)
        if ref and ref.get("kind") in {"tool", "connector"}:
            selected.append(ref)
    skills = [
        str(item).removeprefix("skill.")
        for item in value.get("selected_skills") or []
        if any(ref.get("capability_id") == str(item) and ref.get("kind") == "skill" for ref in known.values())
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
        "selected_capabilities": list({
            (item["capability_id"], item["version"], item["plugin_id"], item["plugin_version"]): item
            for item in selected
        }.values()),
        "selected_skills": list(dict.fromkeys(skills)),
        "planned_steps": steps,
        "clarification": _normalize_clarification(value.get("clarification")),
    }


def _normalize_clarification(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    question = str(value.get("question") or "").strip()[:1000]
    source_fields = value.get("fields")
    if not question or not isinstance(source_fields, list) or not source_fields:
        return None
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in source_fields[:4]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in seen:
            continue
        value_type = str(raw.get("value_type") or "string")
        input_mode = str(raw.get("input_mode") or "text")
        if value_type not in {"string", "integer", "number", "boolean", "array"}:
            continue
        if input_mode not in {"text", "textarea", "single_choice", "multi_choice", "boolean", "number"}:
            input_mode = "text"
        if input_mode == "multi_choice":
            value_type = "array"
        elif input_mode == "single_choice":
            value_type = "string"
        elif input_mode == "boolean":
            value_type = "boolean"
        elif input_mode == "number" and value_type not in {"integer", "number"}:
            value_type = "number"
        options = []
        for option in raw.get("options") or []:
            if not isinstance(option, dict):
                continue
            option_value = str(option.get("value") or "").strip()[:128]
            label = str(option.get("label") or option_value).strip()[:256]
            if option_value and label and all(item["value"] != option_value for item in options):
                options.append({
                    "value": option_value,
                    "label": label,
                    "description": str(option.get("description") or "").strip()[:512],
                })
        if input_mode in {"single_choice", "multi_choice"} and not options:
            continue
        minimum = raw.get("min_selections")
        maximum = raw.get("max_selections")
        min_selections = max(0, int(minimum)) if isinstance(minimum, int) else None
        max_selections = max(1, int(maximum)) if isinstance(maximum, int) else None
        if min_selections is not None and max_selections is not None and min_selections > max_selections:
            continue
        fields.append({
            "name": name,
            "value_type": value_type,
            "required": bool(raw.get("required", True)),
            "description": str(raw.get("label") or name).strip()[:256],
            "input_mode": input_mode,
            "options": options,
            "allow_other": bool(raw.get("allow_other")),
            "min_selections": min_selections,
            "max_selections": max_selections,
            "enum": [item["value"] for item in options],
            "validation": {},
            "sensitive": False,
        })
        seen.add(name)
    if not fields:
        return None
    return {
        "question": question,
        "help_text": str(value.get("help_text") or "").strip()[:2000],
        "fields": fields,
    }
