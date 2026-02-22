"""Plugins command group backed by Node plugin host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.config.loader import load_config, save_config
from joyhousebot.plugins.core.types import PluginHostError
from joyhousebot.plugins.installer import install_package
from joyhousebot.plugins.manager import get_plugin_manager, initialize_plugins_for_workspace
from joyhousebot.services.plugins.doctor_service import (
    collect_plugins_doctor_reports,
    should_run_setup_host,
    snapshot_has_plugin_issues,
)
from joyhousebot.services.plugins.list_service import (
    filter_plugin_rows,
    is_native_plugin_row,
    native_plugin_table_row,
    plugin_info_fields,
    plugin_table_row,
    resolve_plugin_info_row,
    row_get,
)
from joyhousebot.services.plugins.status_service import build_compact_status_report, build_status_metric_rows


def _load_snapshot(force_reload: bool = False):
    cfg = load_config()
    workspace_attr = cfg.workspace_path
    workspace = workspace_attr() if callable(workspace_attr) else workspace_attr
    return initialize_plugins_for_workspace(workspace=workspace, config=cfg, force_reload=force_reload)


def _config_patch_or_fallback(
    *,
    protocol: ProtocolService,
    patch_payload: dict[str, Any],
    fallback_mutate: Callable[[], None],
) -> None:
    try:
        protocol.call("config.patch", patch_payload)
        return
    except Exception:
        pass
    fallback_mutate()


def _fetch_plugins_list(protocol: ProtocolService) -> list[Any]:
    payload = protocol.call("plugins.list", {})
    rows = payload.get("plugins") if isinstance(payload, dict) else []
    return rows if isinstance(rows, list) else []


def _format_checks_table(console: Console, report: dict[str, Any]) -> None:
    checks = report.get("checks", {}) if isinstance(report, dict) else {}
    paths = report.get("paths", {}) if isinstance(report, dict) else {}
    bins = report.get("bins", {}) if isinstance(report, dict) else {}
    table = Table(title="Plugin Host Requirements")
    table.add_column("Check", style="cyan")
    table.add_column("Value")
    table.add_column("Status")
    rows = [
        ("node", bins.get("node", ""), bool(checks.get("nodeAvailable"))),
        ("pnpm", bins.get("pnpm", ""), bool(checks.get("pnpmAvailable"))),
        ("npm", bins.get("npm", ""), bool(checks.get("npmAvailable"))),
        ("openclaw dir", paths.get("openclawDir", ""), bool(checks.get("openclawDirExists"))),
        ("openclaw package.json", paths.get("openclawPackageJson", ""), bool(checks.get("openclawPackageJsonExists"))),
        ("openclaw dist loader", paths.get("openclawDistLoader", ""), bool(checks.get("openclawDistLoaderExists"))),
        ("host script", paths.get("hostScript", ""), bool(checks.get("hostScriptExists"))),
    ]
    for name, value, ok in rows:
        table.add_row(name, str(value), "ok" if ok else "missing")
    console.print(table)
    suggestions = report.get("suggestions", [])
    if isinstance(suggestions, list) and suggestions:
        console.print("[yellow]Suggestions:[/yellow]")
        for text in suggestions:
            console.print(f"- {text}")


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
    for kind in ("rpc", "http", "cli"):
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
    last_24h = runtime.get("last24h", {}) if isinstance(runtime, dict) else {}
    if isinstance(last_24h, dict):
        window_table = Table(title="Native Last 24h")
        window_table.add_column("Metric", style="cyan")
        window_table.add_column("Value")
        window_table.add_row("error count", str(last_24h.get("errorCount", 0)))
        window_table.add_row("circuit open hits", str(last_24h.get("circuitOpenHits", 0)))
        window_table.add_row("circuit hit rate", str(last_24h.get("circuitHitRate", 0.0)))
        console.print(window_table)
        by_code = last_24h.get("errorsByCode", {})
        if isinstance(by_code, dict) and by_code:
            by_code_table = Table(title="Native Last 24h Errors By Code")
            by_code_table.add_column("Code", style="cyan")
            by_code_table.add_column("Count")
            for code, count in sorted(by_code.items(), key=lambda item: str(item[0])):
                by_code_table.add_row(str(code), str(count))
            console.print(by_code_table)


def _run_setup_host(
    console: Console,
    *,
    dry_run: bool,
    install_deps: bool,
    build_dist: bool,
) -> bool:
    manager = get_plugin_manager()
    try:
        result = manager.client.setup_host(
            install_deps=install_deps,
            build_dist=build_dist,
            dry_run=dry_run,
        )
    except PluginHostError as exc:
        console.print(f"[red]{exc.code}:[/red] {exc.message}")
        if exc.data:
            _format_checks_table(console, exc.data.get("report", exc.data))
        return False

    console.print(f"[green]✓[/green] Host setup {'planned' if dry_run else 'completed'}")
    if dry_run:
        for cmd in result.get("planned", []):
            console.print(f"- {' '.join(str(x) for x in cmd)}")
        return True
    for row in result.get("executed", []):
        command = " ".join(str(x) for x in row.get("command", []))
        status = "ok" if row.get("ok") else "failed"
        console.print(f"- [{status}] {command}")
    return True


def register_plugins_commands(app: typer.Typer, console: Console) -> None:
    """Register real plugins command group."""
    protocol = ProtocolService()
    plugins_app = typer.Typer(help="Manage OpenClaw-compatible plugins")
    app.add_typer(plugins_app, name="plugins")

    @plugins_app.command("list")
    def plugins_list(
        keyword: str = typer.Option("", "--keyword", "-k", help="Filter by id/name/source"),
    ) -> None:
        try:
            rows = _fetch_plugins_list(protocol)
        except Exception:
            snapshot = _load_snapshot(force_reload=False)
            if snapshot is None:
                console.print("[yellow]Plugin host unavailable. Run `plugins doctor` for diagnostics.[/yellow]")
                raise typer.Exit(1)
            rows = snapshot.plugins
        rows = filter_plugin_rows(rows, keyword)
        table = Table(title=f"Plugins ({len(rows)})")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Origin")
        table.add_column("Source")
        for row in rows:
            table.add_row(*plugin_table_row(row))
        console.print(table)

    @plugins_app.command("search")
    def plugins_search(keyword: str = typer.Argument(..., help="Search keyword")) -> None:
        plugins_list(keyword=keyword)

    @plugins_app.command("native-list")
    def plugins_native_list(
        keyword: str = typer.Option("", "--keyword", "-k", help="Filter by id/name/source"),
    ) -> None:
        try:
            rows = [row for row in _fetch_plugins_list(protocol) if is_native_plugin_row(row)]
        except Exception:
            snapshot = _load_snapshot(force_reload=False)
            if snapshot is None:
                console.print("[red]Plugin host/native runtime unavailable[/red]")
                raise typer.Exit(1)
            rows = [row for row in snapshot.plugins if row.origin == "native"]
        rows = filter_plugin_rows(rows, keyword)
        table = Table(title=f"Native Plugins ({len(rows)})")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Runtime")
        table.add_column("Caps")
        table.add_column("Methods")
        table.add_column("Hooks")
        table.add_column("Source")
        for row in rows:
            table.add_row(*native_plugin_table_row(row))
        console.print(table)
        if not rows:
            console.print("No native plugins discovered. Add paths to `plugins.load.paths`.")

    @plugins_app.command("cli-list")
    def plugins_cli_list() -> None:
        try:
            payload = protocol.call("plugins.cli.list", {})
            commands = payload.get("commands") if isinstance(payload, dict) else []
        except Exception:
            manager = get_plugin_manager()
            _load_snapshot(force_reload=False)
            commands = manager.cli_commands()
        if not isinstance(commands, list):
            commands = []
        table = Table(title=f"Plugin CLI Commands ({len(commands)})")
        table.add_column("Command", style="cyan")
        for command in commands:
            table.add_row(command)
        console.print(table)

    @plugins_app.command("cli-run")
    def plugins_cli_run(
        command: str = typer.Argument(..., help="Native plugin CLI command name"),
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
        try:
            result = protocol.call("plugins.cli.invoke", {"command": command, "payload": payload_obj})
            result = result if isinstance(result, dict) else {"ok": False, "error": {"code": "ERROR", "message": "invoke failed"}}
        except Exception:
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

    @plugins_app.command("install")
    def plugins_install(
        source: str = typer.Argument(..., help="Path to plugin package (directory or .zip)"),
        target_dir: str = typer.Option("", "--target-dir", help="Install into this directory (default: workspace/.joyhouse/plugins)"),
        apply_config: bool = typer.Option(True, "--apply-config/--no-apply-config", help="Add path and allow to config and save"),
    ) -> None:
        cfg = load_config()
        workspace_attr = cfg.workspace_path
        workspace = Path(workspace_attr()) if callable(workspace_attr) else Path(workspace_attr)
        target = Path(target_dir).expanduser().resolve() if target_dir.strip() else (workspace / ".joyhouse" / "plugins")
        result = install_package(Path(source).expanduser().resolve(), target, cfg)
        if not result.get("ok"):
            console.print(f"[red]{result.get('error', 'Install failed')}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]Installed[/green] {result.get('plugin_id')} -> {result.get('install_path')}")
        updates = result.get("config_updates")
        if apply_config and updates:
            paths = list(cfg.plugins.load.paths or [])
            add_path = updates.get("add_path")
            if add_path and add_path not in paths:
                paths.append(add_path)
                cfg.plugins.load.paths = paths
            allow = list(cfg.plugins.allow or [])
            add_allow = updates.get("add_allow")
            if add_allow and add_allow not in allow:
                allow.append(add_allow)
                cfg.plugins.allow = allow
            save_config(cfg)
            console.print("[green]Config updated[/green]: load.paths and allow. Reload plugins to use (e.g. restart server).")

    @plugins_app.command("info")
    def plugins_info(plugin_id: str = typer.Argument(..., help="Plugin id")) -> None:
        row: Any | None = None
        try:
            row = protocol.call("plugins.info", {"id": plugin_id})
        except Exception:
            snapshot = _load_snapshot(force_reload=False)
            if snapshot is None:
                console.print("[red]Plugin host unavailable[/red]")
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
        cfg = load_config()
        workspace_attr = cfg.workspace_path
        workspace = workspace_attr() if callable(workspace_attr) else workspace_attr
        config_payload = cfg.model_dump(by_alias=True) if hasattr(cfg, "model_dump") else {}
        manager = get_plugin_manager()
        report, native_report = collect_plugins_doctor_reports(
            manager=manager,
            workspace_dir=str(workspace),
            config_payload=config_payload if isinstance(config_payload, dict) else {},
        )
        _format_checks_table(console, report)
        _format_native_checks_table(console, native_report)
        _format_native_runtime_table(console, native_report)
        if should_run_setup_host(report):
            console.print(
                "[yellow]Detected missing OpenClaw dist loader. "
                "Plugin runtime cannot load until build completes.[/yellow]"
            )
            if typer.confirm("Run setup-host now to install/build OpenClaw?", default=True):
                ok = _run_setup_host(
                    console,
                    dry_run=False,
                    install_deps=True,
                    build_dist=True,
                )
                if not ok:
                    raise typer.Exit(1)
                report = manager.client.requirements_report()
                _format_checks_table(console, report)
        snapshot = _load_snapshot(force_reload=True)
        if snapshot is None:
            console.print("[red]Plugin host failed to load.[/red]")
            console.print("Try: [cyan]joyhousebot plugins setup-host[/cyan]")
            raise typer.Exit(1)
        if not snapshot_has_plugin_issues(snapshot):
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

    @plugins_app.command("setup-host")
    def plugins_setup_host(
        dry_run: bool = typer.Option(False, "--dry-run", help="Show commands only"),
        install_deps: bool = typer.Option(True, "--install/--no-install", help="Install openclaw dependencies"),
        build_dist: bool = typer.Option(True, "--build/--no-build", help="Build openclaw dist"),
    ) -> None:
        try:
            payload = protocol.call(
                "plugins.setup_host",
                {
                    "dryRun": dry_run,
                    "installDeps": install_deps,
                    "buildDist": build_dist,
                },
            )
            console.print(f"[green]✓[/green] Host setup {'planned' if dry_run else 'completed'}")
            if dry_run:
                for cmd in payload.get("planned", []) if isinstance(payload, dict) else []:
                    console.print(f"- {' '.join(str(x) for x in cmd)}")
                return
            for row in payload.get("executed", []) if isinstance(payload, dict) else []:
                command = " ".join(str(x) for x in row.get("command", []))
                status = "ok" if row.get("ok") else "failed"
                console.print(f"- [{status}] {command}")
        except Exception:
            ok = _run_setup_host(
                console,
                dry_run=dry_run,
                install_deps=install_deps,
                build_dist=build_dist,
            )
            if not ok:
                raise typer.Exit(1)
        if dry_run:
            return
        snapshot = _load_snapshot(force_reload=True)
        if snapshot:
            console.print(f"[green]✓[/green] Plugins now discoverable: {len(snapshot.plugins)}")
        else:
            console.print("[yellow]Setup succeeded but plugin load still failed; run plugins doctor[/yellow]")

    @plugins_app.command("reload")
    def plugins_reload() -> None:
        try:
            payload = protocol.call("plugins.reload", {})
            count = int(payload.get("plugins", 0)) if isinstance(payload, dict) else 0
            console.print(f"[green]✓[/green] Reloaded {count} plugins")
            return
        except Exception:
            snapshot = _load_snapshot(force_reload=True)
            if snapshot is None:
                console.print("[red]Plugin reload failed[/red]")
                raise typer.Exit(1)
            console.print(f"[green]✓[/green] Reloaded {len(snapshot.plugins)} plugins")

    @plugins_app.command("status")
    def plugins_status(
        as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
        compact: bool = typer.Option(
            False,
            "--compact",
            help="Output compact JSON SLI fields (implies --json)",
        ),
    ) -> None:
        try:
            report = protocol.call("plugins.status", {})
        except Exception:
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
        methods: list[Any] = []
        try:
            payload = protocol.call("plugins.gateway.methods", {})
            methods = payload.get("methods") if isinstance(payload, dict) else []
        except Exception:
            snapshot = _load_snapshot(force_reload=False)
            if snapshot is None:
                console.print("[red]Plugin host unavailable[/red]")
                raise typer.Exit(1)
            methods = snapshot.gateway_methods
        for method in methods if isinstance(methods, list) else []:
            console.print(method)

    @plugins_app.command("services-start")
    def plugins_services_start() -> None:
        try:
            payload = protocol.call("plugins.services.start", {})
            rows = payload.get("rows") if isinstance(payload, dict) else []
        except Exception:
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
        try:
            payload = protocol.call("plugins.services.stop", {})
            rows = payload.get("rows") if isinstance(payload, dict) else []
        except Exception:
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
        def _fallback() -> None:
            cfg = load_config()
            entry = cfg.plugins.entries.get(plugin_id)
            if entry is None:
                from joyhousebot.config.schema import PluginEntryConfig

                cfg.plugins.entries[plugin_id] = PluginEntryConfig(enabled=True)
            else:
                entry.enabled = True
            save_config(cfg)

        _config_patch_or_fallback(
            protocol=protocol,
            patch_payload={"plugins": {"entries": {plugin_id: {"enabled": True}}}},
            fallback_mutate=_fallback,
        )
        _load_snapshot(force_reload=True)
        console.print(f"[green]✓[/green] Enabled plugin: {plugin_id}")

    @plugins_app.command("disable")
    def plugins_disable(plugin_id: str = typer.Argument(..., help="Plugin id")) -> None:
        def _fallback() -> None:
            cfg = load_config()
            entry = cfg.plugins.entries.get(plugin_id)
            if entry is None:
                from joyhousebot.config.schema import PluginEntryConfig

                cfg.plugins.entries[plugin_id] = PluginEntryConfig(enabled=False)
            else:
                entry.enabled = False
            save_config(cfg)

        _config_patch_or_fallback(
            protocol=protocol,
            patch_payload={"plugins": {"entries": {plugin_id: {"enabled": False}}}},
            fallback_mutate=_fallback,
        )
        _load_snapshot(force_reload=True)
        console.print(f"[green]✓[/green] Disabled plugin: {plugin_id}")

    @plugins_app.command("install")
    def plugins_install(
        source: str = typer.Argument(..., help="Plugin path or npm spec"),
    ) -> None:
        source_str = source.strip()
        source_path = Path(source_str).expanduser()
        if source_path.exists():
            path_value = str(source_path.resolve())
            plugin_id = source_path.name
            merged_paths = [path_value]
            try:
                cfg_snapshot = protocol.call("config.get", {})
                parsed = cfg_snapshot.get("parsed") if isinstance(cfg_snapshot, dict) else {}
                plugins = parsed.get("plugins") if isinstance(parsed, dict) else {}
                load_payload = plugins.get("load") if isinstance(plugins, dict) else {}
                current_paths = load_payload.get("paths") if isinstance(load_payload, dict) else []
                if isinstance(current_paths, list):
                    merged_paths = [str(x) for x in current_paths]
                if path_value not in merged_paths:
                    merged_paths.append(path_value)
            except Exception:
                pass

            def _fallback() -> None:
                cfg = load_config()
                if path_value not in cfg.plugins.load.paths:
                    cfg.plugins.load.paths.append(path_value)
                from joyhousebot.config.schema import PluginEntryConfig

                cfg.plugins.entries[plugin_id] = PluginEntryConfig(enabled=True)
                save_config(cfg)

            _config_patch_or_fallback(
                protocol=protocol,
                patch_payload={
                    "plugins": {
                        "load": {"paths": merged_paths},
                        "entries": {plugin_id: {"enabled": True}},
                    }
                },
                fallback_mutate=_fallback,
            )
            _load_snapshot(force_reload=True)
            console.print(f"[green]✓[/green] Linked plugin path: {path_value}")
            return

        # npm spec tracking (actual npm install is delegated to OpenClaw host environment)
        normalized_id = source_str.split("@")[0].split("/")[-1] if "/" in source_str else source_str

        def _fallback() -> None:
            cfg = load_config()
            from joyhousebot.config.schema import PluginEntryConfig, PluginInstallRecord

            cfg.plugins.entries[normalized_id] = PluginEntryConfig(enabled=True)
            cfg.plugins.installs[normalized_id] = PluginInstallRecord(source="npm", spec=source_str)
            save_config(cfg)

        _config_patch_or_fallback(
            protocol=protocol,
            patch_payload={
                "plugins": {
                    "entries": {normalized_id: {"enabled": True}},
                    "installs": {normalized_id: {"source": "npm", "spec": source_str}},
                }
            },
            fallback_mutate=_fallback,
        )
        _load_snapshot(force_reload=True)
        console.print(
            f"[yellow]Recorded npm plugin spec (install on host env if needed):[/yellow] {source_str}"
        )

    @plugins_app.command("update")
    def plugins_update(
        plugin_id: str = typer.Argument("", help="Plugin id (omit for all tracked)"),
    ) -> None:
        tracked: dict[str, Any] = {}
        try:
            cfg_snapshot = protocol.call("config.get", {})
            parsed = cfg_snapshot.get("parsed") if isinstance(cfg_snapshot, dict) else {}
            plugins = parsed.get("plugins") if isinstance(parsed, dict) else {}
            installs = plugins.get("installs") if isinstance(plugins, dict) else {}
            tracked = installs if isinstance(installs, dict) else {}
        except Exception:
            cfg = load_config()
            tracked = cfg.plugins.installs
        if plugin_id:
            if plugin_id not in tracked:
                console.print(f"[red]Plugin not tracked in installs:[/red] {plugin_id}")
                raise typer.Exit(1)
            value = tracked[plugin_id]
            if hasattr(value, "spec"):
                spec = value.spec
            elif isinstance(value, dict):
                spec = value.get("spec")
            else:
                spec = None
            console.print(f"Update requested for {plugin_id} (spec={spec})")
        else:
            if not tracked:
                console.print("No tracked plugin installs.")
                return
            for pid, meta in tracked.items():
                if hasattr(meta, "spec"):
                    text = meta.spec or meta.source
                elif isinstance(meta, dict):
                    text = meta.get("spec") or meta.get("source")
                else:
                    text = str(meta)
                console.print(f"- {pid}: {text}")
        _load_snapshot(force_reload=True)

