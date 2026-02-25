import pytest

from joyhousebot.api.rpc.ws_rpc_methods import (
    build_rpc_ws_response,
    cleanup_rpc_ws_connection,
    handle_rpc_connect_postprocess,
    run_rpc_ws_loop,
    try_handle_rpc_presence_frame,
)


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_try_handle_rpc_presence_frame_updates_presence():
    upserts = []
    events = []

    async def _emit(event, payload):
        events.append((event, payload))

    handled = await try_handle_rpc_presence_frame(
        frame={"type": "presence", "instanceId": "i1"},
        connection_key="rpc_1",
        presence_upsert=lambda *args, **kwargs: upserts.append((args, kwargs)),
        presence_entries=lambda: [{"id": "x"}],
        normalize_presence_entry=lambda e: {"id": e["id"]},
        emit_event=_emit,
    )
    assert handled is True
    assert upserts and events


def test_build_rpc_ws_response_builds_error_frame():
    response = build_rpc_ws_response(
        frame={"id": "r1"},
        ok=False,
        payload=None,
        error=None,
        rpc_error=_rpc_error,
    )
    assert response["type"] == "res"
    assert response["ok"] is False
    assert response["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_handle_rpc_connect_postprocess_updates_state():
    app_state = {}

    class _Node:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Registry:
        async def register(self, **kwargs):
            app_state["registered"] = kwargs

    client = type("Client", (), {"role": "node", "client_id": "n1"})()
    await handle_rpc_connect_postprocess(
        frame={"method": "connect", "params": {"nodeId": "n1"}},
        ok=True,
        client=client,
        connection_key="rpc_1",
        client_host="127.0.0.1",
        websocket=object(),
        app_state=app_state,
        node_session_cls=_Node,
        node_registry_cls=_Registry,
        now_ms=lambda: 1,
    )
    assert "registered" in app_state
    assert app_state["rpc_connections"]["rpc_1"]["clientId"] == "n1"


@pytest.mark.asyncio
async def test_cleanup_rpc_ws_connection_removes_state():
    removed = []

    class _Registry:
        async def unregister_by_conn(self, _conn):
            return None

    app_state = {"node_registry": _Registry(), "rpc_connections": {"rpc_1": {"x": 1}}}
    await cleanup_rpc_ws_connection(
        connection_key="rpc_1",
        app_state=app_state,
        node_registry_cls=_Registry,
        presence_remove_by_connection=lambda key: removed.append(key),
    )
    assert "rpc_1" not in app_state["rpc_connections"]
    assert removed == ["rpc_1"]


@pytest.mark.asyncio
async def test_run_rpc_ws_loop_processes_presence_and_request():
    sent = []
    logs = []
    post_calls = []
    frames = [
        {"type": "presence", "instanceId": "i1"},
        {"type": "req", "id": "r1", "method": "health"},
    ]

    class _Ws:
        async def receive_json(self):
            if frames:
                return frames.pop(0)
            raise StopAsyncIteration

        async def send_json(self, payload):
            sent.append(payload)

    async def _emit(_event, _payload):
        return None

    async def _handle_rpc_request(_frame, _client, _key, _emit_event, _client_host=None):
        return True, {"ok": True}, None

    async def _postprocess(**kwargs):
        post_calls.append(kwargs)

    with pytest.raises(StopAsyncIteration):
        await run_rpc_ws_loop(
            websocket=_Ws(),
            connection_key="rpc_1",
            client_host=None,
            client=type("Client", (), {"client_id": "c1"})(),
            app_state={},
            emit_event=_emit,
            handle_rpc_request=_handle_rpc_request,
            presence_upsert=lambda *_args, **_kwargs: None,
            presence_entries=lambda: [],
            normalize_presence_entry=lambda x: x,
            rpc_error=_rpc_error,
            logger_info=lambda fmt, method, ok, client_id: logs.append((fmt, method, ok, client_id)),
            handle_connect_postprocess=_postprocess,
            node_session_cls=object,
            node_registry_cls=object,
            now_ms=lambda: 1,
        )
    assert sent and sent[0]["ok"] is True
    assert logs and logs[0][1] == "health"
    assert post_calls

