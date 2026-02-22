"""Channel command group extracted from legacy commands.py."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from joyhousebot import __logo__


def _get_bridge_dir(console: Console) -> Path:
    """Get the bridge directory, setting it up if needed."""
    user_bridge = Path.home() / ".joyhousebot" / "bridge"
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    pkg_bridge = Path(__file__).parent.parent.parent / "bridge"
    src_bridge = Path(__file__).parent.parent.parent.parent / "bridge"

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall joyhousebot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)
        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


def register_channels_commands(app: typer.Typer, console: Console) -> None:
    """Register channels command group."""
    channels_app = typer.Typer(help="Manage channels")
    app.add_typer(channels_app, name="channels")

    @channels_app.command("status")
    def channels_status() -> None:
        """Show channel status."""
        from joyhousebot.config.loader import load_config

        config = load_config()
        table = Table(title="Channel Status")
        table.add_column("Channel", style="cyan")
        table.add_column("Enabled", style="green")
        table.add_column("Configuration", style="yellow")

        wa = config.channels.whatsapp
        table.add_row("WhatsApp", "✓" if wa.enabled else "✗", wa.bridge_url)

        dc = config.channels.discord
        table.add_row("Discord", "✓" if dc.enabled else "✗", dc.gateway_url)

        fs = config.channels.feishu
        fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
        table.add_row("Feishu", "✓" if fs.enabled else "✗", fs_config)

        mc = config.channels.mochat
        mc_base = mc.base_url or "[dim]not configured[/dim]"
        table.add_row("Mochat", "✓" if mc.enabled else "✗", mc_base)

        tg = config.channels.telegram
        tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
        table.add_row("Telegram", "✓" if tg.enabled else "✗", tg_config)

        slack = config.channels.slack
        slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
        table.add_row("Slack", "✓" if slack.enabled else "✗", slack_config)
        console.print(table)

    @channels_app.command("login")
    def channels_login() -> None:
        """Link device via QR code."""
        from joyhousebot.config.loader import load_config

        config = load_config()
        bridge_dir = _get_bridge_dir(console)

        console.print(f"{__logo__} Starting bridge...")
        console.print("Scan the QR code to connect.\n")

        env = {**os.environ}
        if config.channels.whatsapp.bridge_token:
            env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

        try:
            subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Bridge failed: {e}[/red]")
        except FileNotFoundError:
            console.print("[red]npm not found. Please install Node.js.[/red]")

