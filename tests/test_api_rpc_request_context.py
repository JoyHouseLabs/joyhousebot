import pytest

from joyhousebot.api.rpc.request_context import (
    make_broadcast_rpc_event_adapter,
    make_connect_logger,
    make_rpc_error_adapter,
)


def test_make_rpc_error_adapter_passthrough():
    def _rpc_error(code: str, message: str, data=None):
        return {"code": code, "message": message, "data": data}

    adapter = make_rpc_error_adapter(_rpc_error)
    assert adapter("X", "oops", {"a": 1}) == {"code": "X", "message": "oops", "data": {"a": 1}}


@pytest.mark.asyncio
async def test_make_broadcast_rpc_event_adapter_passthrough():
    calls = []

    async def _broadcast(event, payload, roles=None):
        calls.append((event, payload, roles))

    adapter = make_broadcast_rpc_event_adapter(_broadcast)
    await adapter("evt", {"ok": True}, {"operator"})
    assert calls == [("evt", {"ok": True}, {"operator"})]


def test_make_connect_logger_uses_expected_format():
    calls = []
    logger = make_connect_logger(lambda fmt, role, scopes, client_id: calls.append((fmt, role, scopes, client_id)))
    logger("operator", ["operator.read"], "c1")
    assert calls and calls[0][0] == "RPC connect role={} scopes={} client={}"

