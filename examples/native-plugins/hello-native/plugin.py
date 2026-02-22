"""Example python-native plugin for joyhousebot."""

from __future__ import annotations

from typing import Any


class HelloNativePlugin:
    def register(self, api: Any) -> None:
        def _echo_tool(value: Any) -> dict[str, Any]:
            return {"ok": True, "echo": value}

        def _echo_rpc(params: dict[str, Any]) -> dict[str, Any]:
            text = str((params or {}).get("text") or "")
            prefix = str(getattr(api, "plugin_config", {}).get("prefix") or "hello")
            return {"message": f"{prefix}: {text}".strip()}

        def _on_gateway_start(*_args: Any, **_kwargs: Any) -> None:
            return None

        api.register_tool("native.hello.echo", _echo_tool)
        api.register_rpc("native.hello.echo", _echo_rpc)
        api.register_hook("gateway_start", _on_gateway_start, priority=1)
        api.register_cli("native.hello.echo", lambda payload: _echo_rpc(payload))


plugin = HelloNativePlugin()

