"""In-memory node registry and invoke broker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class NodeSession:
    node_id: str
    conn_id: str
    display_name: str | None = None
    platform: str | None = None
    version: str | None = None
    core_version: str | None = None
    ui_version: str | None = None
    device_family: str | None = None
    model_identifier: str | None = None
    remote_ip: str | None = None
    caps: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    permissions: dict[str, bool] | None = None
    path_env: str | None = None
    connected_at_ms: int = 0


@dataclass
class NodeInvokeResult:
    ok: bool
    payload: Any | None = None
    payload_json: str | None = None
    error: dict[str, Any] | None = None


class NodeRegistry:
    """Tracks connected nodes and brokers node.invoke roundtrips."""

    def __init__(self):
        self._nodes_by_id: dict[str, NodeSession] = {}
        self._node_sockets: dict[str, Any] = {}
        self._nodes_by_conn: dict[str, str] = {}
        self._pending: dict[str, tuple[str, asyncio.Future[NodeInvokeResult]]] = {}
        self._lock = asyncio.Lock()

    async def register(self, *, node: NodeSession, socket: Any) -> None:
        async with self._lock:
            self._nodes_by_id[node.node_id] = node
            self._node_sockets[node.node_id] = socket
            self._nodes_by_conn[node.conn_id] = node.node_id

    async def unregister_by_conn(self, conn_id: str) -> str | None:
        async with self._lock:
            node_id = self._nodes_by_conn.pop(conn_id, None)
            if not node_id:
                return None
            self._nodes_by_id.pop(node_id, None)
            self._node_sockets.pop(node_id, None)
            # Resolve pending invokes for this node as disconnected.
            doomed = [invoke_id for invoke_id, (nid, _) in self._pending.items() if nid == node_id]
            for invoke_id in doomed:
                _, fut = self._pending.pop(invoke_id)
                if not fut.done():
                    fut.set_result(
                        NodeInvokeResult(
                            ok=False,
                            error={"code": "NOT_CONNECTED", "message": "node disconnected"},
                        )
                    )
            return node_id

    def list_connected(self) -> list[NodeSession]:
        return sorted(self._nodes_by_id.values(), key=lambda x: x.connected_at_ms, reverse=True)

    def get(self, node_id: str) -> NodeSession | None:
        return self._nodes_by_id.get(node_id)

    async def invoke(
        self,
        *,
        node_id: str,
        command: str,
        params: Any = None,
        timeout_ms: int = 30000,
        idempotency_key: str | None = None,
    ) -> NodeInvokeResult:
        socket = self._node_sockets.get(node_id)
        if not socket:
            return NodeInvokeResult(
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "node not connected"},
            )
        invoke_id = str(uuid4())
        fut: asyncio.Future[NodeInvokeResult] = asyncio.get_running_loop().create_future()
        self._pending[invoke_id] = (node_id, fut)
        try:
            await socket.send_json(
                {
                    "type": "event",
                    "event": "node.invoke.request",
                    "payload": {
                        "id": invoke_id,
                        "nodeId": node_id,
                        "command": command,
                        "params": params,
                        "timeoutMs": timeout_ms,
                        "idempotencyKey": idempotency_key,
                    },
                }
            )
            return await asyncio.wait_for(fut, timeout=max(1, timeout_ms) / 1000.0)
        except asyncio.TimeoutError:
            return NodeInvokeResult(
                ok=False,
                error={"code": "TIMEOUT", "message": "node invoke timed out"},
            )
        except Exception as e:
            return NodeInvokeResult(
                ok=False,
                error={"code": "UNAVAILABLE", "message": str(e)},
            )
        finally:
            self._pending.pop(invoke_id, None)

    def handle_invoke_result(
        self,
        *,
        invoke_id: str,
        node_id: str,
        ok: bool,
        payload: Any = None,
        payload_json: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        pending = self._pending.get(invoke_id)
        if not pending:
            return False
        expected_node_id, fut = pending
        if expected_node_id != node_id:
            return False
        if not fut.done():
            fut.set_result(
                NodeInvokeResult(
                    ok=ok,
                    payload=payload,
                    payload_json=payload_json,
                    error=error,
                )
            )
        return True

    async def send_event(self, *, node_id: str, event: str, payload: Any = None) -> bool:
        socket = self._node_sockets.get(node_id)
        if not socket:
            return False
        try:
            await socket.send_json(
                {
                    "type": "event",
                    "event": event,
                    "payload": payload,
                }
            )
            return True
        except Exception:
            return False
