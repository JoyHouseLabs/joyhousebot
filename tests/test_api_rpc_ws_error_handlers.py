import pytest

from joyhousebot.api.rpc.ws_error_handlers import handle_chat_ws_close, handle_rpc_ws_close


@pytest.mark.asyncio
async def test_handle_rpc_ws_close_without_error(monkeypatch):
    called = {"cleanup": 0}

    async def _cleanup(**_kwargs):
        called["cleanup"] += 1

    monkeypatch.setattr(
        "joyhousebot.api.rpc.ws_rpc_methods.cleanup_rpc_ws_connection",
        _cleanup,
    )

    await handle_rpc_ws_close(
        connection_key="k1",
        app_state={},
        node_registry_cls=object,
        presence_remove_by_connection=lambda _k: None,
    )

    assert called["cleanup"] == 1


@pytest.mark.asyncio
async def test_handle_rpc_ws_close_with_error_logs(monkeypatch):
    called = {"cleanup": 0, "log": 0}

    async def _cleanup(**_kwargs):
        called["cleanup"] += 1

    def _log(_fmt, _exc):
        called["log"] += 1

    monkeypatch.setattr(
        "joyhousebot.api.rpc.ws_rpc_methods.cleanup_rpc_ws_connection",
        _cleanup,
    )

    await handle_rpc_ws_close(
        connection_key="k1",
        app_state={},
        node_registry_cls=object,
        presence_remove_by_connection=lambda _k: None,
        logger_error=_log,
        exc=RuntimeError("x"),
    )

    assert called["cleanup"] == 1
    assert called["log"] == 1


def test_handle_chat_ws_close_without_error(monkeypatch):
    called = {"cleanup": 0}

    def _cleanup(**_kwargs):
        called["cleanup"] += 1

    monkeypatch.setattr(
        "joyhousebot.api.rpc.ws_chat_methods.cleanup_chat_presence_and_connection",
        _cleanup,
    )

    handle_chat_ws_close(
        websocket=object(),
        ws_to_presence_key={},
        presence_remove_by_connection=lambda _k: None,
        manager_disconnect=lambda _ws: None,
    )

    assert called["cleanup"] == 1


def test_handle_chat_ws_close_with_error_logs(monkeypatch):
    called = {"cleanup": 0, "log": 0}

    def _cleanup(**_kwargs):
        called["cleanup"] += 1

    def _log(_msg):
        called["log"] += 1

    monkeypatch.setattr(
        "joyhousebot.api.rpc.ws_chat_methods.cleanup_chat_presence_and_connection",
        _cleanup,
    )

    handle_chat_ws_close(
        websocket=object(),
        ws_to_presence_key={},
        presence_remove_by_connection=lambda _k: None,
        manager_disconnect=lambda _ws: None,
        logger_error=_log,
        exc=RuntimeError("x"),
    )

    assert called["cleanup"] == 1
    assert called["log"] == 1

