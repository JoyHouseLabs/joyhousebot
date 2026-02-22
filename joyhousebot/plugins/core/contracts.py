"""Runtime contracts for plugin backends."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .types import PluginSnapshot


@runtime_checkable
class BridgeRuntime(Protocol):
    client: Any

    def load(self, workspace_dir: str, config: dict[str, Any], reload: bool = False) -> PluginSnapshot: ...
    def status(self) -> PluginSnapshot: ...


@runtime_checkable
class NativeRuntime(Protocol):
    def load(self, workspace_dir: str, config: dict[str, Any]) -> Any: ...
    def doctor(self, workspace_dir: str, config: dict[str, Any]) -> dict[str, Any]: ...
    def invoke_rpc(self, registry: Any, method: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def invoke_cli(self, registry: Any, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def dispatch_http(self, registry: Any, request: dict[str, Any]) -> dict[str, Any]: ...

