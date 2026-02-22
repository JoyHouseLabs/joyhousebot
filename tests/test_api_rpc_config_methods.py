import pytest

from joyhousebot.api.rpc.config_methods import try_handle_config_method
from joyhousebot.config.schema import Config


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


def _build_snapshot(cfg):
    return {"hash": f"h-{cfg.gateway.port}", "ok": True}


@pytest.mark.asyncio
async def test_config_get_and_schema():
    cfg = Config()
    schema_payload = {"type": "object"}
    result_get = await try_handle_config_method(
        method="config.get",
        params={},
        rpc_error=_rpc_error,
        build_config_snapshot=_build_snapshot,
        build_config_schema_payload=lambda: schema_payload,
        apply_config_from_raw=lambda _raw: (True, None),
        get_cached_config=lambda **_: cfg,
        update_config=lambda _body: None,  # type: ignore[arg-type]
        config_update_cls=dict,
        config=cfg,
    )
    assert result_get == (True, {"hash": f"h-{cfg.gateway.port}", "ok": True}, None)

    result_schema = await try_handle_config_method(
        method="config.schema",
        params={},
        rpc_error=_rpc_error,
        build_config_snapshot=_build_snapshot,
        build_config_schema_payload=lambda: schema_payload,
        apply_config_from_raw=lambda _raw: (True, None),
        get_cached_config=lambda **_: cfg,
        update_config=lambda _body: None,  # type: ignore[arg-type]
        config_update_cls=dict,
        config=cfg,
    )
    assert result_schema == (True, schema_payload, None)


@pytest.mark.asyncio
async def test_config_set_raw_invalid_and_valid():
    cfg = Config()

    invalid = await try_handle_config_method(
        method="config.set",
        params={"raw": "{bad json}"},
        rpc_error=_rpc_error,
        build_config_snapshot=_build_snapshot,
        build_config_schema_payload=lambda: {},
        apply_config_from_raw=lambda _raw: (False, "parse failed"),
        get_cached_config=lambda **_: cfg,
        update_config=lambda _body: None,  # type: ignore[arg-type]
        config_update_cls=dict,
        config=cfg,
    )
    assert invalid is not None and invalid[0] is False
    assert invalid[2]["code"] == "INVALID_REQUEST"

    valid = await try_handle_config_method(
        method="config.apply",
        params={"raw": "{}"},
        rpc_error=_rpc_error,
        build_config_snapshot=_build_snapshot,
        build_config_schema_payload=lambda: {},
        apply_config_from_raw=lambda _raw: (True, None),
        get_cached_config=lambda **_: cfg,
        update_config=lambda _body: None,  # type: ignore[arg-type]
        config_update_cls=dict,
        config=cfg,
    )
    assert valid == (True, {"hash": f"h-{cfg.gateway.port}", "ok": True}, None)


@pytest.mark.asyncio
async def test_config_patch_calls_update_and_returns_hash():
    cfg = Config()
    called = {"updated": False}

    async def _update_config(_body):
        called["updated"] = True
        return {"wallet": {"enabled": False}}

    result = await try_handle_config_method(
        method="config.patch",
        params={"providers": {}},
        rpc_error=_rpc_error,
        build_config_snapshot=_build_snapshot,
        build_config_schema_payload=lambda: {},
        apply_config_from_raw=lambda _raw: (True, None),
        get_cached_config=lambda **_: cfg,
        update_config=_update_config,
        config_update_cls=dict,
        config=cfg,
    )
    assert called["updated"] is True
    assert result == (
        True,
        {"ok": True, "updated": True, "wallet": {"enabled": False}, "hash": f"h-{cfg.gateway.port}"},
        None,
    )

