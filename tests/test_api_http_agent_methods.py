import pytest
from fastapi import HTTPException

from joyhousebot.api.http.agent_methods import (
    get_agent_response,
    list_agents_response,
    patch_agent_response,
    resolve_agent_or_503,
)
from joyhousebot.config.schema import AgentEntry, Config


def test_resolve_agent_or_503_success():
    agent = object()
    resolved = resolve_agent_or_503(agent_id="a1", resolve_agent=lambda _id: agent)
    assert resolved is agent


def test_resolve_agent_or_503_failure():
    with pytest.raises(HTTPException) as exc:
        resolve_agent_or_503(agent_id="a1", resolve_agent=lambda _id: None)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Agent not initialized"


def test_get_agent_response():
    cfg = Config()
    payload = get_agent_response(config=cfg)
    assert payload["ok"] is True
    assert "agent" in payload


def test_list_agents_response():
    cfg = Config()
    payload = list_agents_response(config=cfg)
    assert payload["ok"] is True
    assert "agents" in payload


def test_patch_agent_response_success():
    cfg = Config()
    cfg.agents.agent_list = [AgentEntry(id="a1", name="A1", workspace="/w", model="gpt-4", activated=True)]
    payload = patch_agent_response(
        agent_id="a1",
        activated=False,
        get_cached_config=lambda **_: cfg,
        save_config=lambda _: None,
        app_state={},
    )
    assert payload["ok"] is True
    assert payload["agent"]["activated"] is False


def test_patch_agent_response_not_found():
    cfg = Config()
    cfg.agents.agent_list = []
    with pytest.raises(HTTPException) as exc:
        patch_agent_response(
            agent_id="nonexistent",
            activated=True,
            get_cached_config=lambda **_: cfg,
            save_config=lambda _: None,
            app_state={},
        )
    assert exc.value.status_code == 404

