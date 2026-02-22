import pytest

from joyhousebot.api.rpc.browser_methods import try_handle_browser_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _Node:
    def __init__(self):
        self.node_id = "n1"
        self.caps = ["browser"]
        self.commands = ["browser.proxy"]


class _InvokeResult:
    def __init__(self, ok=True, payload=None):
        self.ok = ok
        self.payload = payload if payload is not None else {"result": {"ok": True}, "files": []}
        self.payload_json = None
        self.error = None if ok else {"code": "UNAVAILABLE", "message": "failed"}


class _Registry:
    def __init__(self):
        self.node = _Node()
        self.last = None

    def list_connected(self):
        return [self.node]

    async def invoke(self, **kwargs):
        self.last = kwargs
        return _InvokeResult(ok=True)


class _Cfg:
    class Gateway:
        node_browser_mode = "auto"
        node_browser_target = ""

    gateway = Gateway()


@pytest.mark.asyncio
async def test_browser_request_via_node_proxy():
    registry = _Registry()
    cfg = _Cfg()
    res = await try_handle_browser_method(
        method="browser.request",
        params={"method": "GET", "path": "/snapshot"},
        config=cfg,
        node_registry=registry,
        rpc_error=_rpc_error,
        resolve_browser_node=lambda nodes, _target: nodes[0] if nodes else None,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        persist_browser_proxy_files=lambda _files: {},
        apply_browser_proxy_paths=lambda _result, _mapping: None,
        browser_control_url="",
    )
    assert res is not None and res[0] is True
    assert res[1]["ok"] is True
    assert registry.last["command"] == "browser.proxy"


@pytest.mark.asyncio
async def test_browser_request_validation():
    registry = _Registry()
    cfg = _Cfg()
    res = await try_handle_browser_method(
        method="browser.request",
        params={"method": "PUT", "path": "/x"},
        config=cfg,
        node_registry=registry,
        rpc_error=_rpc_error,
        resolve_browser_node=lambda nodes, _target: nodes[0] if nodes else None,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        persist_browser_proxy_files=lambda _files: {},
        apply_browser_proxy_paths=lambda _result, _mapping: None,
        browser_control_url="",
    )
    assert res is not None and res[0] is False
    assert res[2]["code"] == "INVALID_REQUEST"

