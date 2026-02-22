import pytest

from joyhousebot.api.rpc.health_status_methods import try_handle_health_status_method


@pytest.mark.asyncio
async def test_health_and_status_delegate_to_overview_and_shadow():
    shadow_calls = []

    async def _overview():
        return {"ok": True, "alerts": []}

    async def _shadow(method, params, payload):
        shadow_calls.append((method, params, payload))

    health = await try_handle_health_status_method(
        method="health",
        params={"x": 1},
        control_overview=_overview,
        run_rpc_shadow=_shadow,
        load_persistent_state=lambda *_: {},
    )
    status = await try_handle_health_status_method(
        method="status",
        params={"y": 2},
        control_overview=_overview,
        run_rpc_shadow=_shadow,
        load_persistent_state=lambda *_: {},
    )
    assert health is not None and health[0] is True
    assert status is not None and status[0] is True
    assert shadow_calls[0][0] == "health"
    assert shadow_calls[1][0] == "status"


@pytest.mark.asyncio
async def test_last_heartbeat_reads_persistent_state():
    res = await try_handle_health_status_method(
        method="last-heartbeat",
        params={},
        control_overview=lambda: _dummy_overview(),
        run_rpc_shadow=lambda *_: _dummy_shadow(),
        load_persistent_state=lambda _k, _d: {"ts": 123},
    )
    assert res == (True, {"ok": True, "ts": 123}, None)


async def _dummy_overview():
    return {}


async def _dummy_shadow():
    return None

