"""Agent-scoped memory policy normalization and authorization.

The database stores a JSON policy on every Agent revision.  This module keeps
the runtime checks in one place so ContextBuilder and memory tools cannot
silently drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_LAYER_DEFAULTS: dict[str, dict[str, Any]] = {
    "working": {"read": True, "write": False, "persist": False},
    "session": {"read": True, "write": False, "persist": True},
    "episodic": {"read": True, "write": True, "persist": True},
    "profile": {"read": True, "write": True, "persist": True},
    "long_term": {"read": True, "write": True, "persist": True},
    "agent": {"read": False, "write": False, "persist": True},
}


@dataclass(frozen=True, slots=True)
class EffectiveMemoryPolicy:
    """Normalized policy used by one immutable Agent revision at runtime."""

    enabled: bool
    mode: str
    scope: str
    layers: dict[str, dict[str, Any]]
    read_mode: str
    write_mode: str
    retrieval: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "EffectiveMemoryPolicy":
        raw = dict(value or {})
        # Empty policies are retained for backwards compatibility with old
        # executors that used the platform retrieval defaults.
        legacy = not any(key in raw for key in ("enabled", "mode", "layers"))
        enabled = bool(raw.get("enabled", raw.get("read", True)))
        if raw.get("enabled") is False or raw.get("read") is False:
            enabled = False

        layers = {name: dict(settings) for name, settings in _LAYER_DEFAULTS.items()}
        supplied_layers = raw.get("layers")
        if isinstance(supplied_layers, dict):
            for name, settings in supplied_layers.items():
                if name in layers and isinstance(settings, dict):
                    layers[name].update(settings)

        # Legacy policies only had global read/write flags.  Apply them to
        # every durable layer while preserving the new per-layer form.
        if "layers" not in raw:
            if "read" in raw:
                for settings in layers.values():
                    settings["read"] = bool(raw["read"])
            if "write" in raw:
                for name, settings in layers.items():
                    if name not in {"working", "session"}:
                        settings["write"] = bool(raw["write"])
        if not enabled:
            for settings in layers.values():
                settings["read"] = False
                settings["write"] = False

        write_mode = str(raw.get("write_mode") or ("direct" if legacy else "none"))
        if raw.get("write") is False:
            write_mode = "none"
        if write_mode not in {"none", "candidate", "direct"}:
            write_mode = "candidate"
        if write_mode == "none":
            for settings in layers.values():
                settings["write"] = False

        return cls(
            enabled=enabled,
            mode=str(raw.get("mode") or ("legacy" if legacy else "personalized")),
            scope=str(raw.get("scope") or "user_agent"),
            layers=layers,
            read_mode=str(raw.get("read_mode") or ("auto" if enabled else "none")),
            write_mode=write_mode,
            retrieval=dict(raw.get("retrieval") or {}),
        )

    @property
    def can_read_context(self) -> bool:
        return self.enabled and self.read_mode == "auto" and any(
            self.layer_enabled(name, "read") for name in ("profile", "long_term", "episodic")
        )

    @property
    def can_read_tools(self) -> bool:
        return self.enabled and self.read_mode != "none" and any(
            self.layer_enabled(name, "read") for name in ("profile", "long_term", "episodic", "agent")
        )

    @property
    def can_consolidate(self) -> bool:
        return self.enabled and self.write_mode != "none" and any(
            self.layer_enabled(name, "write") for name in ("profile", "long_term", "episodic")
        )

    def layer_enabled(self, layer: str, operation: str) -> bool:
        return bool(self.enabled and self.layers.get(layer, {}).get(operation, False))

    @staticmethod
    def layer_for_path(path: str) -> str:
        clean = str(path or "").replace("\\", "/").lstrip("/")
        name = clean.rsplit("/", 1)[-1]
        if name == "PROFILE.md":
            return "profile"
        if name == "MEMORY.md":
            return "long_term"
        if name == "HISTORY.md" or name.endswith(".md") and len(name) == 13:
            return "episodic"
        if name == ".abstract":
            return "episodic"
        if clean.startswith("agent/"):
            return "agent"
        return "long_term"

    def allows_path(self, path: str, operation: str, *, direct: bool = False) -> bool:
        layer = self.layer_for_path(path)
        if operation == "read" and not self.can_read_tools:
            return False
        if not self.layer_enabled(layer, operation):
            return False
        if direct and operation == "write" and self.write_mode != "direct":
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "scope": self.scope,
            "layers": self.layers,
            "read_mode": self.read_mode,
            "write_mode": self.write_mode,
            "retrieval": self.retrieval,
        }
