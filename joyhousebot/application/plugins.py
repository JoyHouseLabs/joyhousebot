"""Control-plane operations for explicitly triggered plugin diagnostics."""

from __future__ import annotations

import inspect
import time
from typing import Any

from joyhousebot.capabilities.plugin_registry import CapabilityPluginRegistry
from joyhousebot.contracts.plugins import PluginHealthContext, PluginHealthResult


def _configured_plugins(config: Any) -> tuple[Any, ...]:
    registry = CapabilityPluginRegistry()
    modules = list(getattr(getattr(config, "tools", None), "capability_plugins", []) or [])
    registry.load_modules(modules)
    if bool(getattr(getattr(config, "tools", None), "discover_capability_plugins", False)):
        registry.load_entry_points()
    return registry.plugins


async def run_plugin_diagnostics(*, config: Any, store: Any, plugin_id: str) -> list[dict[str, Any]]:
    """Run a plugin's declared read-only checks and persist safe summaries."""
    plugin = next((item for item in _configured_plugins(config) if item.plugin_id == plugin_id), None)
    if plugin is None:
        raise LookupError(f"configured plugin {plugin_id!r} is not installed in this API replica")
    release = store.get_plugin_release(plugin_id)
    version = str(release["version"] if release else plugin.version)
    checks = getattr(plugin, "health_checks", lambda: ())()
    context = PluginHealthContext(store=store, config=config)
    results: list[dict[str, Any]] = []
    for check in checks:
        started = time.monotonic()
        try:
            result = check.run(context)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, PluginHealthResult):
                raise TypeError("health check must return PluginHealthResult")
        except Exception as exc:  # diagnostics must report failures, not hide them
            result = PluginHealthResult(status="failed", summary="diagnostic failed", details={"error_type": type(exc).__name__})
        duration_ms = int((time.monotonic() - started) * 1000)
        store.record_plugin_check_result(
            plugin_id, version, check.name, result.status, result.summary,
            details=result.details, duration_ms=duration_ms,
        )
        results.append({"name": check.name, **result.to_dict(), "duration_ms": duration_ms})
    return results
