from pathlib import Path

import pytest

from joyhousebot.api.rpc.agents_methods import try_handle_agents_method
from joyhousebot.config.schema import Config, AgentEntry


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


def _build_agents_list_payload(config: Config):
    return {"agents": [e.id for e in config.agents.agent_list]}


def _normalize_agent_id(value: str):
    return value.strip().lower().replace(" ", "-")


def _ensure_agent_workspace_bootstrap(workspace: Path, agent_name: str):
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "IDENTITY.md").write_text(f"# {agent_name}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_agents_list_create_delete_flow(tmp_path: Path):
    cfg = Config()
    cfg.agents.agent_list = []
    app_state: dict = {}

    list_res = await try_handle_agents_method(
        method="agents.list",
        params={},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        build_agents_list_payload=_build_agents_list_payload,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        now_ms=lambda: 1,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert list_res == (True, {"agents": []}, None)

    create_res = await try_handle_agents_method(
        method="agents.create",
        params={"id": "AgentA", "name": "Agent A", "workspace": str(tmp_path / "agenta")},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        build_agents_list_payload=_build_agents_list_payload,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        now_ms=lambda: 1,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert create_res is not None and create_res[0] is True
    assert cfg.get_agent_entry("agenta") is not None

    delete_res = await try_handle_agents_method(
        method="agents.delete",
        params={"agentId": "agenta"},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        build_agents_list_payload=_build_agents_list_payload,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        now_ms=lambda: 1,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert delete_res == (True, {"ok": True, "agentId": "agenta", "removedBindings": 1}, None)


@pytest.mark.asyncio
async def test_agents_files_get_set(tmp_path: Path):
    cfg = Config()
    cfg.agents.agent_list = []
    cfg.agents.default_id = None
    cfg.agents.agent_list.append(
        AgentEntry(
            id="main",
            name="main",
            workspace=str(tmp_path / "main"),
            model=cfg.agents.defaults.model,
            model_fallbacks=[],
            provider="",
            max_tokens=cfg.agents.defaults.max_tokens,
            temperature=cfg.agents.defaults.temperature,
            max_tool_iterations=cfg.agents.defaults.max_tool_iterations,
            memory_window=cfg.agents.defaults.memory_window,
            default=True,
            activated=True,
        )
    )
    app_state: dict = {}

    set_res = await try_handle_agents_method(
        method="agents.files.set",
        params={"agentId": "main", "path": "a/b.txt", "content": "hello"},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        build_agents_list_payload=_build_agents_list_payload,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        now_ms=lambda: 2,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert set_res is not None and set_res[0] is True

    get_res = await try_handle_agents_method(
        method="agents.files.get",
        params={"agentId": "main", "path": "a/b.txt"},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        build_agents_list_payload=_build_agents_list_payload,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        now_ms=lambda: 2,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert get_res is not None and get_res[0] is True
    assert get_res[1]["content"] == "hello"

