from pathlib import Path

import pytest

from joyhousebot.config.schema import AgentEntry, Config
from joyhousebot.services.agents.agent_service import (
    create_agent,
    delete_agent,
    get_agent_file,
    get_default_agent_http,
    list_agent_files,
    list_agents_http,
    patch_agent_activated_http,
    set_agent_file,
    update_agent,
)
from joyhousebot.services.errors import ServiceError


def _normalize_agent_id(value: str):
    return value.strip().lower().replace(" ", "-")


def _ensure_agent_workspace_bootstrap(workspace: Path, agent_name: str):
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "IDENTITY.md").write_text(f"# {agent_name}\n", encoding="utf-8")


def test_create_update_delete_agent(tmp_path: Path):
    cfg = Config()
    cfg.agents.agent_list = []
    app_state: dict = {}

    created = create_agent(
        params={"id": "AgentA", "name": "Agent A", "workspace": str(tmp_path / "agenta")},
        config=cfg,
        app_state=app_state,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert created["agentId"] == "agenta"

    updated = update_agent(
        params={"agentId": "agenta", "name": "New Name"},
        config=cfg,
        app_state=app_state,
        normalize_agent_id=_normalize_agent_id,
        ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert updated["name"] == "New Name"

    deleted = delete_agent(
        params={"agentId": "agenta"},
        config=cfg,
        app_state=app_state,
        normalize_agent_id=_normalize_agent_id,
        save_config=lambda _cfg: None,
        get_cached_config=lambda **_: cfg,
    )
    assert deleted["removedBindings"] == 1


def test_agent_file_operations(tmp_path: Path):
    cfg = Config()
    cfg.agents.agent_list = [
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
    ]
    cfg.agents.default_id = "main"

    set_payload = set_agent_file(params={"agentId": "main", "path": "a/b.txt", "content": "hello"}, config=cfg)
    assert set_payload["ok"] is True
    got = get_agent_file(params={"agentId": "main", "path": "a/b.txt"}, config=cfg)
    assert got["content"] == "hello"
    listed = list_agent_files(params={"agentId": "main"}, config=cfg)
    assert any(x["name"] == "a/b.txt" for x in listed["files"])


def test_create_agent_rejects_default_reserved(tmp_path: Path):
    cfg = Config()
    cfg.agents.agent_list = []
    with pytest.raises(ServiceError):
        create_agent(
            params={"id": "default", "name": "default", "workspace": str(tmp_path / "default")},
            config=cfg,
            app_state={},
            normalize_agent_id=_normalize_agent_id,
            ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
            save_config=lambda _cfg: None,
            get_cached_config=lambda **_: cfg,
        )


def test_get_default_agent_http_and_list_agents_http():
    cfg = Config()
    payload = get_default_agent_http(cfg)
    assert payload["ok"] is True
    assert "agent" in payload
    assert "id" in payload["agent"]
    assert "model" in payload["agent"]

    list_payload = list_agents_http(cfg)
    assert list_payload["ok"] is True
    assert "agents" in list_payload
    assert isinstance(list_payload["agents"], list)


def test_patch_agent_activated_http():
    cfg = Config()
    cfg.agents.agent_list = [AgentEntry(id="a1", name="A1", workspace="/w", model="gpt-4", activated=True)]
    app_state: dict = {}
    result = patch_agent_activated_http(
        config=cfg,
        agent_id="a1",
        activated=False,
        save_config=lambda _: None,
        app_state=app_state,
    )
    assert result["ok"] is True
    assert result["agent"]["id"] == "a1"
    assert result["agent"]["activated"] is False

    with pytest.raises(ServiceError, match="not found"):
        patch_agent_activated_http(
            config=cfg,
            agent_id="nonexistent",
            activated=True,
            save_config=lambda _: None,
            app_state=app_state,
        )

