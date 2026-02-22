import pytest

from joyhousebot.api.rpc.control_state import try_handle_control_state_method
from joyhousebot.config.schema import Config


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_skills_update_and_status():
    cfg = Config()
    state: dict = {}
    saved = {"config": None, "persistent": []}

    def _get_cached_config(*, force_reload: bool = False):
        assert force_reload is True
        return cfg

    res = await try_handle_control_state_method(
        method="skills.update",
        params={"skillKey": "demo.skill", "enabled": False},
        config=cfg,
        app_state=state,
        emit_event=None,
        rpc_error=_rpc_error,
        load_persistent_state=lambda *_: {},
        save_persistent_state=lambda key, value: saved["persistent"].append((key, value)),
        now_ms=lambda: 100,
        build_skills_status_report=lambda c: {"ok": True, "n": len((c.skills.entries or {}).keys())},
        build_channels_status_snapshot=lambda *_: {"ok": True},
        get_cached_config=_get_cached_config,
        save_config=lambda c: saved.__setitem__("config", c),
    )
    assert res == (True, {"ok": True, "skillKey": "demo.skill"}, None)
    assert "demo.skill" in cfg.skills.entries
    assert state.get("config") is cfg
    assert saved["config"] is cfg


@pytest.mark.asyncio
async def test_voicewake_set_emits_event():
    cfg = Config()
    events = []
    store = {"rpc.voicewake": {"enabled": False, "keyword": "hey joyhouse"}}

    async def _emit(event: str, payload):
        events.append((event, payload))

    def _load(name, default):
        return dict(store.get(name, default))

    def _save(name, value):
        store[name] = dict(value)

    res = await try_handle_control_state_method(
        method="voicewake.set",
        params={"enabled": True, "keyword": "hello bot"},
        config=cfg,
        app_state={},
        emit_event=_emit,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        now_ms=lambda: 100,
        build_skills_status_report=lambda *_: {},
        build_channels_status_snapshot=lambda *_: {},
        get_cached_config=lambda **_: cfg,
        save_config=lambda *_: None,
    )
    assert res is not None
    ok, payload, err = res
    assert ok is True and err is None
    assert payload["enabled"] is True
    assert events and events[0][0] == "voicewake"


@pytest.mark.asyncio
async def test_tts_convert_validation_and_channels_status():
    cfg = Config()
    res_err = await try_handle_control_state_method(
        method="tts.convert",
        params={"text": ""},
        config=cfg,
        app_state={"channel_manager": object()},
        emit_event=None,
        rpc_error=_rpc_error,
        load_persistent_state=lambda *_: {},
        save_persistent_state=lambda *_: None,
        now_ms=lambda: 1,
        build_skills_status_report=lambda *_: {},
        build_channels_status_snapshot=lambda c, m: {"ok": True, "channel_manager": m is not None},
        get_cached_config=lambda **_: cfg,
        save_config=lambda *_: None,
    )
    assert res_err is not None and res_err[0] is False
    assert res_err[2]["code"] == "INVALID_REQUEST"

    res_status = await try_handle_control_state_method(
        method="channels.status",
        params={},
        config=cfg,
        app_state={"channel_manager": object()},
        emit_event=None,
        rpc_error=_rpc_error,
        load_persistent_state=lambda *_: {},
        save_persistent_state=lambda *_: None,
        now_ms=lambda: 1,
        build_skills_status_report=lambda *_: {},
        build_channels_status_snapshot=lambda c, m: {"ok": True, "channel_manager": m is not None},
        get_cached_config=lambda **_: cfg,
        save_config=lambda *_: None,
    )
    assert res_status == (True, {"ok": True, "channel_manager": True}, None)

