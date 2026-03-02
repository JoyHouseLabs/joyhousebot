"""Example python-native plugin for joyhousebot."""

from __future__ import annotations

from typing import Any


class HelloNativePlugin:
    def register(self, api: Any) -> None:
        def echo(value: Any) -> dict[str, Any]:
            return {"ok": True, "echo": value}

        def greet(params: dict[str, Any]) -> dict[str, Any]:
            name = str((params or {}).get("name") or "world")
            prefix = str(getattr(api, "plugin_config", {}).get("prefix") or "hello")
            return {"message": f"{prefix}: {name}".strip()}

        api.register_tool("echo", echo)
        api.register_rpc("greet", greet)
        api.register_cli("greet", lambda payload: greet(payload))


plugin = HelloNativePlugin()
