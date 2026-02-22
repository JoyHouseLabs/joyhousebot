import pytest

from joyhousebot.api.server import RpcClientState, _handle_rpc_request, app_state


@pytest.mark.asyncio
async def test_node_event_subscribe_unsubscribe_updates_registry():
    old = dict(app_state)
    try:
        app_state["rpc_node_subscriptions"] = {}
        node = RpcClientState(connected=True, role="node", scopes=set(), client_id="node-test")

        ok, _, err = await _handle_rpc_request(
            {
                "type": "req",
                "id": "1",
                "method": "node.event",
                "params": {"event": "chat.subscribe", "payload": {"sessionKey": "main"}},
            },
            node,
            "conn-node-test",
        )
        assert ok, err
        assert app_state["rpc_node_subscriptions"].get("node-test") == {"main"}

        ok, _, err = await _handle_rpc_request(
            {
                "type": "req",
                "id": "2",
                "method": "node.event",
                "params": {"event": "chat.unsubscribe", "payloadJSON": '{"sessionKey":"main"}'},
            },
            node,
            "conn-node-test",
        )
        assert ok, err
        assert app_state["rpc_node_subscriptions"] == {}
    finally:
        app_state.clear()
        app_state.update(old)


@pytest.mark.asyncio
async def test_node_event_exec_updates_heartbeat():
    old = dict(app_state)
    try:
        node = RpcClientState(connected=True, role="node", scopes=set(), client_id="node-exec")
        ok, _, err = await _handle_rpc_request(
            {
                "type": "req",
                "id": "3",
                "method": "node.event",
                "params": {"event": "exec.finished", "payload": {"runId": "r1", "exitCode": 0}},
            },
            node,
            "conn-node-exec",
        )
        assert ok, err
        assert isinstance(app_state.get("rpc_last_heartbeat"), int)
        assert app_state["rpc_last_heartbeat"] > 0
    finally:
        app_state.clear()
        app_state.update(old)
