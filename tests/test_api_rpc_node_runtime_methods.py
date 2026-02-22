import pytest

from joyhousebot.api.rpc.node_runtime_methods import try_handle_node_runtime_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _Node:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.display_name = "Node"
        self.platform = "darwin"
        self.version = "1"
        self.core_version = "1"
        self.ui_version = "1"
        self.device_family = "mac"
        self.model_identifier = "macbook"
        self.remote_ip = "127.0.0.1"
        self.caps = ["browser"]
        self.commands = ["echo"]
        self.permissions = {"fs": True}
        self.path_env = "/usr/bin"
        self.connected_at_ms = 1


class _InvokeResult:
    def __init__(self, ok=True):
        self.ok = ok
        self.payload = {"ok": ok}
        self.payload_json = None
        self.error = None if ok else {"code": "UNAVAILABLE", "message": "bad"}


class _NodeRegistry:
    def __init__(self):
        self._node = _Node("n1")
        self.last_result = None

    def list_connected(self):
        return [self._node]

    def get(self, node_id: str):
        return self._node if node_id == "n1" else None

    async def invoke(self, **kwargs):
        self.last_result = kwargs
        return _InvokeResult(ok=True)

    def handle_invoke_result(self, **kwargs):
        self.last_result = kwargs
        return True


@pytest.mark.asyncio
async def test_node_list_describe_and_invoke():
    registry = _NodeRegistry()
    pairs = {"pending": [], "paired": [{"deviceId": "n1", "roles": ["node"], "displayName": "PairedNode"}]}
    app_state = {}

    def _load_pairs():
        return pairs

    result = await try_handle_node_runtime_method(
        method="node.list",
        params={},
        client_id=None,
        app_state=app_state,
        node_registry=registry,
        config=object(),
        rpc_error=_rpc_error,
        load_device_pairs_state=_load_pairs,
        save_persistent_state=lambda *_: None,
        now_ms=lambda: 100,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        normalize_node_event_payload=lambda _: ({}, None),
        run_node_agent_request=lambda **_: _ok_node_req(),
        get_store=lambda: object(),
        broadcast_rpc_event=lambda *_: _ok_broadcast(),
    )
    assert result is not None and result[0] is True
    assert result[1]["nodes"][0]["nodeId"] == "n1"

    invoke = await try_handle_node_runtime_method(
        method="node.invoke",
        params={"nodeId": "n1", "command": "echo", "params": {"x": 1}},
        client_id=None,
        app_state=app_state,
        node_registry=registry,
        config=object(),
        rpc_error=_rpc_error,
        load_device_pairs_state=_load_pairs,
        save_persistent_state=lambda *_: None,
        now_ms=lambda: 100,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        normalize_node_event_payload=lambda _: ({}, None),
        run_node_agent_request=lambda **_: _ok_node_req(),
        get_store=lambda: object(),
        broadcast_rpc_event=lambda *_: _ok_broadcast(),
    )
    assert invoke is not None and invoke[0] is True
    assert invoke[1]["nodeId"] == "n1"


@pytest.mark.asyncio
async def test_node_event_updates_subscriptions():
    registry = _NodeRegistry()
    app_state = {"rpc_node_subscriptions": {}}
    saves = {}
    events = []

    class _Store:
        def log_task_event(self, **kwargs):
            saves["log"] = kwargs

    async def _broadcast(event, payload, roles=None):
        events.append((event, payload, roles))

    async def _run_node_req(**_):
        return True, ""

    res = await try_handle_node_runtime_method(
        method="node.event",
        params={"event": "chat.subscribe", "nodeId": "n1", "payload": {"sessionKey": "s1"}},
        client_id=None,
        app_state=app_state,
        node_registry=registry,
        config=object(),
        rpc_error=_rpc_error,
        load_device_pairs_state=lambda: {"pending": [], "paired": []},
        save_persistent_state=lambda k, v: saves.__setitem__(k, v),
        now_ms=lambda: 200,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        normalize_node_event_payload=lambda p: (p.get("payload"), None),
        run_node_agent_request=_run_node_req,
        get_store=lambda: _Store(),
        broadcast_rpc_event=_broadcast,
    )
    assert res is not None and res[0] is True
    assert "n1" in app_state["rpc_node_subscriptions"]
    assert any(item[0] == "node.event" for item in events)


async def _ok_broadcast():
    return None


async def _ok_node_req():
    return True, ""

