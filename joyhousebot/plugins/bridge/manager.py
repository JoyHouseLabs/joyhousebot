"""Bridge manager for Node plugin host lifecycle and calls."""

from __future__ import annotations

from typing import Any

from .host_client import PluginHostClient
from joyhousebot.plugins.core.types import PluginSnapshot


class BridgePluginManager:
    """Thin wrapper around Node host client."""

    def __init__(self, openclaw_dir: str | None = None):
        self.client = PluginHostClient(openclaw_dir=openclaw_dir)
        self.snapshot: PluginSnapshot | None = None

    def _openclaw_dir_from_config(self, config: dict[str, Any]) -> str | None:
        plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
        if not plugins:
            return None
        path = plugins.get("openclaw_dir") or plugins.get("openclawDir")
        return (path or "").strip() or None

    def load(self, workspace_dir: str, config: dict[str, Any], reload: bool = False) -> PluginSnapshot:
        if reload or self.snapshot is None:
            openclaw_dir = self._openclaw_dir_from_config(config)
            if reload:
                self.snapshot = self.client.reload(
                    workspace_dir=workspace_dir, config=config, openclaw_dir=openclaw_dir
                )
            else:
                self.snapshot = self.client.load(
                    workspace_dir=workspace_dir, config=config, openclaw_dir=openclaw_dir
                )
        return self.snapshot

    def status(self) -> PluginSnapshot:
        self.snapshot = self.client.status()
        return self.snapshot

