"""Shared doctor helpers for plugin runtime diagnostics."""

from __future__ import annotations

from typing import Any


def collect_plugins_doctor_reports(
    *,
    manager: Any,
    workspace_dir: str,
    config_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    native_report = manager.doctor(workspace_dir=workspace_dir, config=config_payload)
    _merge_runtime_from_manager_doctor(manager=manager, native_report=native_report)
    return {}, native_report


def should_run_setup_host(requirements_report: dict[str, Any]) -> bool:
    return False


def snapshot_has_plugin_issues(snapshot: Any) -> bool:
    diagnostics = getattr(snapshot, "diagnostics", []) if snapshot is not None else []
    plugins = getattr(snapshot, "plugins", []) if snapshot is not None else []
    if diagnostics:
        return True
    return any(getattr(plugin, "status", "") == "error" for plugin in plugins)


def _merge_runtime_from_manager_doctor(*, manager: Any, native_report: dict[str, Any]) -> None:
    try:
        manager_report = manager.doctor()
    except Exception:
        return
    if not isinstance(manager_report, dict):
        return
    native_from_manager = manager_report.get("native")
    if isinstance(native_from_manager, dict) and isinstance(native_from_manager.get("runtime"), dict):
        native_report["runtime"] = native_from_manager.get("runtime")
