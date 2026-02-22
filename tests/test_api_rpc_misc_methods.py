import pytest

from joyhousebot.api.rpc.misc_methods import try_handle_misc_method
from joyhousebot.config.schema import Config


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _FakeStore:
    db_path = "/tmp/fake.db"

    def tail_task_events(self, cursor=None, limit=200):
        return {"cursor": cursor, "limit": limit, "items": []}


@pytest.mark.asyncio
async def test_actions_and_models_handlers():
    cfg = Config()
    result = await try_handle_misc_method(
        method="models.list",
        params={},
        config=cfg,
        app_state={},
        rpc_error=_rpc_error,
        get_models_payload=lambda _cfg: [{"id": "m1"}],
        build_auth_profiles_report=lambda _cfg: {"ok": True},
        build_actions_catalog=lambda: {"actions": ["a1"]},
        validate_action_candidate=lambda **_: {"ok": False, "reason": "bad"},
        validate_action_batch=lambda _items: {"ok": True},
        control_overview=lambda: _dummy_overview(),
        now_ms=lambda: 100,
        get_alerts_lifecycle_view=lambda: {"ok": True},
        presence_entries=lambda: [],
        normalize_presence_entry=lambda x: x,
        get_store=lambda: _FakeStore(),
        load_persistent_state=lambda _k, d: d,
        run_update_install=_dummy_run_update,
        create_task=lambda c: c,
    )
    assert result == (True, {"models": [{"id": "m1"}]}, None)

    bad_action = await try_handle_misc_method(
        method="actions.validate",
        params={"code": "x"},
        config=cfg,
        app_state={},
        rpc_error=_rpc_error,
        get_models_payload=lambda _cfg: [],
        build_auth_profiles_report=lambda _cfg: {"ok": True},
        build_actions_catalog=lambda: {"actions": []},
        validate_action_candidate=lambda **_: {"ok": False, "reason": "bad"},
        validate_action_batch=lambda _items: {"ok": True},
        control_overview=lambda: _dummy_overview(),
        now_ms=lambda: 100,
        get_alerts_lifecycle_view=lambda: {"ok": True},
        presence_entries=lambda: [],
        normalize_presence_entry=lambda x: x,
        get_store=lambda: _FakeStore(),
        load_persistent_state=lambda _k, d: d,
        run_update_install=_dummy_run_update,
        create_task=lambda c: c,
    )
    assert bad_action is not None and bad_action[0] is False
    assert bad_action[2]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_logs_tail_and_update_run():
    cfg = Config()
    app_state = {"rpc_update_status": {"running": False, "ok": None}}
    created = {"called": False}

    def _create_task(_coro):
        created["called"] = True
        close = getattr(_coro, "close", None)
        if callable(close):
            close()
        return None

    logs = await try_handle_misc_method(
        method="logs.tail",
        params={"cursor": "12", "limit": 10},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        get_models_payload=lambda _cfg: [],
        build_auth_profiles_report=lambda _cfg: {},
        build_actions_catalog=lambda: {},
        validate_action_candidate=lambda **_: {"ok": True},
        validate_action_batch=lambda _items: {"ok": True},
        control_overview=lambda: _dummy_overview(),
        now_ms=lambda: 100,
        get_alerts_lifecycle_view=lambda: {},
        presence_entries=lambda: [],
        normalize_presence_entry=lambda x: x,
        get_store=lambda: _FakeStore(),
        load_persistent_state=lambda _k, d: d,
        run_update_install=_dummy_run_update,
        create_task=_create_task,
    )
    assert logs == (True, {"file": "/tmp/fake.db", "cursor": 12, "limit": 10, "items": []}, None)

    update = await try_handle_misc_method(
        method="update.run",
        params={},
        config=cfg,
        app_state=app_state,
        rpc_error=_rpc_error,
        get_models_payload=lambda _cfg: [],
        build_auth_profiles_report=lambda _cfg: {},
        build_actions_catalog=lambda: {},
        validate_action_candidate=lambda **_: {"ok": True},
        validate_action_batch=lambda _items: {"ok": True},
        control_overview=lambda: _dummy_overview(),
        now_ms=lambda: 100,
        get_alerts_lifecycle_view=lambda: {},
        presence_entries=lambda: [],
        normalize_presence_entry=lambda x: x,
        get_store=lambda: _FakeStore(),
        load_persistent_state=lambda _k, d: d,
        run_update_install=_dummy_run_update,
        create_task=_create_task,
    )
    assert update is not None and update[0] is True
    assert created["called"] is True


async def _dummy_overview():
    return {"alertsSummary": {}, "alertsLifecycle": {}}


async def _dummy_run_update():
    return None

