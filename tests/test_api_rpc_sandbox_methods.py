"""Tests for sandbox RPC methods (sandbox.list, sandbox.recreate, sandbox.explain)."""

import pytest

from joyhousebot.api.rpc.sandbox_methods import try_handle_sandbox_method


@pytest.mark.asyncio
async def test_sandbox_list_returns_items():
    """sandbox.list returns {items: list}."""
    state = {}
    def load(name, default):
        return state.get(name, default)
    result = await try_handle_sandbox_method(
        method="sandbox.list",
        params={"browser": False},
        rpc_error=lambda *_: {},
        load_persistent_state=load,
        save_persistent_state=lambda n, v: None,
    )
    assert result is not None
    ok, data, err = result
    assert ok is True
    assert err is None
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_sandbox_explain_returns_payload():
    """sandbox.explain returns policy and backend."""
    def load(name, default):
        return default
    result = await try_handle_sandbox_method(
        method="sandbox.explain",
        params={"session": "", "agent": ""},
        rpc_error=lambda *_: {},
        load_persistent_state=load,
        save_persistent_state=lambda n, v: None,
    )
    assert result is not None
    ok, data, err = result
    assert ok is True
    assert err is None
    assert "policy" in data
    assert "backend" in data


@pytest.mark.asyncio
async def test_sandbox_recreate_returns_ok():
    """sandbox.recreate returns ok and operation."""
    state = {"sandbox_runtime": {"containers": [], "recreateOps": []}}
    def load(name, default):
        return state.get(name, default)
    def save(name, value):
        state[name] = value
    result = await try_handle_sandbox_method(
        method="sandbox.recreate",
        params={"all": False, "session": "", "agent": "", "browser": False, "force": False},
        rpc_error=lambda *_: {},
        load_persistent_state=load,
        save_persistent_state=save,
    )
    assert result is not None
    ok, data, err = result
    assert ok is True
    assert err is None
    assert data.get("ok") is True
    assert "operation" in data


@pytest.mark.asyncio
async def test_sandbox_unrelated_method_returns_none():
    """Unrelated method returns None."""
    result = await try_handle_sandbox_method(
        method="other.method",
        params={},
        rpc_error=lambda *_: {},
        load_persistent_state=lambda n, d: d,
        save_persistent_state=lambda n, v: None,
    )
    assert result is None
