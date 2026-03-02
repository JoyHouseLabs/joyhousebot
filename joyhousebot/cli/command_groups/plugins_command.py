"""Plugins command group for native Python plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.config.loader import load_config, save_config
from joyhousebot.plugins.manager import get_plugin_manager, initialize_plugins_for_workspace
from joyhousebot.services.plugins.list_service import (
    filter_plugin_rows,
    native_plugin_table_row,
    plugin_info_fields,
    resolve_plugin_info_row,
    row_get,
)
from joyhousebot.services.plugins.status_service import build_compact_status_report, build_status_metric_rows


def _load_snapshot(force_reload: bool = False):
    cfg = load_config()
    workspace_attr = cfg.workspace_path
    workspace = workspace_attr() if callable(workspace_attr) else workspace_attr
    return initialize_plugins_for_workspace(workspace=workspace, config=cfg, force_reload=force_reload)


def _format_native_checks_table(console: Console, report: dict[str, Any]) -> None:
    checks = report.get("checks", {}) if isinstance(report, dict) else {}
    table = Table(title="Native Plugin Runtime")
    table.add_column("Check", style="cyan")
    table.add_column("Value")
    rows = [
        ("workspace dir exists", str(bool(checks.get("workspaceDirExists")))),
        ("native manifests discovered", str(checks.get("discoveredCount", 0))),
        ("native plugins loaded", str(checks.get("loadedCount", 0))),
        ("native plugins errored", str(checks.get("errorCount", 0))),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)
    diagnostics = report.get("diagnostics", [])
    if isinstance(diagnostics, list) and diagnostics:
        diag_table = Table(title="Native Plugin Diagnostics")
        diag_table.add_column("Level", style="cyan")
        diag_table.add_column("Plugin")
        diag_table.add_column("Message")
        for diag in diagnostics:
            diag_table.add_row(
                str(diag.get("level", "")),
                str(diag.get("pluginId", "-")),
                str(diag.get("message", "")),
            )
        console.print(diag_table)


def _format_native_runtime_table(console: Console, report: dict[str, Any]) -> None:
    runtime = report.get("runtime", {}) if isinstance(report, dict) else {}
    totals = runtime.get("totals", {}) if isinstance(runtime, dict) else {}
    by_kind = runtime.get("byKind", {}) if isinstance(runtime, dict) else {}
    table = Table(title="Native Runtime Health")
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    table.add_row("calls", str(totals.get("calls", 0)))
    table.add_row("ok", str(totals.get("ok", 0)))
    table.add_row("errors", str(totals.get("errors", 0)))
    table.add_row("timeouts", str(totals.get("timeouts", 0)))
    console.print(table)
    kind_table = Table(title="Native Runtime By Kind")
    kind_table.add_column("Kind", style="cyan")
    kind_table.add_column("Calls")
    kind_table.add_column("Errors")
    kind_table.add_column("Timeouts")
    for kind in ("rpc", "http", "cli", "tool"):
        row = by_kind.get(kind, {}) if isinstance(by_kind, dict) else {}
        kind_table.add_row(
            kind,
            str(row.get("calls", 0)),
            str(row.get("errors", 0)),
            str(row.get("timeouts", 0)),
        )
    console.print(kind_table)
    open_circuits = runtime.get("openCircuits", []) if isinstance(runtime, dict) else []
    if isinstance(open_circuits, list) and open_circuits:
        circuit_table = Table(title="Native Open Circuits")
        circuit_table.add_column("Key", style="cyan")
        circuit_table.add_column("Failure Streak")
        circuit_table.add_column("Retry After (s)")
        for row in open_circuits:
            circuit_table.add_row(
                str(row.get("key", "")),
                str(row.get("failureStreak", "")),
                str(row.get("retryAfterSeconds", "")),
            )
        console.print(circuit_table)


def register_plugins_commands(app: typer.Typer, console: Console) -> None:
    """Register plugins command group."""
    plugins_app = typer.Typer(help="Manage native Python plugins")
    app.add_typer(plugins_app, name="plugins")

    @plugins_app.command("list")
    def plugins_list(
        keyword: str = typer.Option("", "--keyword", "-k", help="Filter by id/name/source"),
    ) -> None:
        snapshot = _load_snapshot(force_reload=False)
        if snapshot is None:
            console.print("[yellow]Plugins not loaded. Run `plugins doctor` for diagnostics.[/yellow]")
            raise typer.Exit(1)
        rows = filter_plugin_rows(snapshot.plugins, keyword)
        table = Table(title=f"Plugins ({len(rows)})")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Origin")
        table.add_column("Source")
        for row in rows:
            table.add_row(
                str(row.id or ""),
                str(row.name or ""),
                str(row.status or ""),
                str(row.origin or ""),
                str(row.source or ""),
            )
        console.print(table)

    @plugins_app.command("search")
    def plugins_search(keyword: str = typer.Argument(..., help="Search keyword")) -> None:
        plugins_list(keyword=keyword)

    @plugins_app.command("info")
    def plugins_info(plugin_id: str = typer.Argument(..., help="Plugin id")) -> None:
        snapshot = _load_snapshot(force_reload=False)
        if snapshot is None:
            console.print("[red]Plugins not loaded[/red]")
            raise typer.Exit(1)
        row = resolve_plugin_info_row(snapshot.plugins, plugin_id)
        if not row:
            console.print(f"[red]Plugin not found:[/red] {plugin_id}")
            raise typer.Exit(1)
        table = Table(title=f"Plugin: {row_get(row, 'name', plugin_id)}")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        for key, value in plugin_info_fields(row):
            table.add_row(key, value)
        if row_get(row, "error"):
            table.add_row("error", str(row_get(row, "error")))
        console.print(table)

    @plugins_app.command("doctor")
    def plugins_doctor() -> None:
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        report = manager.doctor()
        _format_native_checks_table(console, report)
        _format_native_runtime_table(console, report)
        snapshot = _load_snapshot(force_reload=True)
        if snapshot is None:
            console.print("[red]Plugin load failed.[/red]")
            raise typer.Exit(1)
        if not snapshot.diagnostics:
            console.print("[green]No plugin issues detected.[/green]")
            return
        table = Table(title="Plugin Diagnostics")
        table.add_column("Level", style="cyan")
        table.add_column("Plugin")
        table.add_column("Message")
        for diag in snapshot.diagnostics:
            table.add_row(
                str(diag.get("level", "")),
                str(diag.get("pluginId", "-")),
                str(diag.get("message", "")),
            )
        console.print(table)

    @plugins_app.command("reload")
    def plugins_reload() -> None:
        snapshot = _load_snapshot(force_reload=True)
        if snapshot is None:
            console.print("[red]Plugin reload failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Reloaded {len(snapshot.plugins)} plugins")

    @plugins_app.command("status")
    def plugins_status(
        as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
        compact: bool = typer.Option(False, "--compact", help="Output compact JSON SLI fields"),
    ) -> None:
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        report = manager.status_report()
        if compact:
            compact_report = build_compact_status_report(report if isinstance(report, dict) else {})
            console.print_json(data=compact_report)
            return
        if as_json:
            console.print_json(data=report)
            return
        plugins_row = report.get("plugins", {}) if isinstance(report, dict) else {}
        by_origin = plugins_row.get("byOrigin", {}) if isinstance(plugins_row, dict) else {}
        table = Table(title="Plugins Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value")
        for key, value in build_status_metric_rows(report if isinstance(report, dict) else {}):
            table.add_row(key, value)
        console.print(table)
        if isinstance(by_origin, dict) and by_origin:
            origin_table = Table(title="Plugins By Origin")
            origin_table.add_column("Origin", style="cyan")
            origin_table.add_column("Count")
            for origin, count in sorted(by_origin.items(), key=lambda item: str(item[0])):
                origin_table.add_row(str(origin), str(count))
            console.print(origin_table)
        runtime = report.get("nativeRuntime", {}) if isinstance(report, dict) else {}
        if isinstance(runtime, dict):
            _format_native_runtime_table(console, {"runtime": runtime})

    @plugins_app.command("gateway-methods")
    def plugins_gateway_methods() -> None:
        snapshot = _load_snapshot(force_reload=False)
        if snapshot is None:
            console.print("[red]Plugins not loaded[/red]")
            raise typer.Exit(1)
        for method in snapshot.gateway_methods:
            console.print(method)

    @plugins_app.command("cli-list")
    def plugins_cli_list() -> None:
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        commands = manager.cli_commands()
        table = Table(title=f"Plugin CLI Commands ({len(commands)})")
        table.add_column("Command", style="cyan")
        for command in commands:
            table.add_row(command)
        console.print(table)

    @plugins_app.command("cli-run")
    def plugins_cli_run(
        command: str = typer.Argument(..., help="Plugin CLI command name"),
        payload: str = typer.Option("{}", "--payload", help="JSON payload for command"),
    ) -> None:
        try:
            payload_obj = json.loads(payload) if payload.strip() else {}
        except Exception as exc:
            console.print(f"[red]Invalid JSON payload:[/red] {exc}")
            raise typer.Exit(1)
        if not isinstance(payload_obj, dict):
            console.print("[red]Payload must be a JSON object[/red]")
            raise typer.Exit(1)
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        result = manager.invoke_cli_command(command=command, payload=payload_obj)
        if not bool(result.get("ok")):
            err = result.get("error", {})
            if isinstance(err, dict):
                console.print(f"[red]{err.get('code', 'ERROR')}:[/red] {err.get('message', 'invoke failed')}")
            else:
                console.print("[red]plugin cli invoke failed[/red]")
            raise typer.Exit(1)
        console.print(result.get("result"))

    @plugins_app.command("services-start")
    def plugins_services_start() -> None:
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        rows = manager.start_services()
        table = Table(title="Plugin Services Start")
        table.add_column("Service", style="cyan")
        table.add_column("Started")
        table.add_column("Error")
        for row in rows if isinstance(rows, list) else []:
            table.add_row(str(row.get("id", "")), str(row.get("started", False)), str(row.get("error", "")))
        console.print(table)

    @plugins_app.command("services-stop")
    def plugins_services_stop() -> None:
        manager = get_plugin_manager()
        _load_snapshot(force_reload=False)
        rows = manager.stop_services()
        table = Table(title="Plugin Services Stop")
        table.add_column("Service", style="cyan")
        table.add_column("Stopped")
        table.add_column("Error")
        for row in rows if isinstance(rows, list) else []:
            table.add_row(str(row.get("id", "")), str(row.get("stopped", False)), str(row.get("error", "")))
        console.print(table)

    @plugins_app.command("enable")
    def plugins_enable(plugin_id: str = typer.Argument(..., help="Plugin id")) -> None:
        cfg = load_config()
        entry = cfg.plugins.entries.get(plugin_id)
        if entry is None:
            from joyhousebot.config.schema import PluginEntryConfig
            cfg.plugins.entries[plugin_id] = PluginEntryConfig(enabled=True)
        else:
            entry.enabled = True
        save_config(cfg)
        _load_snapshot(force_reload=True)
        console.print(f"[green]✓[/green] Enabled plugin: {plugin_id}")

    @plugins_app.command("disable")
    def plugins_disable(plugin_id: str = typer.Argument(..., help="Plugin id")) -> None:
        cfg = load_config()
        entry = cfg.plugins.entries.get(plugin_id)
        if entry is None:
            from joyhousebot.config.schema import PluginEntryConfig
            cfg.plugins.entries[plugin_id] = PluginEntryConfig(enabled=False)
        else:
            entry.enabled = False
        save_config(cfg)
        _load_snapshot(force_reload=True)
        console.print(f"[green]✓[/green] Disabled plugin: {plugin_id}")

    @plugins_app.command("tools")
    def plugins_tools() -> None:
        snapshot = _load_snapshot(force_reload=False)
        if snapshot is None:
            console.print("[red]Plugins not loaded[/red]")
            raise typer.Exit(1)
        table = Table(title=f"Plugin Tools ({len(snapshot.tool_names)})")
        table.add_column("Tool Name", style="cyan")
        for name in sorted(snapshot.tool_names):
            table.add_row(name)
        console.print(table)
