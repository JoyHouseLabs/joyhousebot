"""Sandbox domain service: list/recreate/explain using Docker backend and registry."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any, Callable

from joyhousebot.config.loader import load_config
from joyhousebot.sandbox.docker_backend import (
    is_docker_available,
    list_containers as docker_list_containers,
    remove_container as docker_remove_container,
)
from joyhousebot.sandbox.registry import read_registry, update_registry_after_remove, write_registry


def _run_async(coro: Any) -> Any:
    """Run async coroutine from sync context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def list_containers_local(
    read_json: Callable[[str, Any], Any],
    browser_only: bool = False,
) -> list[dict[str, Any]]:
    """List sandbox containers: from Docker if available, else from registry."""
    docker_available = _run_async(is_docker_available())
    if docker_available:
        items = _run_async(docker_list_containers(browser_only=browser_only))
        if items:
            return items
    payload = read_registry(read_json)
    containers = payload.get("containers") or []
    out = [c for c in containers if isinstance(c, dict)]
    if browser_only:
        out = [c for c in out if c.get("browser")]
    return out


def recreate_containers_local(
    read_json: Callable[[str, Any], Any],
    write_json: Callable[[str, Any], None],
    all_items: bool,
    session: str | None,
    agent: str | None,
    browser_only: bool,
    force: bool,
) -> dict[str, Any]:
    """Remove sandbox containers (Docker rm -f) and update registry."""
    docker_available = _run_async(is_docker_available())
    removed: list[str] = []
    removed_ids: set[str] = set()
    if docker_available:
        items = _run_async(docker_list_containers(browser_only=browser_only))
        for item in items:
            cid_full = item.get("idFull") or item.get("id") or ""
            if not cid_full:
                continue
            ok, err = _run_async(docker_remove_container(cid_full))
            if ok:
                removed.append(cid_full[:12] if len(cid_full) > 12 else cid_full)
                removed_ids.add(cid_full)
                if len(cid_full) > 12:
                    removed_ids.add(cid_full[:12])
        if removed_ids:
            update_registry_after_remove(read_json, write_json, removed_ids)
    op = {
        "requestedAtMs": int(time.time() * 1000),
        "all": bool(all_items),
        "session": (session or "").strip() or None,
        "agent": (agent or "").strip() or None,
        "browserOnly": bool(browser_only),
        "force": bool(force),
        "removed": removed,
        "dockerAvailable": docker_available,
    }
    payload = read_registry(read_json)
    ops = list(payload.get("recreateOps") or [])
    ops.append(op)
    payload["recreateOps"] = ops[-100:]
    write_json("sandbox_runtime", payload)
    return {"ok": True, "operation": op, "removed": removed}


def explain_local(
    read_json: Callable[[str, Any], Any],
    session: str,
    agent: str,
) -> dict[str, Any]:
    """Build explain payload: policy + docker availability + backend."""
    cfg = load_config()
    docker_available = _run_async(is_docker_available())
    containers = list_containers_local(read_json, browser_only=False)
    return {
        "session": (session or "").strip() or "agent:main:main",
        "agent": (agent or "").strip() or cfg.get_default_agent_id(),
        "policy": {
            "restrict_to_workspace": bool(cfg.tools.restrict_to_workspace),
            "exec_timeout": int(cfg.tools.exec.timeout),
            "exec_shell_mode": bool(cfg.tools.exec.shell_mode),
            "container_enabled": getattr(cfg.tools.exec, "container_enabled", False),
            "container_image": getattr(cfg.tools.exec, "container_image", "alpine:3.18"),
        },
        "custom_policy": read_json("sandbox_policy", {}),
        "docker_available": docker_available,
        "backend": "docker" if docker_available else "direct",
        "containers_count": len(containers),
    }
