import pytest

from joyhousebot.api.rpc import pipeline_handlers


@pytest.mark.asyncio
async def test_handle_agents_with_shadow(monkeypatch):
    calls = []

    async def _agents(**_kwargs):
        return True, {"agents": []}, None

    async def _shadow(**kwargs):
        calls.append(kwargs)
        return kwargs["result"]

    monkeypatch.setattr(pipeline_handlers, "try_handle_agents_method", _agents)
    monkeypatch.setattr(pipeline_handlers, "apply_shadow_hook_if_needed", _shadow)

    res = await pipeline_handlers.handle_agents_with_shadow(
        method="agents.list",
        params={},
        config=object(),
        app_state={},
        rpc_error=lambda *_: {},
        build_agents_list_payload=lambda *_: {},
        normalize_agent_id=lambda x: str(x),
        ensure_agent_workspace_bootstrap=lambda *_: None,
        now_ms=lambda: 1,
        save_config=lambda *_: None,
        get_cached_config=lambda *_: object(),
        run_rpc_shadow=lambda *_: _noop(),
    )
    assert res == (True, {"agents": []}, None)
    assert calls and calls[0]["shadow_methods"] == {"agents.list"}


@pytest.mark.asyncio
async def test_handle_sessions_usage_with_shadow_none(monkeypatch):
    async def _sessions(**_kwargs):
        return None

    monkeypatch.setattr(pipeline_handlers, "try_handle_sessions_usage_method", _sessions)

    res = await pipeline_handlers.handle_sessions_usage_with_shadow(
        method="sessions.list",
        params={},
        config=object(),
        rpc_error=lambda *_: {},
        resolve_agent=lambda *_: None,
        build_sessions_list_payload=lambda *_: {},
        build_chat_history_payload=lambda *_: {},
        apply_session_patch=lambda *_: (True, None),
        now_ms=lambda: 1,
        delete_session=lambda *_: None,
        empty_usage_totals=lambda: {},
        session_usage_entry=lambda *_: {},
        estimate_tokens=lambda *_: 0,
        run_rpc_shadow=lambda *_: _noop(),
    )
    assert res is None


@pytest.mark.asyncio
async def test_handle_config_with_shadow(monkeypatch):
    async def _config(**_kwargs):
        return True, {"config": {}}, None

    async def _shadow(**kwargs):
        return kwargs["result"]

    monkeypatch.setattr(pipeline_handlers, "try_handle_config_method", _config)
    monkeypatch.setattr(pipeline_handlers, "apply_shadow_hook_if_needed", _shadow)

    res = await pipeline_handlers.handle_config_with_shadow(
        method="config.get",
        params={},
        config=object(),
        rpc_error=lambda *_: {},
        build_config_snapshot=lambda *_: {},
        build_config_schema_payload=lambda: {},
        apply_config_from_raw=lambda *_: None,
        get_cached_config=lambda *_: object(),
        update_config=lambda *_: object(),
        config_update_cls=dict,
        run_rpc_shadow=lambda *_: _noop(),
    )
    assert res == (True, {"config": {}}, None)


async def _noop():
    return None

