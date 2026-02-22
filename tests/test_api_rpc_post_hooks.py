import pytest

from joyhousebot.api.rpc.post_hooks import apply_shadow_hook_if_needed


@pytest.mark.asyncio
async def test_apply_shadow_hook_runs_for_target_success():
    calls = []

    async def _shadow(method, params, payload):
        calls.append((method, params, payload))

    result = await apply_shadow_hook_if_needed(
        method="agents.list",
        params={"x": 1},
        result=(True, {"ok": True}, None),
        shadow_methods={"agents.list"},
        run_rpc_shadow=_shadow,
    )
    assert result[0] is True
    assert calls and calls[0][0] == "agents.list"


@pytest.mark.asyncio
async def test_apply_shadow_hook_skips_non_target_or_failed():
    calls = []

    async def _shadow(method, params, payload):
        calls.append((method, params, payload))

    res1 = await apply_shadow_hook_if_needed(
        method="config.get",
        params={},
        result=(False, {"ok": False}, {"code": "X"}),
        shadow_methods={"config.get"},
        run_rpc_shadow=_shadow,
    )
    res2 = await apply_shadow_hook_if_needed(
        method="other.method",
        params={},
        result=(True, {"ok": True}, None),
        shadow_methods={"config.get"},
        run_rpc_shadow=_shadow,
    )
    assert res1[0] is False and res2[0] is True
    assert calls == []

