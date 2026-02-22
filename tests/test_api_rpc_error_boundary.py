from joyhousebot.api.rpc.error_boundary import http_exception_result, unhandled_exception_result, unknown_method_result


def _rpc_error(code: str, message: str, data=None):
    return {"code": code, "message": message, "data": data}


class _HttpLikeError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def test_unknown_method_result():
    res = unknown_method_result(method="x.y", rpc_error=_rpc_error)
    assert res[0] is False
    assert res[2]["code"] == "INVALID_REQUEST"


def test_http_exception_result_logs_and_maps():
    calls = []
    err = _HttpLikeError(404, "not found")
    res = http_exception_result(
        method="abc",
        exc=err,
        log_info=lambda fmt, m, status: calls.append((fmt, m, status)),
        rpc_error=_rpc_error,
    )
    assert res == (False, None, {"code": "HTTP_ERROR", "message": "not found", "data": {"status_code": 404}})
    assert calls and calls[0][1] == "abc"


def test_unhandled_exception_result_logs_and_maps():
    calls = []
    res = unhandled_exception_result(
        method="abc",
        exc=RuntimeError("boom"),
        log_exception=lambda fmt, m: calls.append((fmt, m)),
        rpc_error=_rpc_error,
    )
    assert res[0] is False
    assert res[2]["code"] == "INTERNAL_ERROR"
    assert "boom" in res[2]["message"]
    assert calls and calls[0][1] == "abc"

