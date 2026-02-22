"""Shared plugin types and helpers."""

from .contracts import BridgeRuntime, NativeRuntime
from .protocol import RpcError, RpcRequest, RpcResponse
from .retry import RetryPolicy, with_retry
from .serialization import decode_response_payload, encode_request_line, normalize_rpc_error, safe_dict
from .types import PluginHostError, PluginRecord, PluginSnapshot

__all__ = [
    "BridgeRuntime",
    "NativeRuntime",
    "PluginHostError",
    "PluginRecord",
    "PluginSnapshot",
    "RpcError",
    "RpcRequest",
    "RpcResponse",
    "RetryPolicy",
    "with_retry",
    "safe_dict",
    "encode_request_line",
    "decode_response_payload",
    "normalize_rpc_error",
]

