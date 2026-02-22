"""Shared agent domain operations used by HTTP/RPC/CLI adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from joyhousebot.services.errors import ServiceError


def list_agents(*, config: Any, build_agents_list_payload: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    return build_agents_list_payload(config)


def get_default_agent_http(config: Any) -> dict[str, Any]:
    """Build GET /agent response (default agent info, backward compat)."""
    entry = config._resolve_default_entry()
    return {
        "ok": True,
        "agent": {
            "id": config.get_default_agent_id(),
            "model": entry.model,
            "model_fallbacks": list(getattr(entry, "model_fallbacks", []) or []),
            "temperature": getattr(entry, "temperature", config.agents.defaults.temperature),
            "max_tokens": getattr(entry, "max_tokens", config.agents.defaults.max_tokens),
            "max_tool_iterations": getattr(entry, "max_tool_iterations", config.agents.defaults.max_tool_iterations),
            "memory_window": getattr(entry, "memory_window", config.agents.defaults.memory_window),
            "workspace": entry.workspace,
            "provider_name": config.get_provider_name(entry.model),
        },
    }


def list_agents_http(config: Any) -> dict[str, Any]:
    """Build GET /agents response (all agents for API)."""
    return {"ok": True, "agents": config.get_agent_list_for_api()}


def patch_agent_activated_http(
    *,
    config: Any,
    agent_id: str,
    activated: bool | None,
    save_config: Callable[[Any], None],
    app_state: dict[str, Any],
) -> dict[str, Any]:
    """Update one agent's activated flag and return updated agent dict for PATCH /agents/{id}."""
    if not config.agents.agent_list:
        raise ServiceError(code="NOT_FOUND", message="No agent list in config")
    for e in config.agents.agent_list:
        if e.id == agent_id:
            if activated is not None:
                e.activated = activated
            save_config(config)
            if "config" in app_state:
                app_state["config"] = config
            agents = config.get_agent_list_for_api()
            updated = next((a for a in agents if a["id"] == agent_id), None)
            return {"ok": True, "agent": updated}
    raise ServiceError(code="NOT_FOUND", message=f"Agent {agent_id!r} not found")


def create_agent(
    *,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    normalize_agent_id: Callable[[str], str],
    ensure_agent_workspace_bootstrap: Callable[[Path, str], None],
    save_config: Callable[[Any], None],
    get_cached_config: Callable[..., Any],
) -> dict[str, Any]:
    from joyhousebot.config.schema import AgentEntry

    raw_name = str(params.get("name") or "").strip()
    raw_id = str(params.get("id") or "").strip()
    agent_id = normalize_agent_id(raw_id or raw_name)
    if not raw_name:
        raw_name = agent_id
    if agent_id == "default":
        raise ServiceError(code="INVALID_REQUEST", message='"default" is reserved')
    if config.get_agent_entry(agent_id) is not None:
        raise ServiceError(code="INVALID_REQUEST", message="agent already exists")

    workspace_str = str(params.get("workspace") or f"~/.joyhousebot/agents/{agent_id}")
    workspace = Path(workspace_str).expanduser().resolve()
    ensure_agent_workspace_bootstrap(workspace, raw_name)
    model_fallbacks = params.get("modelFallbacks", params.get("model_fallbacks", []))
    if not isinstance(model_fallbacks, list):
        model_fallbacks = []

    entry = AgentEntry(
        id=agent_id,
        name=raw_name,
        workspace=str(workspace),
        model=str(params.get("model") or config.agents.defaults.model),
        model_fallbacks=[str(x).strip() for x in model_fallbacks if str(x).strip()],
        provider=str(params.get("provider") or ""),
        max_tokens=int(params.get("maxTokens") or params.get("max_tokens") or config.agents.defaults.max_tokens),
        temperature=float(params.get("temperature") or config.agents.defaults.temperature),
        max_tool_iterations=int(
            params.get("maxToolIterations")
            or params.get("max_tool_iterations")
            or config.agents.defaults.max_tool_iterations
        ),
        memory_window=int(params.get("memoryWindow") or params.get("memory_window") or config.agents.defaults.memory_window),
        activated=bool(params.get("activated", True)),
    )
    config.agents.agent_list.append(entry)
    save_config(config)
    app_state["config"] = get_cached_config(force_reload=True)
    return {"ok": True, "agentId": entry.id, "name": entry.name, "workspace": str(workspace)}


def update_agent(
    *,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    normalize_agent_id: Callable[[str], str],
    ensure_agent_workspace_bootstrap: Callable[[Path, str], None],
    save_config: Callable[[Any], None],
    get_cached_config: Callable[..., Any],
) -> dict[str, Any]:
    agent_id = normalize_agent_id(str(params.get("agentId") or params.get("id") or "").strip())
    if not agent_id:
        raise ServiceError(code="INVALID_REQUEST", message="agents.update requires agentId")

    target = None
    for entry in config.agents.agent_list:
        if entry.id == agent_id:
            target = entry
            break
    if target is None:
        raise ServiceError(code="INVALID_REQUEST", message="unknown agent id")

    if "name" in params:
        target.name = str(params.get("name") or "")
    if "workspace" in params:
        target.workspace = str(Path(str(params.get("workspace") or target.workspace)).expanduser().resolve())
        ensure_agent_workspace_bootstrap(Path(target.workspace), target.name or target.id)
    if "model" in params:
        target.model = str(params.get("model") or target.model)
    if "modelFallbacks" in params or "model_fallbacks" in params:
        raw_fallbacks = params.get("modelFallbacks", params.get("model_fallbacks", []))
        if isinstance(raw_fallbacks, list):
            target.model_fallbacks = [str(x).strip() for x in raw_fallbacks if str(x).strip()]
    if "provider" in params:
        target.provider = str(params.get("provider") or "")
    if "activated" in params:
        target.activated = bool(params.get("activated"))

    avatar = str(params.get("avatar") or "").strip()
    if avatar:
        identity_path = Path(target.workspace).expanduser() / "IDENTITY.md"
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        with open(identity_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n- Avatar: {avatar}\n")

    save_config(config)
    app_state["config"] = get_cached_config(force_reload=True)
    return {"ok": True, "agentId": target.id, "name": target.name, "workspace": target.workspace}


def delete_agent(
    *,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    normalize_agent_id: Callable[[str], str],
    save_config: Callable[[Any], None],
    get_cached_config: Callable[..., Any],
) -> dict[str, Any]:
    agent_id = normalize_agent_id(str(params.get("agentId") or params.get("id") or "").strip())
    if not agent_id:
        raise ServiceError(code="INVALID_REQUEST", message="agents.delete requires agentId")
    if agent_id == "default":
        raise ServiceError(code="INVALID_REQUEST", message='"default" cannot be deleted')
    if config.agents.default_id == agent_id:
        raise ServiceError(code="INVALID_REQUEST", message="default agent cannot be deleted")

    before = len(config.agents.agent_list)
    config.agents.agent_list = [entry for entry in config.agents.agent_list if entry.id != agent_id]
    if len(config.agents.agent_list) == before:
        raise ServiceError(code="INVALID_REQUEST", message="unknown agent id")

    save_config(config)
    app_state["config"] = get_cached_config(force_reload=True)
    return {"ok": True, "agentId": agent_id, "removedBindings": 1}


def list_agent_files(*, params: dict[str, Any], config: Any) -> dict[str, Any]:
    agent_id = str(params.get("agentId") or params.get("id") or config.get_default_agent_id())
    entry = config.get_agent_entry(agent_id)
    if entry is None:
        raise ServiceError(code="INVALID_REQUEST", message="unknown agent id")

    root = Path(str(getattr(entry, "workspace", "~/.joyhousebot/workspace"))).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for file_path in root.rglob("*"):
        if file_path.is_file():
            rel = file_path.relative_to(root).as_posix()
            files.append(
                {
                    "name": rel,
                    "path": str(file_path),
                    "size": file_path.stat().st_size,
                    "updatedAtMs": int(file_path.stat().st_mtime * 1000),
                    "mtimeMs": int(file_path.stat().st_mtime * 1000),
                }
            )
    files.sort(key=lambda row: row["path"])
    return {"agentId": agent_id, "workspace": str(root), "files": files}


def get_agent_file(*, params: dict[str, Any], config: Any) -> dict[str, Any]:
    agent_id = str(params.get("agentId") or params.get("id") or config.get_default_agent_id())
    rel_path = str(params.get("path") or "").strip()
    if not rel_path:
        raise ServiceError(code="INVALID_REQUEST", message="path required")
    entry = config.get_agent_entry(agent_id)
    if entry is None:
        raise ServiceError(code="INVALID_REQUEST", message="unknown agent id")

    root = Path(str(getattr(entry, "workspace", "~/.joyhousebot/workspace"))).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ServiceError(code="INVALID_REQUEST", message="path escapes workspace")
    if not target.exists() or not target.is_file():
        return {
            "agentId": agent_id,
            "workspace": str(root),
            "file": {"name": rel_path, "path": str(target), "missing": True},
        }

    content = target.read_text(encoding="utf-8")
    return {
        "agentId": agent_id,
        "workspace": str(root),
        "file": {
            "name": rel_path,
            "path": str(target),
            "missing": False,
            "size": target.stat().st_size,
            "updatedAtMs": int(target.stat().st_mtime * 1000),
            "content": content,
        },
        "path": rel_path,
        "content": content,
    }


def set_agent_file(*, params: dict[str, Any], config: Any) -> dict[str, Any]:
    agent_id = str(params.get("agentId") or params.get("id") or config.get_default_agent_id())
    rel_path = str(params.get("path") or "").strip()
    if not rel_path:
        raise ServiceError(code="INVALID_REQUEST", message="path required")
    content = str(params.get("content") or "")
    entry = config.get_agent_entry(agent_id)
    if entry is None:
        raise ServiceError(code="INVALID_REQUEST", message="unknown agent id")

    root = Path(str(getattr(entry, "workspace", "~/.joyhousebot/workspace"))).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ServiceError(code="INVALID_REQUEST", message="path escapes workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "agentId": agent_id,
        "workspace": str(root),
        "file": {
            "name": rel_path,
            "path": str(target),
            "missing": False,
            "size": target.stat().st_size,
            "updatedAtMs": int(target.stat().st_mtime * 1000),
            "content": content,
        },
        "path": rel_path,
    }


def get_agent_identity(*, params: dict[str, Any], config: Any) -> dict[str, Any]:
    session_key = str(params.get("sessionKey") or "").strip()
    aid = config.get_default_agent_id()
    name = aid
    entry = config.get_agent_entry(aid)
    if entry is not None:
        name = getattr(entry, "name", "") or getattr(entry, "id", "") or aid
    return {"agentId": aid, "name": name, "avatar": "bot", "sessionKey": session_key}

