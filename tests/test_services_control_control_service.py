import pytest

from joyhousebot.config.schema import Config
from joyhousebot.services.control.control_service import try_handle_control_state


@pytest.mark.asyncio
async def test_try_handle_control_state_skills_and_voicewake():
    cfg = Config()
    store = {"rpc.voicewake": {"enabled": False, "keyword": "hey joyhouse"}}
    app_state: dict = {}
    events = []

    def _load(name, default):
        return dict(store.get(name, default))

    def _save(name, value):
        store[name] = dict(value)

    async def _emit(event, payload):
        events.append((event, payload))

    payload = await try_handle_control_state(
        method="skills.update",
        params={"skillKey": "demo.skill", "enabled": True},
        config=cfg,
        app_state=app_state,
        emit_event=None,
        load_persistent_state=_load,
        save_persistent_state=_save,
        now_ms=lambda: 1,
        build_skills_status_report=lambda c: {"n": len((c.skills.entries or {}).keys())},
        build_channels_status_snapshot=lambda *_: {},
        get_cached_config=lambda **_: cfg,
        save_config=lambda *_: None,
    )
    assert payload == {"ok": True, "skillKey": "demo.skill"}

    wake = await try_handle_control_state(
        method="voicewake.set",
        params={"enabled": True, "keyword": "hello"},
        config=cfg,
        app_state=app_state,
        emit_event=_emit,
        load_persistent_state=_load,
        save_persistent_state=_save,
        now_ms=lambda: 1,
        build_skills_status_report=lambda *_: {},
        build_channels_status_snapshot=lambda *_: {},
        get_cached_config=lambda **_: cfg,
        save_config=lambda *_: None,
    )
    assert wake is not None and wake["enabled"] is True
    assert events and events[0][0] == "voicewake"

