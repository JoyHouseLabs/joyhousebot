"""Shared RPC protocol models for Python/Node bridge runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RpcError:
    """Normalized RPC error payload used across bridge runtimes."""

    code: str
    message: str
    data: dict[str, Any] | None = None


@dataclass(slots=True)
class RpcRequest:
    """JSON-RPC-like request frame."""

    id: str
    method: str
    params: dict[str, Any]


@dataclass(slots=True)
class RpcResponse:
    """JSON-RPC-like response frame."""

    id: str
    ok: bool
    result: Any = None
    error: RpcError | None = None

