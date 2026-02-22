"""Types for plugin host integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PluginHostError(Exception):
    """Raised when plugin host RPC fails."""

    code: str
    message: str
    data: dict[str, Any] | None = None

    def __str__(self) -> str:
        if self.data:
            return f"{self.code}: {self.message} ({self.data})"
        return f"{self.code}: {self.message}"


@dataclass(slots=True)
class PluginRecord:
    """Serializable plugin record returned by host."""

    id: str
    name: str
    source: str
    origin: str
    status: str
    enabled: bool
    version: str | None = None
    description: str | None = None
    kind: str | None = None
    runtime: str | None = None
    capabilities: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    hook_names: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    gateway_methods: list[str] = field(default_factory=list)
    cli_commands: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class PluginSnapshot:
    """Snapshot of loaded plugin host state."""

    loaded_at_ms: int
    workspace_dir: str
    openclaw_dir: str
    plugins: list[PluginRecord] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    gateway_methods: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    service_ids: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    hook_names: list[str] = field(default_factory=list)
    skills_dirs: list[str] = field(default_factory=list)

