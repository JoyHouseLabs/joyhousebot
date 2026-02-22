"""health/dashboard/doctor/logs/daemon/reset command groups."""

from __future__ import annotations

import json
import shutil
import sys
import webbrowser
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot.config.loader import get_config_path, get_data_dir, load_config, save_config
from joyhousebot.utils.helpers import get_workspace_path

from joyhousebot.cli.shared.http_utils import get_gateway_base_url, http_json


def register_runtime_commands(
    app: typer.Typer,
    console: Console,
    gateway_command: Callable[..., None],
) -> None:
    """Register health/dashboard/doctor/logs/daemon/reset command groups."""
    health_app = typer.Typer(help="Fetch health from running gateway")
    app.add_typer(health_app, name="health")

    @health_app.callback(invoke_without_command=True)
    def health_callback(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            health_check()

    @health_app.command("check")
    def health_check(
        timeout: float = typer.Option(3.0, "--timeout", help="HTTP timeout in seconds"),
    ) -> None:
        base = get_gateway_base_url()
        payload = http_json("GET", f"{base}/health", timeout=timeout)
        console.print(json.dumps(payload, indent=2, ensure_ascii=False))

    @app.command("dashboard")
    def dashboard_open() -> None:
        """Open the local control UI in system browser."""
        base = get_gateway_base_url()
        url = f"{base}/"
        opened = webbrowser.open(url)
        if opened:
            console.print(f"[green]✓[/green] Opened {url}")
        else:
            console.print(f"[yellow]Could not open browser automatically.[/yellow] URL: {url}")

    doctor_app = typer.Typer(help="Health checks + quick fixes")
    app.add_typer(doctor_app, name="doctor")

    @doctor_app.callback(invoke_without_command=True)
    def doctor_callback(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            doctor_check()

    @doctor_app.command("check")
    def doctor_check() -> None:
        """Run local diagnostics and optional gateway checks."""
        cfg_path = get_config_path()
        workspace = get_workspace_path(load_config().agents.defaults.workspace)
        data_dir = get_data_dir()
        table = Table(title="Doctor Report")
        table.add_column("Check", style="cyan")
        table.add_column("Status")
        table.add_column("Detail")
        table.add_row("config file", "ok" if cfg_path.exists() else "warn", str(cfg_path))
        table.add_row("workspace", "ok" if workspace.exists() else "warn", str(workspace))
        table.add_row("data dir", "ok" if data_dir.exists() else "warn", str(data_dir))
        table.add_row("python", "ok", sys.version.split()[0])
        table.add_row("node", "ok" if shutil.which("node") else "warn", "found" if shutil.which("node") else "not found")
        try:
            base = get_gateway_base_url()
            health = http_json("GET", f"{base}/health", timeout=2.0)
            table.add_row("gateway", "ok", str(health.get("service", "running")))
        except Exception as exc:
            table.add_row("gateway", "warn", f"not reachable ({exc})")
        console.print(table)

    logs_app = typer.Typer(help="Gateway logs")
    app.add_typer(logs_app, name="logs")

    @logs_app.command("tail")
    def logs_tail(
        file: str = typer.Option("", "--file", help="Custom log file path"),
        lines: int = typer.Option(80, "--lines", "-n", help="Tail lines"),
    ) -> None:
        """Tail a local log file when available."""
        candidates = []
        if file:
            candidates.append(Path(file).expanduser())
        data_dir = get_data_dir()
        candidates.extend(
            [
                data_dir / "logs" / "gateway.log",
                data_dir / "logs" / "agent.log",
                Path.home() / ".joyhousebot" / "gateway.log",
                Path.home() / ".joyhousebot" / "agent.log",
            ]
        )
        target = next((p for p in candidates if p.exists() and p.is_file()), None)
        if target is None:
            console.print("[yellow]No gateway log file found.[/yellow] Gateway logs may be in foreground terminal output.")
            return
        content = target.read_text(encoding="utf-8", errors="replace").splitlines()
        for row in content[-max(1, lines):]:
            console.print(row)

    daemon_app = typer.Typer(help="Gateway service alias")
    app.add_typer(daemon_app, name="daemon")

    @daemon_app.command("start")
    def daemon_start(
        host: str = typer.Option("127.0.0.1", "--host", "-h"),
        port: int = typer.Option(18790, "--port", "-p"),
        verbose: bool = typer.Option(False, "--verbose", "-v"),
        wallet_unlock: bool = typer.Option(False, "--wallet-unlock"),
    ) -> None:
        """Alias to gateway command."""
        gateway_command(host=host, port=port, verbose=verbose, wallet_unlock=wallet_unlock)

    reset_app = typer.Typer(help="Reset local config/state")
    app.add_typer(reset_app, name="reset")

    @reset_app.command("all")
    def reset_all(
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ) -> None:
        """Reset config/state while keeping CLI installed."""
        cfg_path = get_config_path()
        data_dir = get_data_dir()
        sessions_dir = data_dir / "sessions"
        cron_file = data_dir / "cron" / "jobs.json"
        targets = [cfg_path, sessions_dir, cron_file]
        if not yes:
            names = "\n".join(f"- {p}" for p in targets)
            confirmed = typer.confirm(f"Will remove:\n{names}\nContinue?")
            if not confirmed:
                raise typer.Exit(0)
        if cfg_path.exists():
            cfg_path.unlink()
        if sessions_dir.exists() and sessions_dir.is_dir():
            shutil.rmtree(sessions_dir)
        if cron_file.exists():
            cron_file.unlink()
        save_config(load_config())
        console.print("[green]✓[/green] Reset completed.")

