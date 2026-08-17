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
                "additionalProperties": False,
                "required": ["name", "objective"],
                "properties": {
                    "id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_.-]{1,128}$",
                    },
                    "name": {"type": "string"},
                    "objective": {"type": "string"},
                    "phase": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "produce",
                            "review",
                            "revise",
                            "synthesize",
                            "checkpoint",
                        ],
                    },
                    "member_id": {"type": "string"},
                    "can_run_in_parallel": {"type": "boolean"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "review_of": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "revision_of": {"type": ["string", "null"]},
                    "review_round": {"type": "integer", "minimum": 0},
                    "output_schema": {"type": ["object", "null"]},
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
    team: dict[str, Any] | None = None,
) -> str:
    catalog = [
        {
            "ref": item.get("ref", {}),
            "description": item.get("description"),
            "execution_mode": item.get("execution_mode"),
            "expected_duration_seconds": item.get("expected_duration_seconds"),
            "team_member_ids": item.get("team_member_ids", []),
        }
        for item in capabilities
        if isinstance(item.get("ref"), dict)
    ]
    skill_catalog = [
        {
            "skill_id": item.get("skill_id"),
            "version": item.get("version"),
            "description": item.get("description"),
            "activation_mode": item.get("activation_mode"),
        }
        for item in capabilities
        if item.get("skill_id")
    ]
    route_hint = {
        "scenario_id": (routing_decision or {}).get("scenario_id"),
        "reason_code": (routing_decision or {}).get("reason_code"),
        "candidate_capabilities": (routing_decision or {}).get("candidate_capabilities", []),
    }
    team_instruction = ""
    if team:
        blueprint = team.get("collaboration_blueprint") or {}
        blueprint_instruction = ""
        if blueprint.get("phases"):
            blueprint_instruction = (
                "A Collaboration Blueprint is frozen for this Run and is binding: every "
                "planned step must fit exactly one blueprint phase (same kind, and its "
                "member_id must be one of that phase's participants); every declared phase "
                "must be covered by at least one step; steps of a phase must depend on "
                "steps of the phases it depends_on; reviewers must differ from the authors "
                "they review; and the plan's widest concurrent level must stay within "
                "guardrails.max_parallel_tasks. Produce the whole plan within this "
                "structure.\n\n"
                f"Frozen Collaboration Blueprint:\n"
                f"{json.dumps(blueprint, ensure_ascii=False)[:8000]}\n\n"
            )
        team_instruction = (
            "A published AgentTeam is frozen for this Run. Assign every planned step to "
            "one listed member_id. The coordinator may assign only itself or members in its "
            "allowed_handoffs. Respect each responsibility and never invent a member. "
            "Build an explicit acyclic DAG: every step needs a stable id, phase, kind, "
            "depends_on and acceptance_criteria. Use review steps for independent criticism, "
            "revise steps for corrections, synthesize for the final decision, and checkpoint "
            "only when the coordinator must assess a completed wave. review_of and revision_of "
            "must reference dependency steps. Keep review_round within the Team budget.\n\n"
            f"{blueprint_instruction}"
            f"Frozen AgentTeam:\n{json.dumps(team, ensure_ascii=False)[:12000]}\n\n"
        )
    return (
        "You are the main coordinator for a multi-user Agent runtime. Classify the request "
        "and produce a concise executable plan. Select only complete catalog ref objects; never "
        "invent or omit their version/plugin fields. Skills are prompt "
        "policies; tools and Agents are executable. A deterministic route candidate with a "
        "scenario_id is mandatory: keep that scenario_id and extract any values you can into "
        "scenario_inputs. Do not replace it with an open-agent plan. Missing scenario fields are "
        "handled by the runtime's structured waiting_input flow, so do not emit a prose question "
        "for a routed scenario. Do not treat nationality, ethnicity, location, gender, or another "
        "preference by itself as a professional role, industry, or research-topic field: leave the "
        "core objective missing so the scenario asks for it. Do not execute work and do not reveal "
        "private chain-of-thought. If no published scenario fits and execution genuinely "
        "requires missing user intent, you may return one concise clarification object (at "
        "most four non-sensitive fields). Prefer choice fields for bounded decisions; never "
        "ask for secrets, credentials, or personal sensitive data. Otherwise return null. "
        "Return only schema-valid JSON.\n\n"
        f"{team_instruction}"
        f"Deterministic route candidate:\n{json.dumps(route_hint, ensure_ascii=False)}\n\n"
        f"Published scenarios:\n{json.dumps(scenarios, ensure_ascii=False)[:20000]}\n\n"
        f"Capability catalog:\n{json.dumps(catalog, ensure_ascii=False)[:24000]}\n\n"
        f"Skill catalog:\n{json.dumps(skill_catalog, ensure_ascii=False)[:6000]}\n\n"
        f"User request:\n{user_prompt}"
    )


def normalize_coordinator_plan(
    value: dict[str, Any],
    capabilities: list[dict[str, Any]],
    scenarios: list[dict[str, Any]] | None = None,
    team: Any | None = None,
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
        if isinstance(item.get("ref"), dict)
    }
    known_skills = {
        str(item.get("skill_id") or "").removeprefix("skill.")
        for item in capabilities
        if str(item.get("skill_id") or "").startswith("skill.")
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
        if str(item).removeprefix("skill.") in known_skills
    ]
    steps = []
    step_ids: set[str] = set()
    team_members = {item.member_id: item for item in team.members} if team else {}
    coordinator = team.coordinator if team else None
    allowed_targets = (
        {coordinator.member_id, *coordinator.allowed_handoffs} if coordinator else set()
    )
    max_steps = int(team.budget_policy["max_tasks"]) if team else 32
    source_steps = value.get("planned_steps") or []
    for index, item in enumerate(source_steps):
        if not isinstance(item, dict) or not str(item.get("objective") or "").strip():
            continue
        if team:
            required_contract = {
                "id",
                "phase",
                "kind",
                "member_id",
                "depends_on",
                "acceptance_criteria",
            }
            missing_contract = required_contract - set(item)
            if missing_contract:
                raise ValueError(
                    "AgentTeam planned step is missing contract fields: "
                    f"{sorted(missing_contract)}"
                )
        raw_id = str(item.get("id") or "").strip()
        if team and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", raw_id):
            raise ValueError("AgentTeam planned step requires a stable id")
        step_id = raw_id or f"step-{index + 1}"
        if step_id in step_ids:
            raise ValueError(f"coordinator planned duplicate step id: {step_id}")
        step_ids.add(step_id)
        kind = str(item.get("kind") or "produce")
        if kind not in {"produce", "review", "revise", "synthesize", "checkpoint"}:
            raise ValueError(f"coordinator planned unsupported step kind: {kind}")
        depends_on = list(
            dict.fromkeys(str(value) for value in item.get("depends_on") or ())
        )
        review_of = list(
            dict.fromkeys(str(value) for value in item.get("review_of") or ())
        )
        revision_of = str(item.get("revision_of") or "").strip() or None
        criteria = [
            str(value).strip()[:500]
            for value in item.get("acceptance_criteria") or ()
            if str(value).strip()
        ][:16]
        review_round = max(0, int(item.get("review_round") or 0))
        step = {
            "id": step_id,
            "name": str(item.get("name") or "step")[:128],
            "objective": str(item.get("objective") or "")[:2000],
            "phase": str(item.get("phase") or "execution")[:128],
            "kind": kind,
            "can_run_in_parallel": bool(item.get("can_run_in_parallel")),
            "depends_on": depends_on,
            "acceptance_criteria": criteria,
            "review_of": review_of,
            "revision_of": revision_of,
            "review_round": review_round,
            "output_schema": (
                dict(item["output_schema"])
                if isinstance(item.get("output_schema"), dict)
                else None
            ),
        }
        if team:
            member_id = str(item.get("member_id") or team.coordinator_member_id)
            if member_id not in team_members:
                raise ValueError(f"coordinator selected an unknown AgentTeam member: {member_id}")
            if member_id not in allowed_targets:
                raise ValueError(f"coordinator is not allowed to hand off to member: {member_id}")
            step["member_id"] = member_id
        steps.append(step)
        if len(steps) > max_steps:
            raise ValueError("coordinator plan exceeds the AgentTeam task budget")
    if team:
        known_step_ids = {str(item["id"]) for item in steps}
        for step in steps:
            references = set(step["depends_on"])
            unknown = references - known_step_ids
            if unknown:
                raise ValueError(
                    f"AgentTeam step {step['id']} has unknown dependencies: {sorted(unknown)}"
                )
            if step["id"] in references:
                raise ValueError(f"AgentTeam step {step['id']} cannot depend on itself")
            review_targets = set(step["review_of"])
            if review_targets and not review_targets <= references:
                raise ValueError(
                    f"AgentTeam review step {step['id']} must depend on every review_of target"
                )
            revision_target = step["revision_of"]
            if revision_target and revision_target not in references:
                raise ValueError(
                    f"AgentTeam revision step {step['id']} must depend on revision_of"
                )
            if step["kind"] == "review" and not review_targets:
                raise ValueError(f"AgentTeam review step {step['id']} requires review_of")
            if step["kind"] == "revise" and not revision_target:
                raise ValueError(f"AgentTeam revise step {step['id']} requires revision_of")
            if (
                step["kind"] in {"synthesize", "checkpoint"}
                and step.get("member_id") != team.coordinator_member_id
            ):
                raise ValueError(
                    f"AgentTeam {step['kind']} step {step['id']} must use the coordinator"
                )
            if step["kind"] in {"review", "revise", "checkpoint"} and not step[
                "acceptance_criteria"
            ]:
                raise ValueError(
                    f"AgentTeam {step['kind']} step {step['id']} requires acceptance criteria"
                )
            if step["review_round"] > int(team.budget_policy["max_review_rounds"]):
                raise ValueError("coordinator plan exceeds the AgentTeam review-round budget")
        handoffs = sum(
            item.get("member_id") != team.coordinator_member_id for item in steps
        )
        if handoffs > int(team.budget_policy["max_handoffs"]):
            raise ValueError("coordinator plan exceeds the AgentTeam handoff budget")
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
