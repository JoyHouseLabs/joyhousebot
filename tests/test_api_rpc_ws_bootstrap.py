import pytest

from joyhousebot.api.rpc.ws_bootstrap import bootstrap_chat_ws_connection, bootstrap_rpc_ws_connection


class _Ws:
    def __init__(self):
        self.client = type("Client", (), {"host": "127.0.0.1"})()
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_bootstrap_rpc_ws_connection_initializes_state():
    ws = _Ws()
    app_state = {}
    upserts = []

    class _ClientState:
        def __init__(self):
            self.client_id = ""

    connection_key, client_host, client, emit_event = await bootstrap_rpc_ws_connection(
        websocket=ws,
        app_state=app_state,
        presence_upsert=lambda *args, **kwargs: upserts.append((args, kwargs)),
        client_state_cls=_ClientState,
    )
    assert ws.accepted is True
    assert connection_key.startswith("rpc_")
    assert client_host == "127.0.0.1"
    assert isinstance(client, _ClientState)
    assert upserts
    assert connection_key in app_state["rpc_connections"]
    await emit_event("x", {"ok": True})
    assert ws.sent and ws.sent[0]["type"] == "event"


@pytest.mark.asyncio
async def test_bootstrap_chat_ws_connection_initializes_state():
    ws = _Ws()
    mapping = {}
    upserts = []
    connected = []

    async def _connect(sock):
        connected.append(sock)

    connection_key = await bootstrap_chat_ws_connection(
        websocket=ws,
        manager_connect=_connect,
        ws_to_presence_key=mapping,
        presence_upsert=lambda *args, **kwargs: upserts.append((args, kwargs)),
    )
    assert connection_key.startswith("ws_")
    assert mapping[ws] == connection_key
    assert connected == [ws]
    assert upserts

