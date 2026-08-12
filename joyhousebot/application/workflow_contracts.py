"""Structured Workflow design contracts shared by design and validation."""

from __future__ import annotations

from typing import Any

WORKFLOW_DESIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "name", "summary", "risk_level", "estimated_duration_minutes", "nodes", "policies",
    ],
    "properties": {
        "name": {"type": "string", "maxLength": 128},
        "summary": {"type": "string", "maxLength": 1000},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "estimated_duration_minutes": {"type": "integer", "minimum": 0, "maximum": 10080},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "name", "objective", "kind", "agent_id", "dependencies",
                    "allowed_tools", "skills", "max_attempts",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,128}$"},
                    "name": {"type": "string", "maxLength": 128},
                    "objective": {"type": "string", "maxLength": 2000},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "agent",
                            "team",
                            "scenario",
                            "approval",
                            "verify",
                            "branch",
                            "bounded_loop",
                        ],
                    },
                    "agent_id": {"type": ["string", "null"]},
                    "team_id": {"type": ["string", "null"]},
                    "scenario_id": {"type": ["string", "null"]},
                    "scenario_version": {"type": ["integer", "null"], "minimum": 1},
                    "scenario_inputs": {"type": "object"},
                    "configuration": {"type": "object"},
                    "output_schema": {"type": ["object", "null"]},
                    "verification_policy": {"type": "object"},
                    "dependencies": {
                        "type": "array", "maxItems": 16, "items": {"type": "string"},
                    },
                    "allowed_tools": {
                        "type": "array", "maxItems": 16, "items": {"type": "string"},
                    },
                    "skills": {
                        "type": "array", "maxItems": 16, "items": {"type": "string"},
                    },
                    "max_attempts": {"type": "integer", "minimum": 1, "maximum": 5},
                },
            },
        },
        "policies": {
            "type": "object",
            "additionalProperties": False,
            "required": ["max_concurrent", "fail_fast", "aggregate"],
            "properties": {
                "max_concurrent": {"type": "integer", "minimum": 1, "maximum": 16},
                "fail_fast": {"type": "boolean"},
                "aggregate": {"type": "boolean"},
            },
        },
    },
}

WORKFLOW_CONTROL_GUIDE = """Control-node contracts:
- verify: configuration={"source":"tasks.<dependency>"}; declare output_schema or verification_policy.
- branch: configuration has source, structured_output path, safe cases with when/targets, and default_targets. Every target must depend on the branch node.
- bounded_loop: configuration has one source+path or initial_state, state_path, max_iterations<=32, exit, and an agent/capability template with output_schema.
- approval: configuration may set owner/operator role, expiry, risk, and data classification.
All control nodes use max_attempts=1. Never emit executable expressions; conditions use eq/ne/in/not_in/exists/truthy/contains only.
"""
