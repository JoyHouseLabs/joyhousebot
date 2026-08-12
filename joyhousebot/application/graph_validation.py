"""Shared catalog and immutable-snapshot validation for Graph submissions."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.orchestration.bounded_loop import bounded_loop_template_capability
from joyhousebot.orchestration.control_nodes import validate_compensation_declarations
from joyhousebot.orchestration.foreach import foreach_template_capability


def task_executables(task: Any) -> tuple[list[CapabilityRef], list[str], list[str]]:
    pinned = [task.capability] if task.capability else []
    tools = list(task.allowed_tools or [])
    skills = list(task.skill_names or [])
    if task.node_type == "foreach":
        template = dict(task.foreach.get("template") or {})
        tools.extend(str(item) for item in template.get("allowed_tools") or [])
        skills.extend(str(item) for item in template.get("skill_names") or [])
        capability = foreach_template_capability(task.foreach)
        if capability is not None:
            pinned.append(capability)
    if task.node_type == "bounded_loop":
        template = dict(task.bounded_loop.get("template") or {})
        tools.extend(str(item) for item in template.get("allowed_tools") or [])
        skills.extend(str(item) for item in template.get("skill_names") or [])
        capability = bounded_loop_template_capability(task.bounded_loop)
        if capability is not None:
            pinned.append(capability)
    return pinned, tools, skills


def validate_graph_catalog(store: Any, tasks: list[Any]) -> list[dict[str, Any]]:
    """Require every executable reference to resolve to a published definition."""
    latest = store.list_capability_definitions()
    latest_by_id = {
        CapabilityRef.from_dict(dict(item["ref"])).capability_id: item for item in latest
    }
    published_skills = {
        str(item["skill_id"]): dict(item.get("current") or {})
        for item in store.list_skills(active_only=True)
        if item.get("current")
    }
    exact: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for task in tasks:
        pinned, tools, skills = task_executables(task)
        for reference in pinned:
            definition = store.get_capability_definition(reference.capability_id, reference.version)
            try:
                published = CapabilityRef.from_dict(dict((definition or {})["ref"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "graph task references unavailable pinned executable capability: "
                    f"{reference.capability_id}@{reference.version}"
                ) from exc
            if published.identity != reference.identity or published.kind.value not in {
                "tool",
                "connector",
            }:
                raise ValueError(
                    "graph task references unavailable pinned executable capability: "
                    f"{reference.capability_id}@{reference.version}"
                )
            exact[published.identity] = definition
        for capability_id in [*tools, *(item.capability_id for item in pinned)]:
            definition = latest_by_id.get(capability_id)
            if (definition or {}).get("ref", {}).get("kind") not in {"tool", "connector"}:
                if not any(identity[0] == capability_id for identity in exact):
                    raise ValueError(
                        f"graph task references unavailable executable capability: {capability_id}"
                    )
        for skill_name in skills:
            capability_id = skill_name if skill_name.startswith("skill.") else f"skill.{skill_name}"
            if capability_id not in published_skills:
                raise ValueError(f"graph task references unavailable skill: {skill_name}")
            refs = {
                str(item.get("skill_id") or ""): item
                for item in dict(getattr(task, "metadata", {}) or {}).get("skill_refs") or []
                if isinstance(item, dict)
            }
            requested = refs.get(capability_id)
            if requested is not None:
                published = store.get_published_skill(
                    capability_id, str(requested.get("version") or "")
                )
                if published is None or (
                    requested.get("content_sha256")
                    and str(requested["content_sha256"])
                    != str(published.get("content_sha256") or "")
                ):
                    raise ValueError(
                        f"graph task references unavailable Skill version: {capability_id}"
                    )
    catalog = list(
        {
            CapabilityRef.from_dict(dict(item["ref"])).identity: item
            for item in [*latest, *exact.values()]
        }.values()
    )
    validate_compensation_declarations(tasks, catalog)
    return catalog


def graph_snapshot_scope(nodes: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, set]:
    """Return the exact Agent/Capability scope frozen by a parent revision."""
    agents = {str(settings.get("agent_id") or "default")}
    pinned: set[tuple[str, str, str, str, str, str]] = set()
    tools: set[str] = set()
    skills: set[str] = set()
    for node in nodes:
        agents.add(str(node.get("agent_id") or settings.get("agent_id") or "default"))
        if node.get("capability"):
            pinned.add(CapabilityRef.from_dict(dict(node["capability"])).identity)
        tools.update(str(item) for item in node.get("allowed_tools") or [])
        skills.update(str(item) for item in node.get("skill_names") or [])
        for configuration_name in ("foreach", "bounded_loop"):
            template = dict((node.get(configuration_name) or {}).get("template") or {})
            tools.update(str(item) for item in template.get("allowed_tools") or [])
            skills.update(str(item) for item in template.get("skill_names") or [])
            if template.get("capability"):
                pinned.add(CapabilityRef.from_dict(dict(template["capability"])).identity)
    return {"agents": agents, "pinned": pinned, "tools": tools, "skills": skills}


def validate_patch_snapshot_scope(tasks: list[Any], scope: dict[str, set]) -> None:
    for task in tasks:
        if task.agent_id and task.agent_id not in scope["agents"]:
            raise ValueError(
                f"GraphPatch cannot introduce Agent outside Run snapshot: {task.agent_id}"
            )
        pinned, tools, skills = task_executables(task)
        for reference in pinned:
            if reference.identity not in scope["pinned"]:
                raise ValueError(
                    "GraphPatch cannot introduce Capability outside Run snapshot: "
                    f"{reference.capability_id}@{reference.version}"
                )
        unknown_tools = set(tools) - scope["tools"]
        if unknown_tools:
            raise ValueError(
                f"GraphPatch cannot introduce tools outside Run snapshot: {sorted(unknown_tools)}"
            )
        normalized_skills = {
            item if item.startswith("skill.") else f"skill.{item}" for item in skills
        }
        allowed_skills = {
            item if item.startswith("skill.") else f"skill.{item}" for item in scope["skills"]
        }
        if normalized_skills - allowed_skills:
            raise ValueError(
                "GraphPatch cannot introduce skills outside Run snapshot: "
                f"{sorted(normalized_skills - allowed_skills)}"
            )
