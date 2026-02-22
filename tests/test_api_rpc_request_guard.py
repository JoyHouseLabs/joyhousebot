from joyhousebot.api.rpc.request_guard import prepare_rpc_request_context


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _Cfg:
    class Gateway:
        rpc_enabled = True

    gateway = Gateway()


class _Client:
    def __init__(self):
        self.role = "operator"
        self.scopes = {"operator.read"}
        self.client_id = "c1"


class _Registry:
    pass


def test_request_guard_rejects_invalid_frame():
    res = prepare_rpc_request_context(
        req={"type": "bad"},
        client=_Client(),
        connection_key="k1",
        app_state={},
        rpc_error=_rpc_error,
        get_cached_config=lambda: _Cfg(),
        node_registry_cls=_Registry,
        is_method_allowed_by_canary=lambda *_: True,
        authorize_rpc_method=lambda *_: None,
        log_denied=lambda *_: None,
    )
    assert res.error is not None
    assert res.error["code"] == "INVALID_REQUEST"


def test_request_guard_sets_registry_and_returns_context():
    state = {}
    res = prepare_rpc_request_context(
        req={"type": "req", "id": "1", "method": "health", "params": {"x": 1}},
        client=_Client(),
        connection_key="k1",
        app_state=state,
        rpc_error=_rpc_error,
        get_cached_config=lambda: _Cfg(),
        node_registry_cls=_Registry,
        is_method_allowed_by_canary=lambda *_: True,
        authorize_rpc_method=lambda *_: None,
        log_denied=lambda *_: None,
    )
    assert res.error is None
    assert res.method == "health"
    assert res.params == {"x": 1}
    assert isinstance(state.get("node_registry"), _Registry)


def test_request_guard_logs_on_auth_denied():
    calls = []
    res = prepare_rpc_request_context(
        req={"type": "req", "id": "1", "method": "health"},
        client=_Client(),
        connection_key="k1",
        app_state={},
        rpc_error=_rpc_error,
        get_cached_config=lambda: _Cfg(),
        node_registry_cls=_Registry,
        is_method_allowed_by_canary=lambda *_: True,
        authorize_rpc_method=lambda *_: {"code": "DENIED"},
        log_denied=lambda method, role, scopes, client_id: calls.append((method, role, scopes, client_id)),
    )
    assert res.error == {"code": "DENIED"}
    assert calls and calls[0][0] == "health"

