from joyhousebot.plugins.core.protocol import RpcRequest
from joyhousebot.plugins.core.serialization import (
    decode_response_payload,
    encode_request_line,
    normalize_rpc_error,
    to_plugin_host_error,
)


def test_encode_request_line_roundtrip_shape():
    line = encode_request_line(RpcRequest(id="1", method="plugins.load", params={"workspaceDir": "/tmp"}))
    assert '"id": "1"' in line
    assert '"method": "plugins.load"' in line


def test_decode_error_payload_and_convert_to_plugin_error():
    response = decode_response_payload(
        {
            "id": "abc",
            "ok": False,
            "error": {"code": "HOST_NOT_READY", "message": "not loaded", "data": {"x": 1}},
        }
    )
    err = to_plugin_host_error(response, fallback_method="plugins.status")
    assert err.code == "HOST_NOT_READY"
    assert err.message == "not loaded"
    assert err.data == {"x": 1}


def test_normalize_rpc_error_with_non_dict_payload():
    err = normalize_rpc_error("boom")
    assert err.code == "RPC_ERROR"
    assert err.message == "rpc failed"

