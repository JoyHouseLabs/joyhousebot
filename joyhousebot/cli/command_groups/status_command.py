"""Status command extracted from legacy commands.py."""

from __future__ import annotations

from rich.console import Console

from joyhousebot import __logo__


def status_command(console: Console) -> None:
    """Show joyhousebot status."""
    from joyhousebot.config.loader import load_config, get_config_path
    from joyhousebot.providers.registry import PROVIDERS

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} joyhousebot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        console.print(f"Model: {config.agents.defaults.model}")
        resolved = config.get_provider_name()
        if config.agents.defaults.provider:
            console.print(f"Provider: {resolved or '(none)'} [dim](set: {config.agents.defaults.provider})[/dim]")
        elif resolved:
            console.print(f"Provider: {resolved} [dim](auto)[/dim]")
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")

