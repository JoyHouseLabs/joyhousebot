import pytest

from joyhousebot.api.rpc.pairing_methods import try_handle_pairing_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


@pytest.mark.asyncio
async def test_device_pair_and_token_methods():
    state = {
        "rpc.device_pairs": {"pending": [{"requestId": "r1", "deviceId": "d1", "displayName": "D1"}], "paired": []}
    }

    def _load(name, default):
        return state.get(name, default)

    def _save(name, value):
        state[name] = value

    async def _broadcast(_event, _payload, _roles):
        return None

    approve = await try_handle_pairing_method(
        method="device.pair.approve",
        params={"requestId": "r1"},
        client_id=None,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        load_device_pairs_state=lambda: state["rpc.device_pairs"],
        hash_pairing_token=lambda token: f"h:{token}",
        now_ms=lambda: 100,
        broadcast_rpc_event=_broadcast,
    )
    assert approve == (True, {"ok": True}, None)
    assert len(state["rpc.device_pairs"]["paired"]) == 1

    rotate = await try_handle_pairing_method(
        method="device.token.rotate",
        params={"deviceId": "d1"},
        client_id=None,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        load_device_pairs_state=lambda: state["rpc.device_pairs"],
        hash_pairing_token=lambda token: f"h:{token}",
        now_ms=lambda: 100,
        broadcast_rpc_event=_broadcast,
    )
    assert rotate is not None and rotate[0] is True
    assert str(rotate[1]["token"]).startswith("tok_")


@pytest.mark.asyncio
async def test_node_pair_request_approve_verify():
    state = {"rpc.device_pairs": {"pending": [], "paired": []}, "rpc.node_tokens": {}}
    events = []

    def _load(name, default):
        return state.get(name, default)

    def _save(name, value):
        state[name] = value

    async def _broadcast(event, payload, roles):
        events.append((event, payload, roles))

    request = await try_handle_pairing_method(
        method="node.pair.request",
        params={"nodeId": "n1", "displayName": "node-1"},
        client_id=None,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        load_device_pairs_state=lambda: state["rpc.device_pairs"],
        hash_pairing_token=lambda token: f"h:{token}",
        now_ms=lambda: 200,
        broadcast_rpc_event=_broadcast,
    )
    assert request is not None and request[0] is True
    request_id = request[1]["request"]["requestId"]

    approve = await try_handle_pairing_method(
        method="node.pair.approve",
        params={"requestId": request_id},
        client_id=None,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        load_device_pairs_state=lambda: state["rpc.device_pairs"],
        hash_pairing_token=lambda token: f"h:{token}",
        now_ms=lambda: 300,
        broadcast_rpc_event=_broadcast,
    )
    assert approve is not None and approve[0] is True
    token = approve[1]["token"]

    verify = await try_handle_pairing_method(
        method="node.pair.verify",
        params={"nodeId": "n1", "token": token},
        client_id=None,
        rpc_error=_rpc_error,
        load_persistent_state=_load,
        save_persistent_state=_save,
        load_device_pairs_state=lambda: state["rpc.device_pairs"],
        hash_pairing_token=lambda token: f"h:{token}",
        now_ms=lambda: 300,
        broadcast_rpc_event=_broadcast,
    )
    assert verify == (True, {"ok": True, "nodeId": "n1"}, None)
    assert any(item[0] == "node.pair.requested" for item in events)
    assert any(item[0] == "node.pair.resolved" for item in events)

