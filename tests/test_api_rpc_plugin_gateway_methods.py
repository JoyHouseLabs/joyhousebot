from joyhousebot.api.rpc.plugin_gateway_methods import try_handle_plugin_gateway_method


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _PluginManager:
    def __init__(self, result=None, raise_error: bool = False):
        self.result = result if result is not None else {"ok": True, "payload": {"x": 1}}
        self.raise_error = raise_error
        self.last = None

    def invoke_gateway_method(self, *, method, params):
        if self.raise_error:
            raise RuntimeError("boom")
        self.last = {"method": method, "params": params}
        return self.result


def test_plugin_gateway_success():
    manager = _PluginManager(result={"ok": True, "payload": {"ok": True}})
    app_state = {"plugin_manager": manager}
    res = try_handle_plugin_gateway_method(
        method="plugin.echo",
        params={"a": 1},
        app_state=app_state,
        rpc_error=_rpc_error,
        plugin_gateway_methods=lambda: ["plugin.echo"],
    )
    assert res == (True, {"ok": True}, None)
    assert manager.last["method"] == "plugin.echo"


def test_plugin_gateway_error_payload():
    manager = _PluginManager(result={"ok": False, "error": {"code": "X", "message": "bad"}})
    app_state = {"plugin_manager": manager}
    res = try_handle_plugin_gateway_method(
        method="plugin.echo",
        params={},
        app_state=app_state,
        rpc_error=_rpc_error,
        plugin_gateway_methods=lambda: ["plugin.echo"],
    )
    assert res is not None and res[0] is False
    assert res[2]["code"] == "X"


def test_plugin_gateway_returns_none_when_unmatched():
    app_state = {"plugin_manager": _PluginManager()}
    res = try_handle_plugin_gateway_method(
        method="unknown.method",
        params={},
        app_state=app_state,
        rpc_error=_rpc_error,
        plugin_gateway_methods=lambda: ["plugin.echo"],
    )
    assert res is None

