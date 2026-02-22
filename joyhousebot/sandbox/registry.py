"""Sandbox container registry: persist container id / session / agent mapping."""

from __future__ import annotations

from typing import Any, Callable


def read_registry(read_json: Callable[[str, Any], Any], key: str = "sandbox_runtime") -> dict[str, Any]:
    """Read registry from state; default key sandbox_runtime -> {containers: [], recreateOps: []}."""
    data = read_json(key, {"containers": [], "recreateOps": []})
    if not isinstance(data, dict):
        return {"containers": [], "recreateOps": []}
    containers = data.get("containers")
    if not isinstance(containers, list):
        containers = []
    recreate_ops = data.get("recreateOps")
    if not isinstance(recreate_ops, list):
        recreate_ops = []
    return {"containers": containers, "recreateOps": recreate_ops}


def write_registry(
    read_json: Callable[[str, Any], Any],
    write_json: Callable[[str, Any], None],
    containers: list[dict[str, Any]],
    recreate_ops: list[dict[str, Any]] | None = None,
    key: str = "sandbox_runtime",
) -> None:
    """Write registry to state; preserve recreateOps if recreate_ops is None."""
    current = read_registry(read_json, key)
    current["containers"] = containers
    if recreate_ops is not None:
        current["recreateOps"] = recreate_ops[-100:]
    write_json(key, current)


def update_registry_after_remove(
    read_json: Callable[[str, Any], Any],
    write_json: Callable[[str, Any], None],
    removed_ids: set[str],
    key: str = "sandbox_runtime",
) -> None:
    """Remove entries with id in removed_ids from registry containers list."""
    data = read_registry(read_json, key)
    containers = [c for c in data["containers"] if isinstance(c, dict) and (c.get("id") or c.get("idFull")) not in removed_ids]
    data["containers"] = containers
    write_json(key, data)
