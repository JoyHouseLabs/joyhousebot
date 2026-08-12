"""Validation for Graph nodes that execute a frozen child Run."""

from __future__ import annotations

import re
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_COMMON = {"mode", "max_children_per_root"}
_TEAM = {
    "team_id",
    "team_revision_id",
    "team_version",
    "coordinator_member_id",
    "coordinator_agent_id",
    "coordinator_agent_revision_id",
}
_SCENARIO = {
    "scenario_id",
    "scenario_version",
    "planning_mode",
    "agent_id",
    "agent_revision_id",
    "inputs",
}


def _required_id(configuration: dict[str, Any], name: str) -> str:
    value = str(configuration.get(name) or "")
    if _ID.fullmatch(value) is None:
        raise ValueError(f"subrun {name} must be a stable identifier")
    return value


def validate_subrun_configuration(task: Any) -> None:
    configuration = dict(task.subrun or {})
    mode = str(configuration.get("mode") or "")
    allowed = _COMMON | (_TEAM if mode == "team" else _SCENARIO if mode == "scenario" else set())
    unknown = set(configuration) - allowed
    if unknown:
        raise ValueError(f"subrun '{task.id}' has unsupported fields: {sorted(unknown)}")
    if mode not in {"team", "scenario"}:
        raise ValueError(f"subrun '{task.id}' mode must be team or scenario")
    if not task.prompt.strip():
        raise ValueError(f"subrun '{task.id}' requires a goal prompt")
    if task.capability is not None or task.allowed_tools or task.skill_names:
        raise ValueError(f"subrun '{task.id}' cannot widen child execution permissions")
    if task.max_attempts != 1:
        raise ValueError(f"subrun '{task.id}' max_attempts must be 1")
    maximum = configuration.get("max_children_per_root", 32)
    if type(maximum) is not int or not 1 <= maximum <= 256:
        raise ValueError(f"subrun '{task.id}' max_children_per_root is invalid")
    if mode == "team":
        for name in (
            "team_id",
            "team_revision_id",
            "coordinator_member_id",
            "coordinator_agent_id",
            "coordinator_agent_revision_id",
        ):
            _required_id(configuration, name)
        version = configuration.get("team_version")
        if type(version) is not int or version < 1:
            raise ValueError(f"subrun '{task.id}' team_version is invalid")
    else:
        _required_id(configuration, "scenario_id")
        _required_id(configuration, "agent_id")
        _required_id(configuration, "agent_revision_id")
        version = configuration.get("scenario_version")
        if type(version) is not int or version < 1:
            raise ValueError(f"subrun '{task.id}' scenario_version is invalid")
        if str(configuration.get("planning_mode") or "") != "fixed":
            raise ValueError(f"subrun '{task.id}' currently requires a fixed Scenario")
        if not isinstance(configuration.get("inputs"), dict):
            raise ValueError(f"subrun '{task.id}' scenario inputs must be an object")
